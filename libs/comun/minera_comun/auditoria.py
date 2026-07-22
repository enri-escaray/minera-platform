"""Auditoría append-only para servicios sin base propia (JSONL local).

Trazabilidad audita en su tabla Postgres (fuente regulatoria); el resto de los
servicios usa este registro local, que la ingesta puede replicar a nube como
cualquier otro flujo. Nunca se edita ni borra: correcciones = registro nuevo.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def auditar(actor: str, accion: str, entidad: str, entidad_id: str, payload: dict) -> None:
    destino = Path(os.getenv("AUDIT_DIR", "/var/auditoria"))
    destino.mkdir(parents=True, exist_ok=True)
    registro = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "accion": accion,
        "entidad": entidad,
        "entidad_id": entidad_id,
        "payload": payload,
    }
    with (destino / "auditoria.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(registro, default=str) + "\n")
