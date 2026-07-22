"""Tests del flujo P1: ventana de persistencia, antirrebote, cierre humano con RBAC."""
import json

import pytest
from fastapi.testclient import TestClient

from minera_comun import emitir_token_dev


def telemetria(equipo: str, metrica: str, valor: float, n: int = 0) -> dict:
    return {
        "event_id": f"evt-{equipo}-{metrica}-{n}",
        "event_type": "telemetria.medida",
        "version": 1,
        "sitio_id": "sitio-dev",
        "occurred_at": f"2026-07-18T10:{n:02d}:00Z",
        "data": {"equipo_id": equipo, "metrica": metrica, "valor": valor},
    }


@pytest.fixture(autouse=True)
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTBOX_DIR", str(tmp_path / "outbox"))
    monkeypatch.setenv("AUDIT_DIR", str(tmp_path / "auditoria"))
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("AUTH_SECRET", "secreto-test")
    yield tmp_path


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def app_state():
    from app.main import app

    return app.state


def inyectar(valores: list[tuple[str, str, float]]):
    """Inyecta eventos de telemetría directo al núcleo (sin Kafka)."""
    import asyncio

    from app.main import procesar_evento_telemetria

    async def correr():
        generadas = []
        for n, (equipo, metrica, valor) in enumerate(valores):
            generadas += await procesar_evento_telemetria(
                telemetria(equipo, metrica, valor, n), app_state().evaluador, app_state().repo
            )
        return generadas

    return asyncio.run(correr())


def leer_outbox(tmp_path) -> list[dict]:
    archivo = tmp_path / "outbox" / "outbox.jsonl"
    if not archivo.exists():
        return []
    return [json.loads(l) for l in archivo.read_text().splitlines()]


def auth(roles: list[str]) -> dict:
    return {"Authorization": f"Bearer {emitir_token_dev('tecnico-1', roles)}"}


def test_pico_aislado_no_dispara(client, entorno):
    ots = inyectar([
        ("cam-101", "vibracion_rms", 9.0),   # pico
        ("cam-101", "vibracion_rms", 3.0),   # vuelve a sano: corta la racha
        ("cam-101", "vibracion_rms", 9.0),
        ("cam-101", "vibracion_rms", 9.0),
    ])
    assert ots == []
    assert leer_outbox(entorno) == []


def test_ventana_sostenida_dispara_una_ot(client, entorno):
    ots = inyectar([("cam-101", "vibracion_rms", v) for v in (8.0, 8.5, 9.0)])
    assert len(ots) == 1
    assert ots[0].prioridad.value == "alta"
    eventos = leer_outbox(entorno)
    assert len(eventos) == 1 and eventos[0]["event_type"] == "ot.generada"
    assert eventos[0]["data"]["repuestos_sugeridos"] == ["rodamiento-22320", "kit-alineacion"]


def test_antirrebote_mientras_ot_abierta(client, entorno):
    inyectar([("cam-101", "vibracion_rms", v) for v in (8.0, 8.5, 9.0)])
    # La vibración sigue alta: NO se generan OTs duplicadas
    ots = inyectar([("cam-101", "vibracion_rms", 9.5) for _ in range(10)])
    assert ots == []
    assert len(leer_outbox(entorno)) == 1


def test_equipos_independientes(client):
    inyectar([("cam-101", "vibracion_rms", v) for v in (8.0, 8.5, 9.0)])
    ots = inyectar([("pala-07", "vibracion_rms", v) for v in (8.0, 8.5, 9.0)])
    assert len(ots) == 1 and ots[0].equipo_id == "pala-07"


def test_cierre_requiere_rol_y_rehabilita_vigilancia(client, entorno):
    ots = inyectar([("cam-101", "temp_hidraulico_c", v) for v in (96.0, 97.0)])
    ot_id = str(ots[0].id)

    # Sin token: 401. Con rol equivocado: 403.
    assert client.post(f"/ots/{ot_id}/cerrar", json={"resolucion": "x"}).status_code == 401
    assert client.post(
        f"/ots/{ot_id}/cerrar", json={"resolucion": "x"}, headers=auth(["reportes.leer"])
    ).status_code == 403

    # Con rol correcto: cierra, audita y publica ot.cerrada
    r = client.post(
        f"/ots/{ot_id}/cerrar",
        json={"resolucion": "Cambio de filtro y aceite; temperatura normalizada"},
        headers=auth(["mantenimiento.cerrar"]),
    )
    assert r.status_code == 200 and r.json()["estado"] == "cerrada"
    tipos = [e["event_type"] for e in leer_outbox(entorno)]
    assert tipos == ["ot.generada", "ot.cerrada"]
    audit = (entorno / "auditoria" / "auditoria.jsonl").read_text()
    assert "ot.cerrada" in audit and "tecnico-1" in audit

    # Cerrada la OT, la regla vuelve a vigilar: nueva racha => nueva OT
    nuevas = inyectar([("cam-101", "temp_hidraulico_c", v) for v in (98.0, 99.0)])
    assert len(nuevas) == 1

    # Cerrar dos veces: conflicto explícito
    assert client.post(
        f"/ots/{ot_id}/cerrar", json={"resolucion": "x"}, headers=auth(["mantenimiento.cerrar"])
    ).status_code == 409


def test_evento_malformado_no_rompe(client):
    import asyncio

    from app.main import procesar_evento_telemetria

    async def correr():
        assert await procesar_evento_telemetria(
            {"data": {"equipo_id": "x"}}, app_state().evaluador, app_state().repo) == []
        assert await procesar_evento_telemetria({}, app_state().evaluador, app_state().repo) == []

    asyncio.run(correr())


def test_umbral_inferior_dispara(client):
    """Regla con condicion 'menor': presión baja sostenida."""
    ots = inyectar([("cam-101", "presion_neumatico_psi", v) for v in (80, 82, 79, 81, 83)])
    assert len(ots) == 1 and ots[0].regla_id == "presion-neumatico-baja"
    # Una lectura sana en el medio corta la racha
    ots = inyectar([("cam-102", "presion_neumatico_psi", v) for v in (80, 82, 95, 81, 83, 80)])
    assert ots == []


def test_regla_compuesta_exige_contexto(client):
    """Sobrecarga sostenida NO dispara si la velocidad no acompaña (o se desconoce)."""
    # Sin dato de velocidad: la racha se completa pero el contexto falta → no dispara
    assert inyectar([("cam-101", "carga_ton", v) for v in (250, 251, 252)]) == []
    # Velocidad baja: sigue sin disparar
    inyectar([("cam-101", "velocidad_kmh", 12.0)])
    assert inyectar([("cam-101", "carga_ton", 253)]) == []
    # Velocidad alta conocida: la próxima lectura sobre umbral dispara
    inyectar([("cam-101", "velocidad_kmh", 31.0)])
    ots = inyectar([("cam-101", "carga_ton", 254)])
    assert len(ots) == 1 and ots[0].regla_id == "sobrecarga-a-velocidad"
