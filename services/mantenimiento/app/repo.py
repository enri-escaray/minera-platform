"""Repositorios de OTs: memoria (tests/dev) y Postgres (runtime).

La invariante de antirrebote vive en tres capas que se refuerzan:
evaluador (rápida), repositorio (lógica) e índice único parcial en la base
(definitiva, sobrevive reinicios y réplicas).
"""
from __future__ import annotations

import json
from typing import Protocol
from uuid import UUID

from .models import EstadoOT, OrdenTrabajo, OTNoExiste


class RepoOTs(Protocol):
    async def guardar(self, ot: OrdenTrabajo) -> None: ...
    async def obtener(self, ot_id: UUID) -> OrdenTrabajo: ...
    async def listar(self, estado: EstadoOT | None = None) -> list[OrdenTrabajo]: ...
    async def vivas(self) -> list[OrdenTrabajo]: ...


class InMemoryRepoOTs:
    def __init__(self) -> None:
        self._ots: dict[UUID, OrdenTrabajo] = {}

    async def guardar(self, ot: OrdenTrabajo) -> None:
        self._ots[ot.id] = ot

    async def obtener(self, ot_id: UUID) -> OrdenTrabajo:
        try:
            return self._ots[ot_id]
        except KeyError as exc:
            raise OTNoExiste(str(ot_id)) from exc

    async def listar(self, estado: EstadoOT | None = None) -> list[OrdenTrabajo]:
        ots = self._ots.values()
        if estado is not None:
            ots = (o for o in ots if o.estado == estado)
        return sorted(ots, key=lambda o: o.creada_en, reverse=True)

    async def vivas(self) -> list[OrdenTrabajo]:
        return [o for o in self._ots.values() if o.estado != EstadoOT.cerrada]


class PostgresRepoOTs:
    def __init__(self, pool) -> None:  # asyncpg.Pool
        self._pool = pool

    async def guardar(self, ot: OrdenTrabajo) -> None:
        async with self._pool.acquire() as con:
            await con.execute(
                """INSERT INTO mantenimiento.orden_trabajo
                   (id, equipo_id, regla_id, origen, prioridad, estado, diagnostico,
                    repuestos, valor_disparo, creada_en, cerrada_en, cerrada_por, resolucion)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10,$11,$12,$13)
                   ON CONFLICT (id) DO UPDATE SET
                     estado = EXCLUDED.estado, cerrada_en = EXCLUDED.cerrada_en,
                     cerrada_por = EXCLUDED.cerrada_por, resolucion = EXCLUDED.resolucion""",
                ot.id, ot.equipo_id, ot.regla_id, ot.origen, ot.prioridad.value,
                ot.estado.value, ot.diagnostico, json.dumps(ot.repuestos_sugeridos),
                ot.valor_disparo, ot.creada_en, ot.cerrada_en, ot.cerrada_por, ot.resolucion,
            )

    async def obtener(self, ot_id: UUID) -> OrdenTrabajo:
        async with self._pool.acquire() as con:
            fila = await con.fetchrow(
                "SELECT * FROM mantenimiento.orden_trabajo WHERE id = $1", ot_id)
        if not fila:
            raise OTNoExiste(str(ot_id))
        return self._a_modelo(fila)

    async def listar(self, estado: EstadoOT | None = None) -> list[OrdenTrabajo]:
        consulta = "SELECT * FROM mantenimiento.orden_trabajo"
        args: list = []
        if estado is not None:
            consulta += " WHERE estado = $1"
            args.append(estado.value)
        consulta += " ORDER BY creada_en DESC"
        async with self._pool.acquire() as con:
            return [self._a_modelo(f) for f in await con.fetch(consulta, *args)]

    async def vivas(self) -> list[OrdenTrabajo]:
        async with self._pool.acquire() as con:
            filas = await con.fetch(
                "SELECT * FROM mantenimiento.orden_trabajo WHERE estado <> 'cerrada'")
        return [self._a_modelo(f) for f in filas]

    @staticmethod
    def _a_modelo(fila) -> OrdenTrabajo:
        datos = dict(fila)
        datos["repuestos_sugeridos"] = json.loads(datos.pop("repuestos"))
        return OrdenTrabajo(**datos)
