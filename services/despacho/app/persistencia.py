"""Persistencia write-through del despachador.

El Despachador sigue siendo el único escritor en memoria (CP local, decisiones
en microsegundos); la persistencia registra cada cambio y permite reconstruir
el estado completo al reiniciar: palas, camiones y colas derivadas de las
asignaciones vivas. `NulaPersistencia` mantiene los tests sin base.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from .models import Asignacion, Camion, EstadoCamion, Pala


class Persistencia(Protocol):
    async def guardar_pala(self, pala: Pala) -> None: ...
    async def guardar_camion(self, camion: Camion) -> None: ...
    async def guardar_asignacion(self, asignacion: Asignacion) -> None: ...
    async def marcar_liberada(self, camion_id: str) -> None: ...
    async def cargar(self) -> tuple[list[Pala], list[Camion], list[Asignacion]]: ...


class NulaPersistencia:
    async def guardar_pala(self, pala: Pala) -> None: ...
    async def guardar_camion(self, camion: Camion) -> None: ...
    async def guardar_asignacion(self, asignacion: Asignacion) -> None: ...
    async def marcar_liberada(self, camion_id: str) -> None: ...

    async def cargar(self) -> tuple[list[Pala], list[Camion], list[Asignacion]]:
        return [], [], []


class PostgresPersistencia:
    def __init__(self, pool) -> None:  # asyncpg.Pool
        self._pool = pool

    async def guardar_pala(self, pala: Pala) -> None:
        async with self._pool.acquire() as con:
            await con.execute(
                """INSERT INTO despacho.pala (id, ubicacion_id, tiempo_carga_min, prioridad_mezcla, activa)
                   VALUES ($1,$2,$3,$4,$5)
                   ON CONFLICT (id) DO UPDATE SET ubicacion_id=EXCLUDED.ubicacion_id,
                     tiempo_carga_min=EXCLUDED.tiempo_carga_min,
                     prioridad_mezcla=EXCLUDED.prioridad_mezcla, activa=EXCLUDED.activa""",
                pala.id, pala.ubicacion_id, pala.tiempo_carga_min, pala.prioridad_mezcla, pala.activa)

    async def guardar_camion(self, camion: Camion) -> None:
        async with self._pool.acquire() as con:
            await con.execute(
                """INSERT INTO despacho.camion (id, capacidad_ton, estado) VALUES ($1,$2,$3)
                   ON CONFLICT (id) DO UPDATE SET capacidad_ton=EXCLUDED.capacidad_ton,
                     estado=EXCLUDED.estado""",
                camion.id, camion.capacidad_ton, camion.estado.value)

    async def guardar_asignacion(self, asignacion: Asignacion) -> None:
        async with self._pool.acquire() as con:
            await con.execute(
                """INSERT INTO despacho.asignacion
                   (id, camion_id, pala_id, espera_estimada_min, motivo, creada_en)
                   VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (id) DO NOTHING""",
                asignacion.id, asignacion.camion_id, asignacion.pala_id,
                asignacion.espera_estimada_min, asignacion.motivo, asignacion.creada_en)

    async def marcar_liberada(self, camion_id: str) -> None:
        async with self._pool.acquire() as con:
            await con.execute(
                "UPDATE despacho.asignacion SET liberada_en=$1 "
                "WHERE camion_id=$2 AND liberada_en IS NULL",
                datetime.now(timezone.utc), camion_id)

    async def cargar(self) -> tuple[list[Pala], list[Camion], list[Asignacion]]:
        async with self._pool.acquire() as con:
            palas = [Pala(**dict(f)) for f in await con.fetch("SELECT * FROM despacho.pala")]
            camiones = [Camion(id=f["id"], capacidad_ton=float(f["capacidad_ton"]),
                               estado=EstadoCamion(f["estado"]))
                        for f in await con.fetch("SELECT * FROM despacho.camion")]
            vivas = [Asignacion(id=f["id"], camion_id=f["camion_id"], pala_id=f["pala_id"],
                                espera_estimada_min=float(f["espera_estimada_min"]),
                                motivo=f["motivo"], creada_en=f["creada_en"])
                     for f in await con.fetch(
                         "SELECT * FROM despacho.asignacion WHERE liberada_en IS NULL")]
        return palas, camiones, vivas
