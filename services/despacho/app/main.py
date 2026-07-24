"""API del servicio de despacho. Escrituras con RBAC; cada asignación publica
`asignacion.creada` a la outbox y queda auditada con su motivo explicable."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from minera_comun import Principal, auditar, publicar_evento, requerir_rol

from .models import (
    Asignacion,
    Camion,
    CamionNoDisponible,
    Despachador,
    EquipoNoExiste,
    ErrorDominio,
    EstadoCamion,
    Pala,
    SinPalasDisponibles,
)


import os


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = None
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        import asyncpg

        from .persistencia import PostgresPersistencia

        app.state.pool = await asyncpg.create_pool(database_url, min_size=1, max_size=10)
        app.state.despachador = Despachador(PostgresPersistencia(app.state.pool))
        await app.state.despachador.restaurar()
    else:
        app.state.despachador = Despachador()
    yield
    if app.state.pool:
        await app.state.pool.close()


app = FastAPI(title="Despacho", version="0.1.0", lifespan=lifespan)

# Panel de sitio (apps/panel): solo consultas, mismo patrón de CORS que reportes.
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:8081").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET"],
    allow_headers=["Authorization", "Content-Type"],
)

_STATUS = {EquipoNoExiste: 404, CamionNoDisponible: 409, SinPalasDisponibles: 409}


@app.exception_handler(ErrorDominio)
async def dominio_handler(_, exc: ErrorDominio):
    return JSONResponse(
        status_code=_STATUS.get(type(exc), 400),
        content={"error": type(exc).__name__, "detalle": str(exc)},
    )


def d() -> Despachador:
    return app.state.despachador


@app.get("/health")
async def health():
    return {"status": "ok", "asignaciones_vivas": (await d().estado())["asignaciones_vivas"]}


@app.get("/estado")
async def estado():
    return await d().estado()


@app.post("/palas", response_model=Pala, status_code=201)
async def registrar_pala(pala: Pala, _: Principal = Depends(requerir_rol("despacho.configurar"))):
    await d().registrar_pala(pala)
    return pala


@app.post("/camiones", response_model=Camion, status_code=201)
async def registrar_camion(camion: Camion, _: Principal = Depends(requerir_rol("despacho.configurar"))):
    await d().registrar_camion(camion)
    return camion


class SolicitudAsignacion(BaseModel):
    camion_id: str
    viaje_min: dict[str, float] = Field(
        default_factory=dict, description="minutos de viaje estimados del camión a cada pala"
    )


@app.post("/asignaciones", response_model=Asignacion, status_code=201)
async def asignar(
    solicitud: SolicitudAsignacion,
    principal: Principal = Depends(requerir_rol("despacho.operar")),
):
    asignacion = await d().asignar(solicitud.camion_id, solicitud.viaje_min)
    publicar_evento(
        "asignacion.creada",
        {
            "asignacion_id": str(asignacion.id),
            "camion_id": asignacion.camion_id,
            "pala_id": asignacion.pala_id,
            "espera_estimada_min": asignacion.espera_estimada_min,
            "motivo": asignacion.motivo,
        },
    )
    auditar(principal.sub, "asignacion.creada", "asignacion", str(asignacion.id),
            {"camion": asignacion.camion_id, "pala": asignacion.pala_id})
    return asignacion


@app.post("/camiones/{camion_id}/liberar", response_model=Camion)
async def liberar(camion_id: str, _: Principal = Depends(requerir_rol("despacho.operar"))):
    return await d().liberar(camion_id)


@app.post("/camiones/{camion_id}/mantenimiento", response_model=Camion)
async def a_mantenimiento(camion_id: str, _: Principal = Depends(requerir_rol("despacho.operar"))):
    """Saca el camión del ciclo (p. ej. al agendarse una OT crítica sobre él)."""
    return await d().marcar_estado_camion(camion_id, EstadoCamion.mantenimiento)
