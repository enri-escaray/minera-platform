"""Servicio de reportes — flujos P2 (reporte de turno) y P4 (conciliación).

Puertos y adaptadores: `FuentePesajes` abstrae la consulta REST a trazabilidad
(REST está permitido para consultas por ADR-003; la integración de escritura
sigue siendo solo por eventos). El scheduler genera el reporte al cierre de
cada turno y lo publica a la outbox; si el enlace está caído, viaja al
reconectar como cualquier evento (P2 del doc de procesos).
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID

from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from minera_comun import (
    Principal,
    auditar,
    publicar_evento,
    registrar_errores_de_validacion,
    requerir_lectura,
    requerir_rol,
    verificar_configuracion,
)

from .models import AlertaConciliacion, BalanceLote, Novedad, ReporteTurno
from .nucleo import conciliar, generar_reporte, rango_turno

log = logging.getLogger("reportes")

TRAZABILIDAD_URL = os.getenv("TRAZABILIDAD_URL", "http://trazabilidad:8000")
TOLERANCIA_CONCILIACION_PCT = float(os.getenv("TOLERANCIA_CONCILIACION_PCT", "2.0"))
# Orígenes de la PWA de campo autorizados a llamar este servicio (CSV en env).
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:8080,http://localhost:8081").split(",") if o.strip()]


class FuentePesajes(Protocol):
    async def pesajes(self, desde: datetime, hasta: datetime) -> list[dict]: ...


class HttpFuentePesajes:
    """Consulta el endpoint de rango de trazabilidad (import perezoso de httpx).

    Llamada servicio→servicio: cuando trazabilidad exige token en las consultas
    (AUTH_MODE=oidc), este servicio debe presentar su propia credencial. Se toma
    de SERVICIO_TOKEN; en dev no hace falta porque la lectura queda abierta.
    """

    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")

    async def pesajes(self, desde: datetime, hasta: datetime) -> list[dict]:
        import httpx

        token = os.getenv("SERVICIO_TOKEN")
        cabeceras = {"Authorization": f"Bearer {token}"} if token else {}
        async with httpx.AsyncClient(timeout=10) as cliente:
            r = await cliente.get(
                f"{self._base}/pesajes",
                params={"desde": desde.isoformat(), "hasta": hasta.isoformat()},
                headers=cabeceras,
            )
            r.raise_for_status()
            return r.json()


MAX_NOVEDADES_EN_MEMORIA = 10_000


class RepoNovedades:
    """En memoria, idempotente por event_id (persistencia Postgres: misma técnica
    que trazabilidad si se necesita retención larga; el evento ya viaja a nube).

    Acotado en tamaño: sin techo, un dispositivo de campo con event_id nuevo en
    cada reintento hace crecer el diccionario hasta voltear el proceso. Se
    descartan las más antiguas — el evento ya viajó a nube, que es la copia
    durable; esto es solo la ventana para el reporte del turno en curso.
    """

    def __init__(self, maximo: int = MAX_NOVEDADES_EN_MEMORIA) -> None:
        self._novedades: OrderedDict[UUID, Novedad] = OrderedDict()
        self._maximo = maximo

    def guardar(self, novedad: Novedad) -> bool:
        if novedad.event_id in self._novedades:
            return False
        self._novedades[novedad.event_id] = novedad
        while len(self._novedades) > self._maximo:
            self._novedades.popitem(last=False)
        return True

    def del_turno(self, fecha: str, turno: str) -> list[Novedad]:
        return [n for n in self._novedades.values() if n.fecha == fecha and n.turno == turno]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fallo cerrado: con auth mal configurada el servicio no llega a 'ready'.
    verificar_configuracion()
    app.state.fuente = HttpFuentePesajes(TRAZABILIDAD_URL)
    app.state.novedades = RepoNovedades()
    tarea = None
    if os.getenv("SCHEDULER", "1") == "1":
        tarea = asyncio.create_task(bucle_cierre_de_turno(app))
    yield
    if tarea:
        tarea.cancel()


app = FastAPI(title="Reportes", version="0.1.0", lifespan=lifespan)
registrar_errores_de_validacion(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/novedades", response_model=Novedad, status_code=201)
async def registrar_novedad(
    novedad: Novedad, principal: Principal = Depends(requerir_rol("campo.reportar"))
):
    """Entrada de la PWA de campo. Idempotente: la resincronización offline
    reenvía con el mismo event_id y no duplica."""
    novedad.autor = principal.sub
    if app.state.novedades.guardar(novedad):
        publicar_evento(
            "novedad.registrada",
            {"novedad_id": str(novedad.event_id), "turno": novedad.turno,
             "fecha": novedad.fecha, "categoria": novedad.categoria,
             "texto": novedad.texto, "autor": novedad.autor},
        )
    return novedad


@app.get("/reportes/turno", response_model=ReporteTurno)
async def reporte_de_turno(
    fecha: str,
    turno: str,
    _=Depends(requerir_lectura("reportes.leer", "campo.reportar", "metalurgia.conciliar")),
):
    """P2 bajo demanda: siempre disponible en sitio, con o sin enlace."""
    if turno not in ("dia", "noche"):
        raise HTTPException(status_code=422, detail="turno debe ser 'dia' o 'noche'")
    try:
        # Validación explícita: `fecha` va directo a fromisoformat y una fecha
        # basura devolvía 500 (error del servidor) en vez de 422 (error del cliente).
        desde, hasta = rango_turno(fecha, turno)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="fecha debe ser YYYY-MM-DD") from exc
    try:
        pesajes = await app.state.fuente.pesajes(desde, hasta)
    except Exception as exc:  # noqa: BLE001 — trazabilidad caída no debe tirar reportes
        raise HTTPException(status_code=503, detail=f"trazabilidad no disponible: {exc}") from exc
    return generar_reporte(fecha, turno, pesajes, app.state.novedades.del_turno(fecha, turno))


MAX_BALANCES_POR_CONCILIACION = 5_000


@app.post("/conciliaciones", response_model=list[AlertaConciliacion])
async def ejecutar_conciliacion(
    balances: list[BalanceLote] = Body(max_length=MAX_BALANCES_POR_CONCILIACION),
    principal: Principal = Depends(requerir_rol("metalurgia.conciliar")),
):
    """P4: balance de masas por lote. Cada alerta se publica y se audita;
    ninguna bloquea la operación."""
    alertas = conciliar(balances, TOLERANCIA_CONCILIACION_PCT)
    for alerta in alertas:
        publicar_evento("conciliacion.alerta", alerta.model_dump())
    auditar(principal.sub, "conciliacion.ejecutada", "conciliacion", "diaria",
            {"lotes": len(balances), "alertas": len(alertas)})
    return alertas


async def bucle_cierre_de_turno(app: FastAPI) -> None:
    """Al pasar las 07:00 y las 19:00 (hora del sitio, UTC en dev) genera y
    publica el reporte del turno que terminó."""
    ultimo_generado: str | None = None
    while True:
        ahora = datetime.now(timezone.utc)
        if ahora.hour == 7:
            fecha, turno = (ahora - timedelta(days=1)).strftime("%Y-%m-%d"), "noche"
        elif ahora.hour == 19:
            fecha, turno = ahora.strftime("%Y-%m-%d"), "dia"
        else:
            fecha = turno = ""
        clave = f"{fecha}-{turno}"
        if turno and clave != ultimo_generado:
            try:
                desde, hasta = rango_turno(fecha, turno)
                pesajes = await app.state.fuente.pesajes(desde, hasta)
                reporte = generar_reporte(
                    fecha, turno, pesajes, app.state.novedades.del_turno(fecha, turno))
                publicar_evento(
                    "reporte.turno.generado",
                    {"fecha": fecha, "turno": turno,
                     "total_toneladas": reporte.total_toneladas,
                     "cantidad_pesajes": reporte.cantidad_pesajes},
                )
                ultimo_generado = clave
                log.info("reporte de turno %s publicado", clave)
            except Exception:  # noqa: BLE001 — reintenta en el próximo ciclo
                log.exception("no se pudo generar el reporte %s; se reintenta", clave)
        await asyncio.sleep(60)
