"""Servicio de trazabilidad — API de cadena de custodia del mineral.

Arranca con repositorio en memoria si no hay DATABASE_URL (tests/dev),
o con PostgresRepo si la variable existe. La publicación a Kafka es
best-effort con buffer local: si el broker no está, el evento queda en
la outbox y se reintenta (patrón transactional outbox simplificado).
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from minera_comun import Principal, publicar_evento, requerir_rol

from datetime import datetime

from .models import (
    ImportarBloques,
    CadenaCustodia,
    CodigoDuplicado,
    CrearLote,
    ErrorDominio,
    Lote,
    LoteNoExiste,
    Pesaje,
    RegistrarPesaje,
)
from .repo import InMemoryRepo, Repo

SITIO_ID = os.getenv("SITIO_ID", "sitio-dev")


@asynccontextmanager
async def lifespan(app: FastAPI):
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        import asyncpg
        from .repo import PostgresRepo

        pool = await asyncpg.create_pool(database_url, min_size=1, max_size=10)
        app.state.repo = PostgresRepo(pool)
        yield
        await pool.close()
    else:
        app.state.repo = InMemoryRepo()
        yield


app = FastAPI(title="Trazabilidad", version="0.1.0", lifespan=lifespan)

# Panel de sitio (apps/panel): solo consultas, mismo patrón de CORS que reportes.
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:8081").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(ErrorDominio)
async def dominio_handler(_, exc: ErrorDominio):
    status = 404 if isinstance(exc, LoteNoExiste) else 409
    return JSONResponse(status_code=status, content={"error": type(exc).__name__, "detalle": str(exc)})


def repo() -> Repo:
    return app.state.repo


@app.get("/health")
async def health():
    return {"status": "ok", "sitio": SITIO_ID}


@app.post("/lotes", response_model=Lote, status_code=201)
async def crear_lote(cmd: CrearLote, _: Principal = Depends(requerir_rol("trazabilidad.escribir"))):
    lote, nuevo = await repo().crear_lote(cmd)
    if nuevo:
        publicar_evento(
            "lote.creado",
            {
                "lote_id": str(lote.id),
                "codigo": lote.codigo,
                "tipo": lote.tipo.value,
                "disparo_id": str(lote.disparo_id) if lote.disparo_id else None,
                "lotes_origen": [str(x) for x in lote.lotes_origen],
            },
        )
    return lote


@app.post("/pesajes", response_model=Pesaje, status_code=201)
async def registrar_pesaje(cmd: RegistrarPesaje, _: Principal = Depends(requerir_rol("trazabilidad.escribir"))):
    pesaje, nuevo = await repo().registrar_pesaje(cmd)
    if nuevo:
        publicar_evento(
            "pesaje.registrado",
            {
                "lote_id": str(pesaje.lote_id),
                "instrumento_id": pesaje.instrumento_id,
                "masa_kg": pesaje.masa_kg,
                "medido_en": pesaje.medido_en.isoformat(),
            },
        )
    return pesaje


@app.post("/bloques/importar", status_code=201)
async def importar_bloques(
    cmd: ImportarBloques, _: Principal = Depends(requerir_rol("geologia.importar"))
):
    """Import del modelo de bloques (ADR-004: el formato del vendor se traduce
    afuera, en tools/convertir_bloques_csv.py; acá entra el contrato neutro)."""
    cantidad = await repo().guardar_bloques(cmd.origen_modelo, cmd.bloques)
    publicar_evento(
        "bloque.importado",
        {"origen_modelo": cmd.origen_modelo, "cantidad": cantidad,
         "comando_event_id": str(cmd.event_id)},
    )
    return {"importados": cantidad, "origen_modelo": cmd.origen_modelo}


@app.get("/lotes", response_model=list[Lote])
async def listar_lotes():
    """Listado para el panel de sitio (más recientes primero, tope 200)."""
    return await repo().listar_lotes()


@app.get("/pesajes", response_model=list[Pesaje])
async def listar_pesajes(desde: datetime, hasta: datetime):
    """Consulta por rango: la usa `reportes` para el reporte de turno (P2)."""
    return await repo().listar_pesajes(desde, hasta)


MAX_PROFUNDIDAD = 6  # límite defensivo para el grafo de custodia


async def _custodia(lote_id: UUID, profundidad: int) -> CadenaCustodia:
    lote = await repo().obtener_lote(lote_id)
    pesajes = await repo().pesajes_de(lote_id)
    origenes: list[CadenaCustodia] = []
    if profundidad < MAX_PROFUNDIDAD:
        for origen_id in lote.lotes_origen:
            origenes.append(await _custodia(origen_id, profundidad + 1))
    return CadenaCustodia(
        lote=lote,
        pesajes=pesajes,
        masa_total_kg=sum(p.masa_kg for p in pesajes),
        origenes=origenes,
    )


@app.get("/lotes/{lote_id}/custodia", response_model=CadenaCustodia)
async def cadena_custodia(lote_id: UUID):
    """Historia completa del lote: pesajes propios + recursión sobre sus orígenes."""
    return await _custodia(lote_id, 0)
