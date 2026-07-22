"""Tests de la columna vertebral de eventos. El escenario central es el modo isla:
el enlace cae, la cola crece, el enlace vuelve, todo se drena en orden y sin duplicados."""
import asyncio
import json

from app.bridge import (
    FakeDestino,
    drenar_una_vez,
    leer_outbox,
    procesar_mensaje_mqtt,
)
from app.cola import ColaPersistente


def cola_nueva(tmp_path) -> ColaPersistente:
    return ColaPersistente(tmp_path / "cola.db")


def msg(equipo: str, metrica: str, ts: str, valor: float) -> tuple[str, bytes]:
    return (
        f"telemetria/{equipo}/{metrica}",
        json.dumps({"ts": ts, "valor": valor}).encode(),
    )


def test_encolar_es_idempotente(tmp_path):
    cola = cola_nueva(tmp_path)
    topico, payload = msg("cam-101", "vibracion_rms", "2026-07-18T10:00:00Z", 4.2)
    assert procesar_mensaje_mqtt(cola, topico, payload, "sitio-dev") is True
    # Reintento MQTT QoS1 exacto: mismo equipo, métrica y timestamp
    assert procesar_mensaje_mqtt(cola, topico, payload, "sitio-dev") is False
    assert cola.profundidad() == 1


def test_mensaje_malformado_no_rompe_el_bridge(tmp_path):
    cola = cola_nueva(tmp_path)
    assert procesar_mensaje_mqtt(cola, "telemetria/cam-101/carga", b"no-es-json", "s") is False
    assert procesar_mensaje_mqtt(cola, "otro/topico", b"{}", "s") is False
    assert cola.profundidad() == 0


def test_modo_isla_completo(tmp_path):
    """El test que justifica toda la arquitectura."""
    cola = cola_nueva(tmp_path)
    destino = FakeDestino()

    # 1. Operación normal: llega telemetría y se publica
    t1, p1 = msg("cam-101", "carga_ton", "2026-07-18T10:00:00Z", 218.5)
    procesar_mensaje_mqtt(cola, t1, p1, "sitio-dev")
    assert asyncio.run(drenar_una_vez(cola, destino)) == 1
    assert cola.profundidad() == 0

    # 2. Cae el enlace. La telemetría sigue llegando: la cola crece, nada se pierde
    destino.disponible = False
    for i in range(5):
        t, p = msg("cam-101", "carga_ton", f"2026-07-18T10:0{i + 1}:00Z", 200.0 + i)
        procesar_mensaje_mqtt(cola, t, p, "sitio-dev")
    assert asyncio.run(drenar_una_vez(cola, destino)) == 0
    assert cola.profundidad() == 5

    # 3. Vuelve el enlace: se drena todo, en orden temporal
    destino.disponible = True
    assert asyncio.run(drenar_una_vez(cola, destino)) == 5
    assert cola.profundidad() == 0
    valores = [e["data"]["valor"] for _, e in destino.publicados[1:]]
    assert valores == [200.0, 201.0, 202.0, 203.0, 204.0]

    # 4. Drenar de nuevo no re-publica nada
    assert asyncio.run(drenar_una_vez(cola, destino)) == 0
    assert len(destino.publicados) == 6


def test_caida_a_mitad_de_lote_retoma_donde_quedo(tmp_path):
    cola = cola_nueva(tmp_path)
    destino = FakeDestino()
    for i in range(4):
        t, p = msg("pala-07", "presion", f"2026-07-18T11:0{i}:00Z", float(i))
        procesar_mensaje_mqtt(cola, t, p, "sitio-dev")

    # El destino acepta 2 y se cae en el tercero
    original = destino.publicar

    async def publicar_fragil(topico, evento):
        if len(destino.publicados) >= 2:
            from app.bridge import DestinoNoDisponible
            raise DestinoNoDisponible("corte a mitad de lote")
        await original(topico, evento)

    destino.publicar = publicar_fragil
    assert asyncio.run(drenar_una_vez(cola, destino)) == 2
    assert cola.profundidad() == 2  # los 2 publicados quedaron marcados; 2 pendientes

    destino.publicar = original
    assert asyncio.run(drenar_una_vez(cola, destino)) == 2
    assert cola.profundidad() == 0
    assert len(destino.publicados) == 4  # exactamente una vez cada uno


def test_relay_outbox(tmp_path):
    """La outbox de trazabilidad viaja a la cola; re-lecturas no duplican."""
    cola = cola_nueva(tmp_path)
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    eventos = [
        {"event_id": "e-1", "event_type": "lote.creado", "data": {}},
        {"event_id": "e-2", "event_type": "pesaje.registrado", "data": {}},
    ]
    (outbox / "outbox.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in eventos)
    )
    rutas = {"lote.creado": "trazabilidad.lotes", "pesaje.registrado": "trazabilidad.pesajes"}

    assert leer_outbox(cola, outbox, rutas) == 2
    assert leer_outbox(cola, outbox, rutas) == 0  # offset guardado: no relee
    # Un evento nuevo se agrega al archivo y solo ese entra
    with (outbox / "outbox.jsonl").open("a") as f:
        f.write(json.dumps({"event_id": "e-3", "event_type": "lote.creado", "data": {}}) + "\n")
    assert leer_outbox(cola, outbox, rutas) == 1
    assert cola.profundidad() == 3
