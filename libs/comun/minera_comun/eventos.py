"""Outbox transaccional simplificada, compartida por todos los servicios del sitio.

El servicio escribe su evento acá (JSONL local, append atómico por línea) y el
relay de `ingesta` lo lleva a Kafka. En modo isla la outbox crece; nunca se
bloquea la operación por falta de broker (ADR-002, ADR-003).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def publicar_evento(
    event_type: str,
    data: dict,
    *,
    outbox_dir: str | Path | None = None,
    sitio_id: str | None = None,
    trace_id: str | None = None,
) -> dict:
    """Escribe el evento a la outbox y lo retorna (con su envelope completo)."""
    destino = Path(outbox_dir or os.getenv("OUTBOX_DIR", "/var/outbox"))
    destino.mkdir(parents=True, exist_ok=True)
    evento = {
        "event_id": str(uuid4()),
        "event_type": event_type,
        "version": 1,
        "sitio_id": sitio_id or os.getenv("SITIO_ID", "sitio-dev"),
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "trace_id": trace_id or str(uuid4()),
        "data": data,
    }
    with (destino / "outbox.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(evento, default=str) + "\n")
    return evento
