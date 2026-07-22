# Procesos automatizados — especificación de flujos

## P1 — Mantenimiento predictivo → OT automática
1. Sensores publican telemetría a MQTT; bridge la lleva a Kafka (`telemetria.equipos`).
2. Servicio `mantenimiento` (edge) evalúa modelos por equipo (umbral/ML descargado desde nube, firmado).
3. Anomalía detectada → emite `ot.generada` (origen=predictivo) con diagnóstico y repuestos sugeridos.
4. Consumidores: (a) planificación agenda técnico en ventana de menor impacto productivo;
   (b) inventario reserva repuestos; si falta stock → emite `oc.solicitada` hacia ERP (nube, asíncrono).
5. Cierre de OT publica `ot.cerrada` → realimenta dataset de entrenamiento en nube.
Regla: el modelo SUGIERE; ninguna OT crítica detiene un equipo sin confirmación humana.

## P2 — Reporte de turno automático
Job programado al cierre de turno: agrega pesajes, movimientos, disponibilidad (telemetría),
desvíos vs. plan; genera reporte y lo publica. Cero digitación manual. Si el enlace está caído,
el reporte corporativo se envía al reconectar (el local siempre está disponible en sitio).

## P3 — Reposición automática de inventario
Consumo de repuestos/insumos descuenta stock local → si stock < punto_reorden (considerando
lead time del sitio remoto) → `oc.solicitada` al ERP. Idempotente por (item, ventana).

## P4 — Conciliación mina-planta diaria
Cruza: leyes estimadas (modelo de bloques) vs. pesajes vs. análisis de laboratorio vs. producción
planta. Balance de masas por lote; desvíos > tolerancia generan alerta a metalurgia. No bloquea operación.
