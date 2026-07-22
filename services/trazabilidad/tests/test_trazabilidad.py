"""Tests del servicio: idempotencia, cadena de custodia, RBAC, errores de dominio."""
from datetime import datetime, timezone
from uuid import uuid4




def auth(token):
    return {"Authorization": f"Bearer {token}"}


def crear_lote(client, token, codigo="LT-TEST-001", **extra):
    r = client.post("/lotes", json={"codigo": codigo, "tipo": "rom", **extra}, headers=auth(token))
    assert r.status_code == 201, r.text
    return r.json()


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_crear_lote_y_consultar_custodia(client, token_operador):
    lote = crear_lote(client, token_operador)
    r = client.get(f"/lotes/{lote['id']}/custodia")
    assert r.status_code == 200
    assert r.json()["masa_total_kg"] == 0


def test_codigo_duplicado_rechazado(client, token_operador):
    crear_lote(client, token_operador, codigo="LT-DUP")
    r = client.post("/lotes", json={"codigo": "LT-DUP", "tipo": "rom"}, headers=auth(token_operador))
    assert r.status_code == 409


def test_pesaje_idempotente_no_duplica_masa(client, token_operador):
    lote = crear_lote(client, token_operador, codigo="LT-PESO")
    pesaje = {
        "event_id": str(uuid4()),
        "lote_id": lote["id"],
        "instrumento_id": "bascula-01",
        "masa_kg": 42000.0,
        "medido_en": datetime.now(timezone.utc).isoformat(),
    }
    assert client.post("/pesajes", json=pesaje, headers=auth(token_operador)).status_code == 201
    # Reintento de sincronización con el MISMO event_id (modo isla → reconexión)
    assert client.post("/pesajes", json=pesaje, headers=auth(token_operador)).status_code == 201
    custodia = client.get(f"/lotes/{lote['id']}/custodia").json()
    assert custodia["masa_total_kg"] == 42000.0  # una sola vez, no 84000


def test_pesaje_sobre_lote_inexistente(client, token_operador):
    r = client.post("/pesajes", headers=auth(token_operador), json={
        "event_id": str(uuid4()),
        "lote_id": str(uuid4()),
        "instrumento_id": "bascula-01",
        "masa_kg": 100.0,
        "medido_en": datetime.now(timezone.utc).isoformat(),
    })
    assert r.status_code == 404


def test_grafo_de_custodia_con_mezcla(client, token_operador):
    a = crear_lote(client, token_operador, codigo="LT-A")
    b = crear_lote(client, token_operador, codigo="LT-B")
    for lote, masa in ((a, 10000.0), (b, 5000.0)):
        client.post("/pesajes", headers=auth(token_operador), json={
            "event_id": str(uuid4()), "lote_id": lote["id"],
            "instrumento_id": "pesometro-c1", "masa_kg": masa,
            "medido_en": datetime.now(timezone.utc).isoformat(),
        })
    mezcla = crear_lote(client, token_operador, codigo="LT-STOCK-1",
                        tipo="stock", lotes_origen=[a["id"], b["id"]])
    custodia = client.get(f"/lotes/{mezcla['id']}/custodia").json()
    assert len(custodia["origenes"]) == 2
    masas = sorted(o["masa_total_kg"] for o in custodia["origenes"])
    assert masas == [5000.0, 10000.0]


def test_escritura_sin_token_es_401(client):
    r = client.post("/lotes", json={"codigo": "LT-X", "tipo": "rom"})
    assert r.status_code == 401


def test_escritura_con_rol_incorrecto_es_403(client):
    from minera_comun import emitir_token_dev
    token = emitir_token_dev("visitante", ["reportes.leer"])
    r = client.post("/lotes", json={"codigo": "LT-X", "tipo": "rom"}, headers=auth(token))
    assert r.status_code == 403


def test_importar_bloques_upsert(client):
    from minera_comun import emitir_token_dev
    geo = auth(emitir_token_dev("geologa-1", ["geologia.importar"]))
    bloques = [{"id": "B-001", "centroide_x": 1.0, "centroide_y": 2.0, "cota": 3050,
                "leyes_estimadas": {"cu_pct": 0.85}}]
    r = client.post("/bloques/importar", headers=geo,
                    json={"origen_modelo": "vulcan-2026-07", "bloques": bloques})
    assert r.status_code == 201 and r.json()["importados"] == 1
    # Reimport del mismo bloque con ley corregida: upsert, no error
    bloques[0]["leyes_estimadas"] = {"cu_pct": 0.91}
    r = client.post("/bloques/importar", headers=geo,
                    json={"origen_modelo": "vulcan-2026-07b", "bloques": bloques})
    assert r.status_code == 201
    # Sin rol de geología: 403
    r = client.post("/bloques/importar", headers=auth(emitir_token_dev("x", ["trazabilidad.escribir"])),
                    json={"origen_modelo": "m", "bloques": bloques})
    assert r.status_code == 403


def test_listar_pesajes_por_rango(client, token_operador):
    lote = crear_lote(client, token_operador, codigo="LT-RANGO")
    for hora, masa in (("08", 100.0), ("12", 200.0), ("20", 300.0)):
        client.post("/pesajes", headers=auth(token_operador), json={
            "event_id": str(uuid4()), "lote_id": lote["id"], "instrumento_id": "b-1",
            "masa_kg": masa, "medido_en": f"2026-07-18T{hora}:00:00Z"})
    r = client.get("/pesajes", params={"desde": "2026-07-18T07:00:00Z",
                                       "hasta": "2026-07-18T19:00:00Z"})
    assert [p["masa_kg"] for p in r.json()] == [100.0, 200.0]  # el de las 20:00 queda fuera
