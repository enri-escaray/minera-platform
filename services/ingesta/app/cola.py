"""Cola persistente en SQLite: el buffer store-and-forward del sitio.

Por qué SQLite y no memoria: si el proceso muere durante un corte de enlace,
los eventos encolados sobreviven al reinicio (WAL + fsync). Es la garantía
de que el modo isla no pierde datos, cueste lo que cueste el corte.

Idempotencia: event_id es PRIMARY KEY. Encolar el mismo evento dos veces
(reintento MQTT QoS1, re-lectura de outbox) es un no-op.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


class ColaPersistente:
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._lock = threading.Lock()
        con = self._con()
        con.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS eventos (
                event_id   TEXT PRIMARY KEY,
                topico     TEXT NOT NULL,
                payload    TEXT NOT NULL,
                estado     TEXT NOT NULL DEFAULT 'pendiente',
                creado_en  TEXT NOT NULL,
                enviado_en TEXT
            );
            CREATE INDEX IF NOT EXISTS eventos_pendientes_idx
                ON eventos (estado, creado_en);
            """
        )
        con.commit()
        con.close()

    def _con(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._path, timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def encolar(self, topico: str, evento: dict) -> bool:
        """True si se encoló; False si event_id ya existía (duplicado ignorado)."""
        event_id = evento["event_id"]
        with self._lock:
            con = self._con()
            try:
                cur = con.execute(
                    "INSERT OR IGNORE INTO eventos (event_id, topico, payload, creado_en) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        str(event_id),
                        topico,
                        json.dumps(evento, default=str),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                con.commit()
                return cur.rowcount == 1
            finally:
                con.close()

    def pendientes(self, limite: int = 200) -> list[tuple[str, str, dict]]:
        """(event_id, topico, evento) en orden de llegada — preserva causalidad."""
        con = self._con()
        try:
            filas = con.execute(
                "SELECT event_id, topico, payload FROM eventos "
                "WHERE estado = 'pendiente' ORDER BY creado_en, event_id LIMIT ?",
                (limite,),
            ).fetchall()
            return [(f["event_id"], f["topico"], json.loads(f["payload"])) for f in filas]
        finally:
            con.close()

    def marcar_enviados(self, event_ids: list[str]) -> None:
        if not event_ids:
            return
        with self._lock:
            con = self._con()
            try:
                ahora = datetime.now(timezone.utc).isoformat()
                con.executemany(
                    "UPDATE eventos SET estado = 'enviado', enviado_en = ? WHERE event_id = ?",
                    [(ahora, eid) for eid in event_ids],
                )
                con.commit()
            finally:
                con.close()

    def profundidad(self) -> int:
        """Eventos pendientes. Métrica clave: crece en modo isla, drena al reconectar."""
        con = self._con()
        try:
            return con.execute(
                "SELECT count(*) FROM eventos WHERE estado = 'pendiente'"
            ).fetchone()[0]
        finally:
            con.close()

    def purgar_enviados(self, conservar_horas: int = 48) -> int:
        """Limpieza periódica; conserva enviados recientes para auditoría/debug local."""
        with self._lock:
            con = self._con()
            try:
                cur = con.execute(
                    "DELETE FROM eventos WHERE estado = 'enviado' "
                    "AND enviado_en < datetime('now', ?)",
                    (f"-{conservar_horas} hours",),
                )
                con.commit()
                return cur.rowcount
            finally:
                con.close()
