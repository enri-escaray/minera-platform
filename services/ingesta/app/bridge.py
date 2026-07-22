"""Bridge de ingesta del sitio: MQTT → cola persistente → Kafka, más relay de outbox.

Diseño por puertos y adaptadores:
- `Destino` es la interfaz de publicación (KafkaDestino en runtime, FakeDestino en tests).
- El drenador es agnóstico: toma pendientes de la cola, publica, marca enviados.
- Si el destino falla (enlace caído), el drenador retrocede con backoff y la cola crece.
  Nada se pierde, nada se bloquea, nada se duplica.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .cola import ColaPersistente

# Namespace fijo para derivar event_id determinístico de mensajes de sensores
# que no traen id propio: el mismo (equipo, metrica, ts) siempre da el mismo UUID,
# así los reintentos de MQTT QoS1 no generan eventos nuevos.
NS_TELEMETRIA = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


class DestinoNoDisponible(Exception):
    """El broker de destino no responde; el drenador debe esperar y reintentar."""


class Destino(Protocol):
    async def publicar(self, topico: str, evento: dict) -> None: ...


class FakeDestino:
    """Para tests: registra lo publicado y puede simular caída de enlace."""

    def __init__(self) -> None:
        self.publicados: list[tuple[str, dict]] = []
        self.disponible = True

    async def publicar(self, topico: str, evento: dict) -> None:
        if not self.disponible:
            raise DestinoNoDisponible("enlace caído (simulado)")
        self.publicados.append((topico, evento))


class KafkaDestino:
    """Adaptador aiokafka. Import perezoso: los tests no necesitan la librería."""

    def __init__(self, bootstrap: str) -> None:
        self._bootstrap = bootstrap
        self._producer = None

    async def _asegurar(self):
        if self._producer is None:
            from aiokafka import AIOKafkaProducer  # import perezoso

            self._producer = AIOKafkaProducer(
                bootstrap_servers=self._bootstrap,
                acks="all",
                enable_idempotence=True,
            )
            await self._producer.start()
        return self._producer

    async def publicar(self, topico: str, evento: dict) -> None:
        try:
            producer = await self._asegurar()
            await producer.send_and_wait(
                topico,
                json.dumps(evento, default=str).encode(),
                key=str(evento.get("data", {}).get("equipo_id", evento["event_id"])).encode(),
            )
        except Exception as exc:  # noqa: BLE001 — cualquier fallo de red = no disponible
            self._producer = None
            raise DestinoNoDisponible(str(exc)) from exc


# ---------------------------------------------------------------- bridge MQTT

def procesar_mensaje_mqtt(
    cola: ColaPersistente, topico_mqtt: str, payload: bytes, sitio_id: str
) -> bool:
    """Convierte un mensaje MQTT de telemetría en evento de dominio y lo encola.

    Convención de tópico MQTT: telemetria/{equipo_id}/{metrica}
    Payload JSON: {"ts": "...", "valor": <num>}
    Retorna True si encoló, False si era duplicado o inválido.
    """
    partes = topico_mqtt.split("/")
    if len(partes) != 3 or partes[0] != "telemetria":
        return False
    _, equipo_id, metrica = partes
    try:
        cuerpo = json.loads(payload)
        ts = str(cuerpo["ts"])
        valor = float(cuerpo["valor"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return False  # mensaje malformado: se descarta y se cuenta, no se rompe el bridge

    event_id = uuid.uuid5(NS_TELEMETRIA, f"{equipo_id}|{metrica}|{ts}")
    evento = {
        "event_id": str(event_id),
        "event_type": "telemetria.medida",
        "version": 1,
        "sitio_id": sitio_id,
        "occurred_at": ts,
        "data": {"equipo_id": equipo_id, "metrica": metrica, "valor": valor},
    }
    return cola.encolar("telemetria.equipos", evento)


# ------------------------------------------------------------- relay de outbox

def leer_outbox(cola: ColaPersistente, outbox_dir: str | Path, topico_por_tipo: dict) -> int:
    """Lee las outbox JSONL de los servicios y encola sus eventos.

    Guarda el offset en bytes por archivo (sidecar .offset): re-lecturas parciales
    son seguras porque encolar es idempotente por event_id. Retorna eventos nuevos.
    """
    outbox_dir = Path(outbox_dir)
    nuevos = 0
    for archivo in sorted(outbox_dir.glob("*.jsonl")):
        offset_file = archivo.with_suffix(".offset")
        desde = int(offset_file.read_text()) if offset_file.exists() else 0
        with archivo.open("rb") as f:
            f.seek(desde)
            for linea in f:
                if not linea.endswith(b"\n"):
                    break  # línea a medio escribir: se retoma en la próxima pasada
                try:
                    evento = json.loads(linea)
                    topico = topico_por_tipo.get(
                        evento.get("event_type", ""), "eventos.sin_ruta"
                    )
                    if cola.encolar(topico, evento):
                        nuevos += 1
                except json.JSONDecodeError:
                    pass  # línea corrupta: se salta, se loguea en runtime
                desde = f.tell()
        offset_file.write_text(str(desde))
    return nuevos


# ------------------------------------------------------------------- drenador

async def drenar_una_vez(cola: ColaPersistente, destino: Destino, limite: int = 200) -> int:
    """Publica un lote de pendientes en orden. Retorna cuántos envió.

    Si el destino cae a mitad de lote, lo ya publicado queda marcado y el resto
    permanece pendiente: el próximo intento retoma exactamente donde quedó.
    """
    enviados: list[str] = []
    try:
        for event_id, topico, evento in cola.pendientes(limite):
            await destino.publicar(topico, evento)
            enviados.append(event_id)
    except DestinoNoDisponible:
        pass
    finally:
        cola.marcar_enviados(enviados)
    return len(enviados)


async def bucle_drenador(
    cola: ColaPersistente,
    destino: Destino,
    intervalo_s: float = 1.0,
    backoff_max_s: float = 60.0,
    detener: asyncio.Event | None = None,
) -> None:
    """Bucle infinito con backoff exponencial cuando el enlace está caído."""
    espera = intervalo_s
    while detener is None or not detener.is_set():
        pendientes_antes = cola.profundidad()
        enviados = await drenar_una_vez(cola, destino)
        if enviados > 0 or pendientes_antes == 0:
            espera = intervalo_s  # enlace sano: ritmo normal
        else:
            espera = min(espera * 2, backoff_max_s)  # enlace caído: backoff
        await asyncio.sleep(espera)


def metricas(cola: ColaPersistente) -> dict:
    """Para /health y Prometheus: la profundidad de cola ES el estado del enlace."""
    return {
        "cola_pendientes": cola.profundidad(),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
