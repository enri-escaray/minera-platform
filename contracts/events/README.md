# Contratos de eventos

Tópicos Kafka (sitio): `trazabilidad.lotes`, `trazabilidad.pesajes`, `mantenimiento.ots`,
`telemetria.equipos` (agregados). Nombre de evento = `dominio.accion` + versión en el esquema.

Reglas: cambios compatibles hacia atrás dentro de la misma versión mayor; campo nuevo = opcional;
campo eliminado o renombrado = nueva versión + doble publicación por 90 días.
Todo evento lleva: `event_id` (UUID v7), `event_type`, `version`, `sitio_id`, `occurred_at`, `trace_id`.
