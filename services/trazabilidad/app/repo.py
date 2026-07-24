"""Patrón repositorio: InMemoryRepo para tests/dev sin DB, PostgresRepo en runtime.

Ambos implementan la misma interfaz. La idempotencia se resuelve ACÁ (en el borde de
persistencia), no en la capa HTTP: es la última línea de defensa contra duplicados
producidos por reintentos de sincronización en modo isla.
"""
from __future__ import annotations

from typing import Protocol
from uuid import UUID, uuid4

from datetime import datetime

from .models import (
    Bloque,
    CodigoDuplicado,
    CrearLote,
    EstadoLote,
    Lote,
    LoteNoExiste,
    Pesaje,
    RegistrarPesaje,
)


class Repo(Protocol):
    async def crear_lote(self, cmd: CrearLote) -> tuple[Lote, bool]: ...
    async def obtener_lote(self, lote_id: UUID) -> Lote: ...
    async def registrar_pesaje(self, cmd: RegistrarPesaje) -> tuple[Pesaje, bool]: ...
    async def pesajes_de(self, lote_id: UUID) -> list[Pesaje]: ...
    async def listar_pesajes(self, desde: datetime, hasta: datetime) -> list[Pesaje]: ...
    async def listar_lotes(self) -> list[Lote]: ...
    async def guardar_bloques(self, origen_modelo: str, bloques: list[Bloque]) -> int: ...


class InMemoryRepo:
    """Para tests y desarrollo sin base. Misma semántica de idempotencia que Postgres."""

    def __init__(self) -> None:
        self._lotes: dict[UUID, Lote] = {}
        self._por_codigo: dict[str, UUID] = {}
        self._comandos_vistos: dict[UUID, UUID] = {}  # event_id -> lote_id
        self._pesajes: dict[UUID, Pesaje] = {}         # event_id -> pesaje
        self._bloques: dict[str, Bloque] = {}

    async def crear_lote(self, cmd: CrearLote) -> tuple[Lote, bool]:
        if cmd.event_id in self._comandos_vistos:
            return self._lotes[self._comandos_vistos[cmd.event_id]], False
        if cmd.codigo in self._por_codigo:
            raise CodigoDuplicado(cmd.codigo)
        for origen in cmd.lotes_origen:
            if origen not in self._lotes:
                raise LoteNoExiste(str(origen))
        lote = Lote(
            id=uuid4(),
            codigo=cmd.codigo,
            tipo=cmd.tipo,
            disparo_id=cmd.disparo_id,
            lotes_origen=cmd.lotes_origen,
        )
        self._lotes[lote.id] = lote
        self._por_codigo[lote.codigo] = lote.id
        self._comandos_vistos[cmd.event_id] = lote.id
        return lote, True

    async def obtener_lote(self, lote_id: UUID) -> Lote:
        try:
            return self._lotes[lote_id]
        except KeyError as exc:
            raise LoteNoExiste(str(lote_id)) from exc

    async def registrar_pesaje(self, cmd: RegistrarPesaje) -> tuple[Pesaje, bool]:
        if cmd.event_id in self._pesajes:
            return self._pesajes[cmd.event_id], False  # duplicado: no suma masa
        if cmd.lote_id not in self._lotes:
            raise LoteNoExiste(str(cmd.lote_id))
        pesaje = Pesaje(**cmd.model_dump())
        self._pesajes[cmd.event_id] = pesaje
        return pesaje, True

    async def pesajes_de(self, lote_id: UUID) -> list[Pesaje]:
        return sorted(
            (p for p in self._pesajes.values() if p.lote_id == lote_id),
            key=lambda p: p.medido_en,
        )

    async def listar_pesajes(self, desde: datetime, hasta: datetime) -> list[Pesaje]:
        return sorted(
            (p for p in self._pesajes.values() if desde <= p.medido_en < hasta),
            key=lambda p: p.medido_en,
        )

    async def listar_lotes(self) -> list[Lote]:
        return sorted(self._lotes.values(), key=lambda l: l.creado_en, reverse=True)

    async def guardar_bloques(self, origen_modelo: str, bloques: list[Bloque]) -> int:
        for bloque in bloques:
            self._bloques[bloque.id] = bloque  # upsert: el último import gana
        return len(bloques)


class PostgresRepo:
    """Implementación asyncpg. La idempotencia usa ON CONFLICT sobre claves únicas."""

    def __init__(self, pool) -> None:  # asyncpg.Pool
        self._pool = pool

    async def crear_lote(self, cmd: CrearLote) -> tuple[Lote, bool]:
        async with self._pool.acquire() as con, con.transaction():
            existente = await con.fetchrow(
                "SELECT l.* FROM trazabilidad.auditoria a "
                "JOIN trazabilidad.lote_mineral l ON l.id::text = a.entidad_id "
                "WHERE a.accion = 'lote.creado' AND a.payload->>'event_id' = $1",
                str(cmd.event_id),
            )
            if existente:
                return await self._hidratar_lote(con, existente), False
            dup = await con.fetchval(
                "SELECT 1 FROM trazabilidad.lote_mineral WHERE codigo = $1", cmd.codigo
            )
            if dup:
                raise CodigoDuplicado(cmd.codigo)
            lote_id = uuid4()
            await con.execute(
                "INSERT INTO trazabilidad.lote_mineral (id, codigo, disparo_id, tipo) "
                "VALUES ($1, $2, $3, $4)",
                lote_id, cmd.codigo, cmd.disparo_id, cmd.tipo.value,
            )
            for origen in cmd.lotes_origen:
                await con.execute(
                    "INSERT INTO trazabilidad.lote_origen (lote_id, lote_origen_id) VALUES ($1, $2)",
                    lote_id, origen,
                )
            await con.execute(
                "INSERT INTO trazabilidad.auditoria (actor, accion, entidad, entidad_id, payload) "
                "VALUES ($1, 'lote.creado', 'lote_mineral', $2, $3::jsonb)",
                "svc-trazabilidad", str(lote_id), cmd.model_dump_json(),
            )
            fila = await con.fetchrow(
                "SELECT * FROM trazabilidad.lote_mineral WHERE id = $1", lote_id
            )
            return await self._hidratar_lote(con, fila), True

    async def obtener_lote(self, lote_id: UUID) -> Lote:
        async with self._pool.acquire() as con:
            fila = await con.fetchrow(
                "SELECT * FROM trazabilidad.lote_mineral WHERE id = $1", lote_id
            )
            if not fila:
                raise LoteNoExiste(str(lote_id))
            return await self._hidratar_lote(con, fila)

    async def registrar_pesaje(self, cmd: RegistrarPesaje) -> tuple[Pesaje, bool]:
        async with self._pool.acquire() as con:
            existe_lote = await con.fetchval(
                "SELECT 1 FROM trazabilidad.lote_mineral WHERE id = $1", cmd.lote_id
            )
            if not existe_lote:
                raise LoteNoExiste(str(cmd.lote_id))
            insertado = await con.fetchrow(
                "INSERT INTO trazabilidad.pesaje "
                "(event_id, lote_id, movimiento_id, instrumento_id, masa_kg, medido_en) "
                "VALUES ($1, $2, $3, $4, $5, $6) "
                "ON CONFLICT (event_id) DO NOTHING RETURNING event_id",
                cmd.event_id, cmd.lote_id, cmd.movimiento_id,
                cmd.instrumento_id, cmd.masa_kg, cmd.medido_en,
            )
            return Pesaje(**cmd.model_dump()), insertado is not None

    async def pesajes_de(self, lote_id: UUID) -> list[Pesaje]:
        async with self._pool.acquire() as con:
            filas = await con.fetch(
                "SELECT event_id, lote_id, movimiento_id, instrumento_id, masa_kg, medido_en "
                "FROM trazabilidad.pesaje WHERE lote_id = $1 ORDER BY medido_en", lote_id
            )
            return [Pesaje(**dict(f)) for f in filas]

    async def listar_pesajes(self, desde: datetime, hasta: datetime) -> list[Pesaje]:
        async with self._pool.acquire() as con:
            filas = await con.fetch(
                "SELECT event_id, lote_id, movimiento_id, instrumento_id, masa_kg, medido_en "
                "FROM trazabilidad.pesaje WHERE medido_en >= $1 AND medido_en < $2 "
                "ORDER BY medido_en", desde, hasta)
            return [Pesaje(**dict(f)) for f in filas]

    async def listar_lotes(self) -> list[Lote]:
        async with self._pool.acquire() as con:
            filas = await con.fetch(
                "SELECT * FROM trazabilidad.lote_mineral ORDER BY creado_en DESC LIMIT 200"
            )
            return [await self._hidratar_lote(con, f) for f in filas]

    async def guardar_bloques(self, origen_modelo: str, bloques: list[Bloque]) -> int:
        import json

        async with self._pool.acquire() as con, con.transaction():
            for b in bloques:
                await con.execute(
                    """INSERT INTO trazabilidad.bloque_geologico
                       (id, origen_modelo, centroide, cota, leyes_estimadas)
                       VALUES ($1, $2, point($3, $4), $5, $6::jsonb)
                       ON CONFLICT (id) DO UPDATE SET origen_modelo=EXCLUDED.origen_modelo,
                         centroide=EXCLUDED.centroide, cota=EXCLUDED.cota,
                         leyes_estimadas=EXCLUDED.leyes_estimadas,
                         importado_en=now()""",
                    b.id, origen_modelo, b.centroide_x, b.centroide_y, b.cota,
                    json.dumps(b.leyes_estimadas))
        return len(bloques)

    async def _hidratar_lote(self, con, fila) -> Lote:
        origenes = await con.fetch(
            "SELECT lote_origen_id FROM trazabilidad.lote_origen WHERE lote_id = $1", fila["id"]
        )
        return Lote(
            id=fila["id"],
            codigo=fila["codigo"],
            tipo=fila["tipo"],
            estado=EstadoLote(fila["estado"]),
            disparo_id=fila["disparo_id"],
            lotes_origen=[o["lote_origen_id"] for o in origenes],
            creado_en=fila["creado_en"],
        )
