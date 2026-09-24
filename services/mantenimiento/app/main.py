"""Servicio de mantenimiento predictivo (flujo P1).

Consume `telemetria.equipos`, evalúa reglas por equipo y publica `ot.generada`
en la outbox común (→ relay → Kafka). Expone API para listar, agendar y cerrar
OTs; cerrar exige rol y queda auditado.

Puertos y adaptadores: `FuenteEventos` abstrae Kafka; los tests inyectan eventos
directamente. Repositorio en memoria detrás de interfaz (persistencia Postgres:
pendiente en roadmap, misma técnica que trazabilidad).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Protocol
from uuid import UUID

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from minera_comun import (
    Principal,
    auditar,
    publicar_evento,
    registrar_errores_de_validacion,
    requerir_lectura,
    requerir_rol,
    verificar_configuracion,
)

from .models import (
    ErrorDominio,
    EstadoOT,
    Evaluador,
    OrdenTrabajo,
    OTNoExiste,
    OTYaCerrada,
    ReglaPredictiva,
)
from .repo import InMemoryRepoOTs, PostgresRepoOTs, RepoOTs

log = logging.getLogger("mantenimiento")

REGLAS_PATH = os.getenv("REGLAS_PATH", os.path.join(os.path.dirname(__file__), "reglas.json"))
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "")
TOPICO_TELEMETRIA = "telemetria.equipos"


def cargar_reglas(path: str) -> list[ReglaPredictiva]:
    with open(path, encoding="utf-8") as f:
        return [ReglaPredictiva(**r) for r in json.load(f)]


async def procesar_evento_telemetria(evento: dict, evaluador: Evaluador, repo: RepoOTs) -> list[OrdenTrabajo]:
    """Núcleo del flujo P1, puro respecto de infraestructura: evalúa y persiste.

    Idempotente ante replays de Kafka: el antirrebote del evaluador impide
    OTs duplicadas para el mismo (equipo, regla) mientras haya una viva.
    """
    data = evento.get("data", {})
    try:
        equipo_id = str(data["equipo_id"])
        metrica = str(data["metrica"])
        valor = float(data["valor"])
    except (KeyError, TypeError, ValueError):
        return []  # evento malformado: se ignora, no se rompe el consumidor
    nuevas = evaluador.procesar_medida(equipo_id, metrica, valor)
    for ot in nuevas:
        await repo.guardar(ot)
        publicar_evento(
            "ot.generada",
            {
                "ot_id": str(ot.id),
                "equipo_id": ot.equipo_id,
                "origen": ot.origen,
                "prioridad": ot.prioridad.value,
                "diagnostico": ot.diagnostico,
                "repuestos_sugeridos": ot.repuestos_sugeridos,
            },
            trace_id=evento.get("trace_id"),
        )
        log.info("OT %s generada para %s (%s)", ot.id, ot.equipo_id, ot.regla_id)
    return nuevas


class FuenteEventos(Protocol):
    async def consumir(self) -> dict: ...


class KafkaFuente:
    """Adaptador aiokafka con import perezoso; grupo propio para offset committeado."""

    def __init__(self, bootstrap: str, topico: str) -> None:
        self._bootstrap, self._topico, self._consumer = bootstrap, topico, None

    async def consumir(self) -> dict:
        if self._consumer is None:
            from aiokafka import AIOKafkaConsumer

            consumer = AIOKafkaConsumer(
                self._topico,
                bootstrap_servers=self._bootstrap,
                group_id="mantenimiento",
                enable_auto_commit=True,
                auto_offset_reset="earliest",
            )
            try:
                await consumer.start()
            except Exception:
                # Broker aún no disponible (p. ej. arranque del sitio): dejar
                # _consumer en None para que el próximo intento vuelva a start().
                # Si quedara asignado sin arrancar, todos los getone() siguientes
                # fallarían para siempre con el coordinador en None.
                await consumer.stop()
                raise
            self._consumer = consumer
        msg = await self._consumer.getone()
        return json.loads(msg.value)


async def bucle_consumidor(fuente: FuenteEventos, evaluador: Evaluador, repo: RepoOTs) -> None:
    while True:
        try:
            evento = await fuente.consumir()
            await procesar_evento_telemetria(evento, evaluador, repo)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — el consumidor no muere por un evento malo
            log.exception("error procesando evento; se continúa")
            await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fallo cerrado: con auth mal configurada el servicio no llega a 'ready'.
    verificar_configuracion()
    app.state.evaluador = Evaluador(cargar_reglas(REGLAS_PATH))
    app.state.pool = None
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        import asyncpg

        app.state.pool = await asyncpg.create_pool(database_url, min_size=1, max_size=10)
        app.state.repo = PostgresRepoOTs(app.state.pool)
        # Tolerancia a fallos: al reiniciar, las OTs vivas en la base
        # reconstruyen el antirrebote del evaluador (nada se dispara doble).
        for ot in await app.state.repo.vivas():
            app.state.evaluador.suprimir(ot.equipo_id, ot.regla_id)
    else:
        app.state.repo = InMemoryRepoOTs()
    tarea = None
    if KAFKA_BOOTSTRAP:
        fuente = KafkaFuente(KAFKA_BOOTSTRAP, TOPICO_TELEMETRIA)
        tarea = asyncio.create_task(bucle_consumidor(fuente, app.state.evaluador, app.state.repo))
    yield
    if tarea:
        tarea.cancel()
    if app.state.pool:
        await app.state.pool.close()


app = FastAPI(title="Mantenimiento", version="0.1.0", lifespan=lifespan)
registrar_errores_de_validacion(app)

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
    status = 404 if isinstance(exc, OTNoExiste) else 409
    return JSONResponse(status_code=status, content={"error": type(exc).__name__, "detalle": str(exc)})


@app.get("/health")
async def health():
    return {"status": "ok", "ots_abiertas": len(await app.state.repo.listar(EstadoOT.abierta))}


@app.get("/ots", response_model=list[OrdenTrabajo])
async def listar_ots(
    estado: EstadoOT | None = None,
    _=Depends(requerir_lectura("mantenimiento.leer", "mantenimiento.planificar", "mantenimiento.cerrar")),
):
    return await app.state.repo.listar(estado)


@app.post("/ots/{ot_id}/agendar", response_model=OrdenTrabajo)
async def agendar_ot(
    ot_id: UUID, principal: Principal = Depends(requerir_rol("mantenimiento.planificar"))
):
    ot = await app.state.repo.obtener(ot_id)
    if ot.estado == EstadoOT.cerrada:
        raise OTYaCerrada(str(ot_id))
    ot.estado = EstadoOT.agendada
    await app.state.repo.guardar(ot)
    auditar(principal.sub, "ot.agendada", "orden_trabajo", str(ot.id), {})
    return ot


class CerrarOT(BaseModel):
    """Cuerpo mínimo del cierre; la resolución escrita es obligatoria."""

    resolucion: str = Field(min_length=1, max_length=4000)


@app.post("/ots/{ot_id}/cerrar", response_model=OrdenTrabajo)
async def cerrar_ot(
    ot_id: UUID,
    cuerpo: CerrarOT,
    principal: Principal = Depends(requerir_rol("mantenimiento.cerrar")),
):
    """Cierre humano y autenticado (regla P1). Rehabilita la vigilancia del equipo."""
    ot = await app.state.repo.obtener(ot_id)
    if ot.estado == EstadoOT.cerrada:
        raise OTYaCerrada(str(ot_id))
    ot.estado = EstadoOT.cerrada
    ot.cerrada_en = datetime.now(timezone.utc)
    ot.cerrada_por = principal.sub
    ot.resolucion = cuerpo.resolucion
    await app.state.repo.guardar(ot)
    app.state.evaluador.habilitar(ot.equipo_id, ot.regla_id)
    publicar_evento(
        "ot.cerrada",
        {"ot_id": str(ot.id), "equipo_id": ot.equipo_id, "resolucion": ot.resolucion},
    )
    auditar(principal.sub, "ot.cerrada", "orden_trabajo", str(ot.id), {"resolucion": ot.resolucion})
    return ot
