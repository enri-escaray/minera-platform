"""Tests del servicio: idempotencia, cadena de custodia, RBAC, errores de dominio."""
from datetime import datetime, timedelta, timezone
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


def test_listar_lotes_para_panel(client, token_operador):
    crear_lote(client, token_operador, codigo="LT-PANEL-1")
    crear_lote(client, token_operador, codigo="LT-PANEL-2")
    r = client.get("/lotes")  # lectura pública: la consume el panel de sitio
    assert r.status_code == 200
    codigos = [l["codigo"] for l in r.json()]
    assert "LT-PANEL-1" in codigos and "LT-PANEL-2" in codigos


def test_listar_pesajes_por_rango(client, token_operador):
    lote = crear_lote(client, token_operador, codigo="LT-RANGO")
    for hora, masa in (("08", 100.0), ("12", 200.0), ("20", 300.0)):
        client.post("/pesajes", headers=auth(token_operador), json={
            "event_id": str(uuid4()), "lote_id": lote["id"], "instrumento_id": "b-1",
            "masa_kg": masa, "medido_en": f"2026-07-18T{hora}:00:00Z"})
    r = client.get("/pesajes", params={"desde": "2026-07-18T07:00:00Z",
                                       "hasta": "2026-07-18T19:00:00Z"})
    assert [p["masa_kg"] for p in r.json()] == [100.0, 200.0]  # el de las 20:00 queda fuera


# --------------------------------------------------- límites de entrada (DoS)

def test_masa_infinita_rechazada(client, token_operador):
    """`float` acepta Infinity y el parser de Python lo admite como literal
    (JSON estándar no, pero json.loads sí). Sin cota superior, un `Infinity`
    entraba a la cadena de custodia y envenenaba todo total de masa aguas abajo.
    Se manda el cuerpo crudo justamente porque un cliente honesto no puede
    serializarlo — el atacante no usa un cliente honesto."""
    lote = crear_lote(client, token_operador, codigo="LT-INF")
    cuerpo = (
        '{"event_id": "%s", "lote_id": "%s", "instrumento_id": "b-1", '
        '"masa_kg": Infinity, "medido_en": "%s"}'
        % (uuid4(), lote["id"], datetime.now(timezone.utc).isoformat())
    )
    r = client.post("/pesajes", headers={**auth(token_operador),
                                         "Content-Type": "application/json"},
                    content=cuerpo)
    assert r.status_code == 422


def test_lotes_origen_acotado(client, token_operador):
    """El grafo de custodia se recorre recursivamente: un fan-out sin techo
    convierte un GET de custodia en una amplificación contra la base."""
    r = client.post("/lotes", headers=auth(token_operador), json={
        "codigo": "LT-FANOUT", "tipo": "stock",
        "lotes_origen": [str(uuid4()) for _ in range(101)]})
    assert r.status_code == 422


def test_import_de_bloques_acotado(client):
    from minera_comun import emitir_token_dev
    bloque = {"id": "B", "centroide_x": 1.0, "centroide_y": 2.0, "cota": 3000.0,
              "leyes_estimadas": {"cu_pct": 0.8}}
    r = client.post("/bloques/importar",
                    headers=auth(emitir_token_dev("geo", ["geologia.importar"])),
                    json={"origen_modelo": "m", "bloques": [bloque] * 50_001})
    assert r.status_code == 422


def test_rango_de_pesajes_invertido_es_422(client):
    ahora = datetime.now(timezone.utc)
    r = client.get("/pesajes", params={"desde": ahora.isoformat(),
                                       "hasta": (ahora - timedelta(days=1)).isoformat()})
    assert r.status_code == 422


def test_rango_de_pesajes_excesivo_es_422(client):
    ahora = datetime.now(timezone.utc)
    r = client.get("/pesajes", params={"desde": (ahora - timedelta(days=400)).isoformat(),
                                       "hasta": ahora.isoformat()})
    assert r.status_code == 422


def test_custodia_acota_el_trabajo_por_consulta(client, token_operador):
    """Con mezclas en rombo el mismo subárbol se reexplora por cada camino.
    El presupuesto trunca la respuesta antes de que la consulta voltee el servicio."""
    from app.main import MAX_NODOS_CUSTODIA

    base = [crear_lote(client, token_operador, codigo=f"LT-B{i}")["id"] for i in range(6)]
    nivel = base
    for capa in range(4):
        nivel = [crear_lote(client, token_operador, codigo=f"LT-C{capa}-{i}",
                            lotes_origen=nivel)["id"] for i in range(6)]
    cima = crear_lote(client, token_operador, codigo="LT-CIMA", lotes_origen=nivel)

    r = client.get(f"/lotes/{cima['id']}/custodia")
    assert r.status_code == 200

    def contar(nodo):
        return 1 + sum(contar(o) for o in nodo["origenes"])

    assert contar(r.json()) <= MAX_NODOS_CUSTODIA + 1
