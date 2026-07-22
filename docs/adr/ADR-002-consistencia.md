# ADR-002 — AP hacia afuera, CP hacia adentro
**Estado:** aceptado (2026-07)

## Decisión
Ante partición de red sitio↔nube, se prioriza disponibilidad (AP): el sitio sigue escribiendo
localmente y sincroniza después (consistencia eventual). Dentro del sitio, las operaciones
críticas (despacho, pesajes, bloqueos de seguridad) usan consistencia fuerte sobre PostgreSQL
con réplica síncrona local.

## Consecuencias
+ La mina nunca se detiene por falta de enlace.
− El ERP puede ver stock/producción desactualizados minutos u horas. Mitigación: timestamp de
  "dato al" visible en toda reportería corporativa; conciliación automática al reconectar.
− Exige idempotencia estricta en todos los eventos (event_id único, consumidores tolerantes a duplicados).
