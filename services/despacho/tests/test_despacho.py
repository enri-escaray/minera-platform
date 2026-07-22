"""Tests de despacho: criterio de asignación, colas, exclusiones, RBAC y determinismo."""
import pytest
from fastapi.testclient import TestClient

from minera_comun import emitir_token_dev


@pytest.fixture(autouse=True)
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTBOX_DIR", str(tmp_path / "outbox"))
    monkeypatch.setenv("AUDIT_DIR", str(tmp_path / "auditoria"))
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("AUTH_SECRET", "secreto-test")


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def auth(*roles):
    return {"Authorization": f"Bearer {emitir_token_dev('despachador-1', list(roles))}"}


@pytest.fixture()
def rajo(client):
    cfg, op = auth("despacho.configurar"), auth("despacho.operar")
    for pala in (
        {"id": "pala-01", "ubicacion_id": "frente-n", "tiempo_carga_min": 2.5, "prioridad_mezcla": 1},
        {"id": "pala-02", "ubicacion_id": "frente-s", "tiempo_carga_min": 3.0, "prioridad_mezcla": 5},
    ):
        assert client.post("/palas", json=pala, headers=cfg).status_code == 201
    for camion in ("cam-101", "cam-102", "cam-103"):
        assert client.post(
            "/camiones", json={"id": camion, "capacidad_ton": 220}, headers=cfg
        ).status_code == 201
    return op


def test_asigna_menor_espera(client, rajo):
    r = client.post("/asignaciones", headers=rajo,
                    json={"camion_id": "cam-101", "viaje_min": {"pala-01": 4.0, "pala-02": 9.0}})
    assert r.status_code == 201
    cuerpo = r.json()
    assert cuerpo["pala_id"] == "pala-01"
    assert cuerpo["espera_estimada_min"] == 4.0
    assert "espera estimada" in cuerpo["motivo"]  # decisión explicable


def test_cola_desvia_al_siguiente_camion(client, rajo):
    # cam-101 va a pala-01 (4.0 vs 5.0). Su presencia en cola encarece pala-01:
    client.post("/asignaciones", headers=rajo,
                json={"camion_id": "cam-101", "viaje_min": {"pala-01": 4.0, "pala-02": 5.0}})
    r = client.post("/asignaciones", headers=rajo,
                    json={"camion_id": "cam-102", "viaje_min": {"pala-01": 4.0, "pala-02": 5.0}})
    # pala-01: 4.0 + 1*2.5 = 6.5 ; pala-02: 5.0 → gana pala-02
    assert r.json()["pala_id"] == "pala-02"


def test_liberar_descuenta_cola(client, rajo):
    client.post("/asignaciones", headers=rajo,
                json={"camion_id": "cam-101", "viaje_min": {"pala-01": 4.0, "pala-02": 5.0}})
    client.post("/camiones/cam-101/liberar", headers=rajo)
    r = client.post("/asignaciones", headers=rajo,
                    json={"camion_id": "cam-102", "viaje_min": {"pala-01": 4.0, "pala-02": 5.0}})
    assert r.json()["pala_id"] == "pala-01"  # cola vacía otra vez


def test_empate_lo_define_prioridad_de_mezcla(client, rajo):
    r = client.post("/asignaciones", headers=rajo,
                    json={"camion_id": "cam-101", "viaje_min": {"pala-01": 6.0, "pala-02": 6.0}})
    assert r.json()["pala_id"] == "pala-02"  # misma espera, prioridad 5 > 1


def test_camion_en_mantenimiento_no_se_asigna(client, rajo):
    client.post("/camiones/cam-103/mantenimiento", headers=rajo)
    r = client.post("/asignaciones", headers=rajo, json={"camion_id": "cam-103"})
    assert r.status_code == 409


def test_camion_ya_en_ciclo_no_se_reasigna(client, rajo):
    client.post("/asignaciones", headers=rajo, json={"camion_id": "cam-101"})
    assert client.post("/asignaciones", headers=rajo,
                       json={"camion_id": "cam-101"}).status_code == 409


def test_escrituras_exigen_rol(client, rajo):
    assert client.post("/asignaciones", json={"camion_id": "cam-101"}).status_code == 401
    assert client.post("/asignaciones", json={"camion_id": "cam-101"},
                       headers=auth("reportes.leer")).status_code == 403


def test_restaurar_reconstruye_estado_del_rajo():
    """Reinicio del servicio a mitad de turno: colas y asignaciones vivas vuelven."""
    import asyncio

    from app.models import Asignacion, Camion, Despachador, EstadoCamion, Pala

    class FakePersistencia:
        async def cargar(self):
            pala = Pala(id="pala-01", ubicacion_id="frente-n", tiempo_carga_min=2.5)
            c1 = Camion(id="cam-101", capacidad_ton=220, estado=EstadoCamion.en_ciclo)
            c2 = Camion(id="cam-102", capacidad_ton=220)
            viva = Asignacion(camion_id="cam-101", pala_id="pala-01",
                              espera_estimada_min=4.0, motivo="restaurada")
            return [pala], [c1, c2], [viva]

        async def guardar_pala(self, p): ...
        async def guardar_camion(self, c): ...
        async def guardar_asignacion(self, a): ...
        async def marcar_liberada(self, cid): ...

    async def correr():
        d = Despachador(FakePersistencia())
        await d.restaurar()
        estado = await d.estado()
        assert estado["en_cola"]["pala-01"] == 1          # la cola se reconstruyó
        assert estado["asignaciones_vivas"] == 1
        # cam-101 sigue en ciclo (no puede reasignarse); cam-102 sí puede
        from app.models import CamionNoDisponible
        try:
            await d.asignar("cam-101", {})
            raise AssertionError("debió rechazar")
        except CamionNoDisponible:
            pass
        asignacion = await d.asignar("cam-102", {"pala-01": 3.0})
        assert asignacion.espera_estimada_min == 3.0 + 1 * 2.5  # la cola restaurada cuenta
        # liberar el restaurado descuenta la cola correctamente
        await d.liberar("cam-101")
        assert (await d.estado())["en_cola"]["pala-01"] == 1

    asyncio.run(correr())
