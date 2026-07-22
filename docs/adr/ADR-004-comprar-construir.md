# ADR-004 — Comprar ERP y geología; construir edge, integración y trazabilidad
**Estado:** aceptado (2026-07)

## Decisión
ERP (finanzas, compras, RRHH) y modelado geológico son commodities maduros: se compran y se
integran por API/eventos. La diferenciación está en la capa edge, la trazabilidad de custodia
y la automatización operativa: eso se construye en este repo.

## Consecuencias
+ Foco del equipo en lo que genera ventaja; time-to-value del ERP conocido.
− Dependencia de vendors: los contratos de integración (`bloque.importado`, `oc.creada`) aíslan
  el dominio propio de los formatos del vendor.
