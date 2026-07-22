"""Dominio de mantenimiento: reglas predictivas y órdenes de trabajo.

Regla del ADR y de docs/procesos-automatizados.md (P1): el evaluador SUGIERE.
Ninguna OT detiene un equipo por sí sola; el cierre y el agendamiento son
acciones humanas autenticadas.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Prioridad(str, Enum):
    critica = "critica"
    alta = "alta"
    media = "media"
    baja = "baja"


class EstadoOT(str, Enum):
    abierta = "abierta"
    agendada = "agendada"
    cerrada = "cerrada"


class Condicion(str, Enum):
    mayor = "mayor"
    menor = "menor"


def _cumple(valor: float, umbral: float, condicion: Condicion) -> bool:
    return valor > umbral if condicion == Condicion.mayor else valor < umbral


class UmbralCondicion(BaseModel):
    umbral: float
    condicion: Condicion = Condicion.mayor


class ReglaPredictiva(BaseModel):
    """Umbral con ventana de persistencia: dispara solo si `ventana_n` lecturas
    CONSECUTIVAS de la métrica principal cumplen la condición (mayor o menor
    que el umbral). Un pico aislado de sensor no genera OT.

    Reglas compuestas: `condiciones_adicionales` exige que, al momento del
    disparo, el último valor conocido de otras métricas del MISMO equipo
    también cumpla su condición (p. ej. sobrecarga sostenida Y alta velocidad).
    Si falta contexto o no se cumple, la racha se mantiene y se reevalúa con
    la próxima lectura: no se pierde el disparo, se posterga."""

    id: str
    metrica: str
    umbral: float
    condicion: Condicion = Condicion.mayor
    condiciones_adicionales: dict[str, UmbralCondicion] = Field(default_factory=dict)
    ventana_n: int = Field(ge=1, default=3)
    prioridad: Prioridad
    diagnostico: str
    repuestos_sugeridos: list[str] = Field(default_factory=list)


class OrdenTrabajo(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    equipo_id: str
    regla_id: str
    origen: str = "predictivo"
    prioridad: Prioridad
    estado: EstadoOT = EstadoOT.abierta
    diagnostico: str
    repuestos_sugeridos: list[str] = Field(default_factory=list)
    valor_disparo: float
    creada_en: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    cerrada_en: datetime | None = None
    cerrada_por: str | None = None
    resolucion: str | None = None


class Evaluador:
    """Mantiene el estado de ventanas por (equipo, métrica) y decide disparos.

    Antirrebote de negocio: mientras exista una OT no cerrada para
    (equipo, regla), la regla no vuelve a disparar. Al cerrarse la OT,
    la ventana se resetea y el equipo vuelve a estar vigilado desde cero.
    """

    def __init__(self, reglas: list[ReglaPredictiva]) -> None:
        self._reglas_por_metrica: dict[str, list[ReglaPredictiva]] = defaultdict(list)
        for regla in reglas:
            self._reglas_por_metrica[regla.metrica].append(regla)
        self._ventanas: dict[tuple[str, str], deque[float]] = {}
        self._ultimo_valor: dict[tuple[str, str], float] = {}  # (equipo, metrica) -> valor
        self._suprimidas: set[tuple[str, str]] = set()  # (equipo_id, regla_id) con OT viva

    def procesar_medida(self, equipo_id: str, metrica: str, valor: float) -> list[OrdenTrabajo]:
        """Retorna las OTs que esta medida dispara (normalmente 0 o 1)."""
        self._ultimo_valor[(equipo_id, metrica)] = valor
        disparos: list[OrdenTrabajo] = []
        for regla in self._reglas_por_metrica.get(metrica, []):
            clave = (equipo_id, regla.id)
            if clave in self._suprimidas:
                continue
            ventana = self._ventanas.setdefault(clave, deque(maxlen=regla.ventana_n))
            if _cumple(valor, regla.umbral, regla.condicion):
                ventana.append(valor)
            else:
                ventana.clear()  # una lectura sana corta la racha
            if len(ventana) == regla.ventana_n and self._contexto_cumplido(equipo_id, regla):
                disparos.append(
                    OrdenTrabajo(
                        equipo_id=equipo_id,
                        regla_id=regla.id,
                        prioridad=regla.prioridad,
                        diagnostico=regla.diagnostico,
                        repuestos_sugeridos=regla.repuestos_sugeridos,
                        valor_disparo=valor,
                    )
                )
                self._suprimidas.add(clave)
                ventana.clear()
        return disparos

    def _contexto_cumplido(self, equipo_id: str, regla: ReglaPredictiva) -> bool:
        """Reglas compuestas: todas las métricas de contexto deben tener un último
        valor conocido que cumpla su condición. Sin contexto conocido, no dispara."""
        for metrica, uc in regla.condiciones_adicionales.items():
            valor = self._ultimo_valor.get((equipo_id, metrica))
            if valor is None or not _cumple(valor, uc.umbral, uc.condicion):
                return False
        return True

    def suprimir(self, equipo_id: str, regla_id: str) -> None:
        """Reconstrucción al arrancar: cada OT viva en la base suprime su regla."""
        self._suprimidas.add((equipo_id, regla_id))

    def habilitar(self, equipo_id: str, regla_id: str) -> None:
        """Se llama al cerrar la OT: la regla vuelve a vigilar el equipo."""
        self._suprimidas.discard((equipo_id, regla_id))


class ErrorDominio(Exception):
    pass


class OTNoExiste(ErrorDominio):
    pass


class OTYaCerrada(ErrorDominio):
    pass
