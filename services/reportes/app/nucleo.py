"""Funciones puras de los flujos P2 y P4: sin red, sin reloj, sin estado.

Toda la lógica de negocio testeable vive acá; main.py solo orquesta.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from .models import AlertaConciliacion, BalanceLote, Novedad, ReporteTurno

# Turnos de 12 horas: día 07:00-19:00, noche 19:00-07:00 (configurable a futuro)
INICIO_DIA = time(7, 0)
INICIO_NOCHE = time(19, 0)


def rango_turno(fecha: str, turno: str) -> tuple[datetime, datetime]:
    base = datetime.fromisoformat(fecha).replace(tzinfo=timezone.utc)
    if turno == "dia":
        return base.replace(hour=7), base.replace(hour=19)
    return base.replace(hour=19), (base + timedelta(days=1)).replace(hour=7)


def generar_reporte(
    fecha: str, turno: str, pesajes: list[dict], novedades: list[Novedad]
) -> ReporteTurno:
    """P2: agrega los pesajes del rango y adjunta las novedades del turno.
    Cero digitación manual: todo sale de datos ya capturados."""
    desde, hasta = rango_turno(fecha, turno)
    por_instrumento: dict[str, float] = {}
    total = 0.0
    for pesaje in pesajes:
        masa_ton = float(pesaje["masa_kg"]) / 1000.0
        total += masa_ton
        instrumento = str(pesaje["instrumento_id"])
        por_instrumento[instrumento] = por_instrumento.get(instrumento, 0.0) + masa_ton
    return ReporteTurno(
        fecha=fecha, turno=turno, desde=desde, hasta=hasta,
        total_toneladas=round(total, 2), cantidad_pesajes=len(pesajes),
        toneladas_por_instrumento={k: round(v, 2) for k, v in por_instrumento.items()},
        novedades=sorted(novedades, key=lambda n: n.creado_en),
    )


def conciliar(balances: list[BalanceLote], tolerancia_pct: float = 2.0) -> list[AlertaConciliacion]:
    """P4: balance de masas por lote. Desvío sobre tolerancia = alerta, no bloqueo
    (la báscula puede fallar; la operación no se detiene por software)."""
    alertas: list[AlertaConciliacion] = []
    for balance in balances:
        desvio = abs(balance.masa_salida_kg - balance.masa_entrada_kg)
        desvio_pct = round(100.0 * desvio / balance.masa_entrada_kg, 2)
        if desvio_pct > tolerancia_pct:
            direccion = "faltante" if balance.masa_salida_kg < balance.masa_entrada_kg else "excedente"
            alertas.append(AlertaConciliacion(
                lote_id=balance.lote_id,
                masa_entrada_kg=balance.masa_entrada_kg,
                masa_salida_kg=balance.masa_salida_kg,
                desvio_pct=desvio_pct,
                detalle=(f"{direccion} de {desvio:.0f} kg ({desvio_pct}%) sobre "
                         f"tolerancia de {tolerancia_pct}%: revisar calibración de "
                         f"instrumentos o pérdida física del lote {balance.lote_id}"),
            ))
    return alertas
