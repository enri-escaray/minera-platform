"""Modelos de dominio y DTOs del servicio de trazabilidad."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


class TipoLote(str, Enum):
    rom = "rom"
    stock = "stock"
    alimentacion_planta = "alimentacion_planta"
    concentrado = "concentrado"
    relave = "relave"


class EstadoLote(str, Enum):
    activo = "activo"
    consumido = "consumido"
    embarcado = "embarcado"
    cerrado = "cerrado"


class CrearLote(BaseModel):
    """Comando de creación. event_id garantiza idempotencia del comando."""
    event_id: UUID = Field(default_factory=uuid4)
    codigo: str = Field(min_length=3, max_length=64)
    tipo: TipoLote
    disparo_id: UUID | None = None
    lotes_origen: list[UUID] = Field(default_factory=list)

    @field_validator("lotes_origen")
    @classmethod
    def sin_duplicados(cls, v: list[UUID]) -> list[UUID]:
        if len(v) != len(set(v)):
            raise ValueError("lotes_origen contiene duplicados")
        return v


class Lote(BaseModel):
    id: UUID
    codigo: str
    tipo: TipoLote
    estado: EstadoLote = EstadoLote.activo
    disparo_id: UUID | None = None
    lotes_origen: list[UUID] = Field(default_factory=list)
    creado_en: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RegistrarPesaje(BaseModel):
    """El event_id es la clave de idempotencia: reintentos no duplican masa."""
    event_id: UUID
    lote_id: UUID
    instrumento_id: str
    masa_kg: float = Field(gt=0)
    medido_en: datetime
    movimiento_id: UUID | None = None


class Pesaje(BaseModel):
    event_id: UUID
    lote_id: UUID
    instrumento_id: str
    masa_kg: float
    medido_en: datetime
    movimiento_id: UUID | None = None


class Bloque(BaseModel):
    """Celda del modelo de bloques importada desde el software geológico."""

    id: str
    centroide_x: float
    centroide_y: float
    cota: float
    leyes_estimadas: dict[str, float]


class ImportarBloques(BaseModel):
    """Comando idempotente de import (contrato bloque.importado).

    El adaptador (tools/convertir_bloques_csv.py) traduce el export de
    Deswik/Vulcan a este formato; el dominio no conoce formatos de vendor (ADR-004).
    """

    event_id: UUID = Field(default_factory=uuid4)
    origen_modelo: str = Field(min_length=1)
    bloques: list[Bloque] = Field(min_length=1)


class CadenaCustodia(BaseModel):
    """Respuesta de la consulta principal: historia completa de un lote."""
    lote: Lote
    pesajes: list[Pesaje]
    masa_total_kg: float
    origenes: list["CadenaCustodia"] = Field(default_factory=list)


class ErrorDominio(Exception):
    """Base de errores de negocio; se mapean a HTTP 4xx explícitos."""


class LoteNoExiste(ErrorDominio):
    pass


class CodigoDuplicado(ErrorDominio):
    pass
