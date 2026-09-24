"""Dominio de reportes: novedades de campo (P2), reporte de turno (P2) y
conciliación de balance de masas (P4)."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Novedad(BaseModel):
    """Registro de campo hecho por un operador (desde la PWA, offline-first).
    event_id lo genera el dispositivo: la resincronización nunca duplica."""

    event_id: UUID = Field(default_factory=uuid4)
    turno: str = Field(pattern="^(dia|noche)$")
    # Con patrón: `fecha` termina en datetime.fromisoformat al armar el reporte,
    # así que una fecha libre es un 500 esperando a pasar.
    fecha: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    categoria: str = Field(pattern="^(seguridad|operacion|equipo|otro)$")
    texto: str = Field(min_length=3, max_length=2000)
    autor: str = Field(default="", max_length=128)
    creado_en: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReporteTurno(BaseModel):
    fecha: str
    turno: str
    desde: datetime
    hasta: datetime
    total_toneladas: float
    cantidad_pesajes: int
    toneladas_por_instrumento: dict[str, float]
    novedades: list[Novedad]
    generado_en: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BalanceLote(BaseModel):
    """Entrada de la conciliación P4: masas medidas de un proceso o traslado."""

    lote_id: str = Field(min_length=1, max_length=64)
    # Cota superior además de gt=0: `float` acepta infinito y NaN se cuela en
    # cualquier comparación; el desvío calculado terminaría en una alerta sin sentido.
    masa_entrada_kg: float = Field(gt=0, le=1e12)
    masa_salida_kg: float = Field(ge=0, le=1e12)


class AlertaConciliacion(BaseModel):
    lote_id: str
    masa_entrada_kg: float
    masa_salida_kg: float
    desvio_pct: float
    detalle: str
