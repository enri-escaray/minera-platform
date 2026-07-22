"""Runtime del servicio de ingesta.

Corre tres cosas en paralelo:
1. Cliente MQTT suscrito a telemetria/# → encola en la cola persistente.
2. Bucle relay: lee las outbox de los demás servicios → misma cola.
3. Bucle drenador: cola → Kafka, con backoff cuando el enlace cae.

Expone /health con la profundidad de cola: si crece sostenido, el sitio está en modo isla.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading

from fastapi import FastAPI
import uvicorn

from .bridge import KafkaDestino, bucle_drenador, leer_outbox, metricas, procesar_mensaje_mqtt
from .cola import ColaPersistente

log = logging.getLogger("ingesta")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

SITIO_ID = os.getenv("SITIO_ID", "sitio-dev")
COLA_PATH = os.getenv("COLA_PATH", "/var/cola/eventos.db")
OUTBOX_DIR = os.getenv("OUTBOX_DIR", "/var/outbox")
MQTT_HOST = os.getenv("MQTT_HOST", "mqtt")
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "redpanda:9092")

RUTAS_OUTBOX = {
    "lote.creado": "trazabilidad.lotes",
    "pesaje.registrado": "trazabilidad.pesajes",
    "ot.generada": "mantenimiento.ots",
    "ot.cerrada": "mantenimiento.ots",
    "asignacion.creada": "despacho.asignaciones",
    "bloque.importado": "trazabilidad.lotes",
    "novedad.registrada": "reportes.novedades",
    "conciliacion.alerta": "reportes.conciliaciones",
    "reporte.turno.generado": "reportes.turnos",
}

cola = ColaPersistente(COLA_PATH)
app = FastAPI(title="Ingesta", version="0.1.0")


@app.get("/health")
async def health():
    m = metricas(cola)
    m["sitio"] = SITIO_ID
    return m


def iniciar_mqtt() -> None:
    """Cliente MQTT en hilo propio (paho es síncrono). QoS 1: el broker reintenta,
    la idempotencia por uuid5 absorbe los duplicados del reintento."""
    import paho.mqtt.client as mqtt

    def on_connect(client, _userdata, _flags, rc, _props=None):
        log.info("MQTT conectado rc=%s; suscribiendo telemetria/#", rc)
        client.subscribe("telemetria/#", qos=1)

    def on_message(_client, _userdata, msg):
        if not procesar_mensaje_mqtt(cola, msg.topic, msg.payload, SITIO_ID):
            log.debug("descartado o duplicado: %s", msg.topic)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    while True:
        try:
            client.connect(MQTT_HOST, 1883, keepalive=30)
            break
        except OSError as exc:
            log.warning("MQTT no disponible (%s); reintento en 5s", exc)
            import time

            time.sleep(5)
    client.loop_forever(retry_first_connection=True)


async def bucle_relay(intervalo_s: float = 2.0) -> None:
    while True:
        try:
            nuevos = leer_outbox(cola, OUTBOX_DIR, RUTAS_OUTBOX)
            if nuevos:
                log.info("relay: %d eventos de outbox encolados", nuevos)
        except FileNotFoundError:
            pass  # todavía no hay outbox: normal en arranque
        await asyncio.sleep(intervalo_s)


async def bucle_purga() -> None:
    while True:
        await asyncio.sleep(3600)
        borrados = cola.purgar_enviados(conservar_horas=48)
        if borrados:
            log.info("purga: %d eventos enviados antiguos eliminados", borrados)


async def principal() -> None:
    threading.Thread(target=iniciar_mqtt, daemon=True, name="mqtt").start()
    destino = KafkaDestino(KAFKA_BOOTSTRAP)
    servidor = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8001, log_level="warning"))
    await asyncio.gather(
        bucle_drenador(cola, destino),
        bucle_relay(),
        bucle_purga(),
        servidor.serve(),
    )


if __name__ == "__main__":
    asyncio.run(principal())
