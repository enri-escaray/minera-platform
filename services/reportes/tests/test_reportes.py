"""Tests P2 y P4: reporte de turno, novedades idempotentes, conciliación."""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from minera_comun import emitir_token_dev

from app.models import BalanceLote
from app.nucleo import conciliar, generar_reporte, rango_turno


@pytest.fixture(autouse=True)
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTBOX_DIR", str(tmp_path / "outbox"))
    monkeypatch.setenv("AUDIT_DIR", str(tmp_path / "auditoria"))
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("AUTH_SECRET", "secreto-de-test-no-usar-en-produccion")
    monkeypatch.setenv("SCHEDULER", "0")  # sin bucle en tests


class FakeFuente:
    def __init__(self, pesajes):
        self._pesajes = pesajes

    async def pesajes(self, desde, hasta):
        return self._pesajes


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        c.app = app
        yield c


def auth(*roles):
    return {"Authorization": f"Bearer {emitir_token_dev('operador-2', list(roles))}"}


def test_rango_turno_noche_cruza_medianoche():
    desde, hasta = rango_turno("2026-07-18", "noche")
    assert desde.hour == 19 and hasta.day == 19 and hasta.hour == 7


def test_generar_reporte_agrega_por_instrumento():
    pesajes = [
        {"masa_kg": 220000, "instrumento_id": "bascula-01"},
        {"masa_kg": 180000, "instrumento_id": "bascula-01"},
        {"masa_kg": 50000, "instrumento_id": "pesometro-c1"},
    ]
    reporte = generar_reporte("2026-07-18", "dia", pesajes, [])
    assert reporte.total_toneladas == 450.0
    assert reporte.toneladas_por_instrumento == {"bascula-01": 400.0, "pesometro-c1": 50.0}
    assert reporte.cantidad_pesajes == 3


def test_reporte_por_api_incluye_novedades(client):
    from app.main import app

    app.state.fuente = FakeFuente([{"masa_kg": 100000, "instrumento_id": "b-1"}])
    novedad = {"event_id": str(uuid4()), "turno": "dia", "fecha": "2026-07-18",
               "categoria": "seguridad", "texto": "Berma del sector N reparada"}
    assert client.post("/novedades", json=novedad, headers=auth("campo.reportar")).status_code == 201
    # Reenvío offline con el mismo event_id: no duplica
    client.post("/novedades", json=novedad, headers=auth("campo.reportar"))
    r = client.get("/reportes/turno", params={"fecha": "2026-07-18", "turno": "dia"})
    cuerpo = r.json()
    assert cuerpo["total_toneladas"] == 100.0
    assert len(cuerpo["novedades"]) == 1
    assert cuerpo["novedades"][0]["autor"] == "operador-2"  # autor = sub del token


def test_novedad_requiere_rol(client):
    novedad = {"turno": "dia", "fecha": "2026-07-18", "categoria": "otro", "texto": "abc"}
    assert client.post("/novedades", json=novedad).status_code == 401
    assert client.post("/novedades", json=novedad, headers=auth("reportes.leer")).status_code == 403


def test_conciliacion_detecta_desvios():
    balances = [
        BalanceLote(lote_id="LT-A", masa_entrada_kg=100000, masa_salida_kg=99000),   # 1%: ok
        BalanceLote(lote_id="LT-B", masa_entrada_kg=100000, masa_salida_kg=95000),   # 5%: faltante
        BalanceLote(lote_id="LT-C", masa_entrada_kg=100000, masa_salida_kg=103500),  # 3.5%: excedente
    ]
    alertas = conciliar(balances, tolerancia_pct=2.0)
    assert [a.lote_id for a in alertas] == ["LT-B", "LT-C"]
    assert "faltante" in alertas[0].detalle and "excedente" in alertas[1].detalle


def test_conciliacion_por_api_publica_y_audita(client, entorno, tmp_path):
    balances = [{"lote_id": "LT-B", "masa_entrada_kg": 100000, "masa_salida_kg": 90000}]
    r = client.post("/conciliaciones", json=balances, headers=auth("metalurgia.conciliar"))
    assert r.status_code == 200 and r.json()[0]["desvio_pct"] == 10.0
    outbox = (tmp_path / "outbox" / "outbox.jsonl").read_text()
    assert "conciliacion.alerta" in outbox
    assert "conciliacion.ejecutada" in (tmp_path / "auditoria" / "auditoria.jsonl").read_text()


# --------------------------------------------------- límites y errores de entrada

def test_fecha_invalida_es_422_no_500(client):
    """`fecha` iba directo a datetime.fromisoformat: una fecha basura convertía
    un error del cliente en un error del servidor."""
    r = client.get("/reportes/turno", params={"fecha": "no-es-fecha", "turno": "dia"})
    assert r.status_code == 422


def test_novedad_con_fecha_libre_rechazada(client):
    r = client.post("/novedades", headers=auth("campo.reportar"), json={
        "event_id": str(uuid4()), "turno": "dia", "fecha": "18/07/2026",
        "categoria": "seguridad", "texto": "prueba de formato"})
    assert r.status_code == 422


def test_conciliacion_acotada(client):
    balance = {"lote_id": "L-1", "masa_entrada_kg": 100.0, "masa_salida_kg": 90.0}
    r = client.post("/conciliaciones", headers=auth("metalurgia.conciliar"),
                    json=[balance] * 5_001)
    assert r.status_code == 422


def test_novedades_en_memoria_acotadas():
    """Sin techo, un dispositivo con event_id nuevo en cada reintento hace
    crecer el diccionario hasta voltear el proceso."""
    from app.main import RepoNovedades
    from app.models import Novedad

    repo = RepoNovedades(maximo=10)
    for i in range(50):
        repo.guardar(Novedad(turno="dia", fecha="2026-07-18", categoria="otro",
                             texto=f"novedad {i}"))
    assert len(repo._novedades) == 10
