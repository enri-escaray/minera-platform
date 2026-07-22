"""Dominio de despacho: asignación camión → pala.

Consistencia fuerte DENTRO del sitio (ADR-002): el estado de colas por pala es
un único escritor protegido por lock; una asignación es atómica y visible de
inmediato para la siguiente decisión. Esto vive en el edge con latencia local.

Criterio de asignación (v1, determinista y explicable):
    espera_estimada(pala) = viaje_min + en_cola * tiempo_carga_min
Gana la menor espera; empate → mayor prioridad_mezcla; empate → id de pala
(estabilidad total: la misma situación produce siempre la misma decisión,
condición para poder auditar y explicar cada asignación).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class EstadoCamion(str, Enum):
    disponible = "disponible"
    en_ciclo = "en_ciclo"
    mantenimiento = "mantenimiento"


class Pala(BaseModel):
    id: str
    ubicacion_id: str
    tiempo_carga_min: float = Field(gt=0)
    prioridad_mezcla: int = Field(ge=0, default=0, description="mayor = más urgente para el plan de mezcla")
    activa: bool = True


class Camion(BaseModel):
    id: str
    capacidad_ton: float = Field(gt=0)
    estado: EstadoCamion = EstadoCamion.disponible


class Asignacion(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    camion_id: str
    pala_id: str
    espera_estimada_min: float
    motivo: str
    creada_en: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ErrorDominio(Exception):
    pass


class EquipoNoExiste(ErrorDominio):
    pass


class CamionNoDisponible(ErrorDominio):
    pass


class SinPalasDisponibles(ErrorDominio):
    pass


def elegir_pala(
    palas: list[Pala],
    en_cola: dict[str, int],
    viaje_min: dict[str, float],
) -> tuple[Pala, float, str]:
    """Función pura: decide la mejor pala. Retorna (pala, espera, motivo legible)."""
    candidatas = [p for p in palas if p.activa]
    if not candidatas:
        raise SinPalasDisponibles("no hay palas activas")

    def clave(p: Pala) -> tuple[float, int, str]:
        espera = viaje_min.get(p.id, 0.0) + en_cola.get(p.id, 0) * p.tiempo_carga_min
        return (espera, -p.prioridad_mezcla, p.id)

    elegida = min(candidatas, key=clave)
    espera = viaje_min.get(elegida.id, 0.0) + en_cola.get(elegida.id, 0) * elegida.tiempo_carga_min
    motivo = (
        f"espera estimada {espera:.1f} min "
        f"(viaje {viaje_min.get(elegida.id, 0.0):.1f} + "
        f"{en_cola.get(elegida.id, 0)} en cola x {elegida.tiempo_carga_min:.1f} min de carga); "
        f"prioridad de mezcla {elegida.prioridad_mezcla}"
    )
    return elegida, espera, motivo


class Despachador:
    """Estado del rajo en memoria del edge, escritor único con lock.

    La matriz de viaje (camión→pala) llega de telemetría/plan; acá se recibe ya
    calculada por ubicación. Cuando un camión descarga, `liberar` decrementa la
    cola de su pala y lo vuelve disponible.
    """

    def __init__(self, persistencia=None) -> None:
        from .persistencia import NulaPersistencia

        self._lock = asyncio.Lock()
        self._persistencia = persistencia or NulaPersistencia()
        self._palas: dict[str, Pala] = {}
        self._camiones: dict[str, Camion] = {}
        self._en_cola: dict[str, int] = {}
        self._asignacion_de: dict[str, Asignacion] = {}  # camion_id -> asignación viva

    async def restaurar(self) -> None:
        """Reconstruye el estado del rajo desde la base al reiniciar: palas,
        camiones y colas derivadas de las asignaciones vivas. Tolerancia a
        fallos del servicio sin perder el turno en curso."""
        palas, camiones, vivas = await self._persistencia.cargar()
        async with self._lock:
            for pala in palas:
                self._palas[pala.id] = pala
                self._en_cola.setdefault(pala.id, 0)
            for camion in camiones:
                self._camiones[camion.id] = camion
            for asignacion in vivas:
                self._asignacion_de[asignacion.camion_id] = asignacion
                self._en_cola[asignacion.pala_id] = self._en_cola.get(asignacion.pala_id, 0) + 1

    async def registrar_pala(self, pala: Pala) -> None:
        async with self._lock:
            self._palas[pala.id] = pala
            self._en_cola.setdefault(pala.id, 0)
        await self._persistencia.guardar_pala(pala)

    async def registrar_camion(self, camion: Camion) -> None:
        async with self._lock:
            self._camiones[camion.id] = camion
        await self._persistencia.guardar_camion(camion)

    async def marcar_estado_camion(self, camion_id: str, estado: EstadoCamion) -> Camion:
        async with self._lock:
            camion = self._camion(camion_id)
            camion.estado = estado
        await self._persistencia.guardar_camion(camion)
        return camion

    async def asignar(self, camion_id: str, viaje_min: dict[str, float]) -> Asignacion:
        async with self._lock:
            camion = self._camion(camion_id)
            if camion.estado != EstadoCamion.disponible:
                raise CamionNoDisponible(f"{camion_id} está {camion.estado.value}")
            pala, espera, motivo = elegir_pala(
                list(self._palas.values()), self._en_cola, viaje_min
            )
            asignacion = Asignacion(
                camion_id=camion_id, pala_id=pala.id,
                espera_estimada_min=espera, motivo=motivo,
            )
            self._en_cola[pala.id] += 1
            camion.estado = EstadoCamion.en_ciclo
            self._asignacion_de[camion_id] = asignacion
        await self._persistencia.guardar_asignacion(asignacion)
        await self._persistencia.guardar_camion(camion)
        return asignacion

    async def liberar(self, camion_id: str) -> Camion:
        """El camión descargó: sale de la cola de su pala y vuelve a estar disponible."""
        async with self._lock:
            camion = self._camion(camion_id)
            asignacion = self._asignacion_de.pop(camion_id, None)
            if asignacion is not None:
                self._en_cola[asignacion.pala_id] = max(0, self._en_cola[asignacion.pala_id] - 1)
            camion.estado = EstadoCamion.disponible
        await self._persistencia.marcar_liberada(camion_id)
        await self._persistencia.guardar_camion(camion)
        return camion

    async def estado(self) -> dict:
        async with self._lock:
            return {
                "palas": [p.model_dump() for p in self._palas.values()],
                "camiones": [c.model_dump() for c in self._camiones.values()],
                "en_cola": dict(self._en_cola),
                "asignaciones_vivas": len(self._asignacion_de),
            }

    def _camion(self, camion_id: str) -> Camion:
        try:
            return self._camiones[camion_id]
        except KeyError as exc:
            raise EquipoNoExiste(camion_id) from exc
