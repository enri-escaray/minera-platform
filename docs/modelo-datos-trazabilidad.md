# Modelo de datos — Trazabilidad del mineral

Objetivo: poder responder, para cualquier tonelada embarcada, **de qué bloque geológico salió,
por qué equipos pasó, qué leyes tenía en cada punto y quién registró cada dato** (cadena de custodia).

## Concepto central: el LOTE

La unidad trazable es el **lote de mineral**: material de un mismo disparo (voladura) con ley
estimada homogénea. Todo movimiento físico referencia un lote. La planta consume lotes y produce
lotes nuevos (concentrado), manteniendo el vínculo `lote_origen[] → lote_producto` (grafo, no árbol:
un stockpile mezcla lotes).

## Entidades

| Entidad | Qué representa | Claves |
|---|---|---|
| `bloque_geologico` | Celda del modelo de bloques (import desde Deswik/Vulcan) | id, coordenadas, ley_estimada por elemento |
| `disparo` | Voladura ejecutada sobre un conjunto de bloques | id, fecha, polígono, bloques[] |
| `lote_mineral` | Unidad trazable de material | id, disparo_id, tipo (ROM/stock/concentrado), estado |
| `movimiento` | Traslado físico de (parte de) un lote | lote_id, equipo_id, origen, destino, timestamps |
| `pesaje` | Medición de masa (báscula camión, pesómetro correa) | lote_id, masa_kg, instrumento_id, event_id único |
| `analisis_laboratorio` | Ley real medida sobre una muestra del lote | lote_id, elemento, ley, metodo, laboratorio |
| `ubicacion` | Nodo físico: frente, stockpile, tolva, celda planta, puerto | id, tipo, geometría |
| `proceso_planta` | Corrida de proceso: consume lotes, produce lotes | lotes_entrada[], lotes_salida[], recuperacion |
| `embarque` | Despacho a cliente/puerto | lotes[], destino, documentos |
| `auditoria` | Append-only: toda escritura de negocio | actor, accion, entidad, payload, ts |

## Reglas de integridad

1. **Balance de masas por lote**: `Σ pesajes de salida ≤ Σ pesajes de entrada + tolerancia_instrumental`.
   Se verifica en conciliación diaria automática; los desvíos generan alerta, no bloqueo (la báscula
   puede fallar; la operación no se detiene por software).
2. **Idempotencia**: `pesaje.event_id` es UNIQUE. Reintentos de sincronización no duplican masa.
3. **Inmutabilidad**: pesajes y análisis no se editan; se corrigen con un registro nuevo tipo
   `ajuste` que referencia al original (requisito de auditoría regulatoria).
4. **El grafo de custodia nunca se rompe**: `proceso_planta` exige al menos un lote de entrada;
   `embarque` exige lotes con análisis de laboratorio vigente.

## Series de tiempo (TimescaleDB)

La telemetría (posición GPS de camiones, vibración, tonelaje instantáneo de correas) NO va en las
tablas de trazabilidad: va en hypertables (`telemetria_equipo`, `telemetria_proceso`) con retención
de 90 días en el edge y agregados de 1 min replicados a nube. La trazabilidad guarda solo los
eventos de negocio derivados (un `pesaje`, no las 10.000 lecturas del pesómetro).

## Diagrama

Ver ERD en `db/migrations/001_trazabilidad.sql` (la migración es la fuente de verdad del esquema).
