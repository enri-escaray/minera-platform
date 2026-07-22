# ADR-003 — Integración por eventos idempotentes
**Estado:** aceptado (2026-07)

## Decisión
Los servicios se integran publicando/consumiendo eventos de dominio en Kafka con esquemas
versionados (`contracts/events/`). REST solo para consultas y comandos de usuario. Prohibido
RPC síncrono entre servicios para flujos de negocio.

## Consecuencias
+ Un requisito nuevo = un consumidor nuevo del stream, sin tocar productores.
+ Los buffers de Kafka son el mecanismo natural de store-and-forward en modo isla.
− Debugging distribuido más difícil. Mitigación: trace_id propagado en cada evento; auditoría append-only.
