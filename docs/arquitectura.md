# Arquitectura

Ver discusión completa y diagrama en el hilo de diseño. Resumen operativo:

## Capas (modelo Purdue / IEC 62443)
- **Niveles 0–2 (OT):** PLC, SCADA, sensores, flota autónoma. Red propia. El software de gestión solo LEE.
- **Nivel 3 (Edge, por sitio):** clúster K3s con servicios de dominio, PostgreSQL+Timescale, Kafka, MQTT.
  Autónomo: opera indefinidamente en modo isla.
- **DMZ:** único punto de paso sitio↔nube. Replicación asíncrona de tópicos. Denegación por defecto.
- **Niveles 4–5 (Nube):** ERP, data lake, ML, consolidación multi-sitio, reportería.

## Conductos autorizados (mantener actualizado)
| # | Origen | Destino | Protocolo | Dirección | Contenido |
|---|---|---|---|---|---|
| C1 | OT (SCADA/OPC UA) | Edge ingesta | OPC UA / MQTT | OT→Edge (solo lectura) | Telemetría |
| C2 | Edge Kafka | Nube Kafka | TLS mutuo, replicación | Edge→Nube | Eventos de dominio, agregados 1min |
| C3 | Nube | Edge | TLS mutuo | Nube→Edge | Planes (mina/mantenimiento), catálogos, config firmada |
| C4 | Nube ERP | Servicios nube | HTTPS API | Bidireccional | Órdenes de compra, maestros |

## Trade-offs adoptados (detalle en ADRs)
- ADR-001: arquitectura híbrida edge+nube (no cloud-only, no on-prem-only).
- ADR-002: AP entre sitio y nube, CP dentro del sitio.
- ADR-003: integración entre servicios por eventos idempotentes, no RPC síncrono.
- ADR-004: comprar ERP y geología; construir integración, edge y trazabilidad.
