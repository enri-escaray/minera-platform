# Replicación sitio → nube (conducto C2)

La replicación de tópicos es EL único flujo de datos del sitio hacia la nube, y es
unidireccional y asíncrona (ADR-001/002). Se implementa con MirrorMaker 2 corriendo
EN LA DMZ (no en el sitio ni en la nube): así el sitio nunca abre conexiones entrantes
y la nube nunca alcanza la red del sitio.

## Qué se replica
- trazabilidad.lotes, trazabilidad.pesajes  → custodia consolidada multi-sitio
- mantenimiento.ots                          → CMMS corporativo y dataset de entrenamiento
- despacho.asignaciones                      → analítica de productividad
- telemetria.equipos                         → SOLO agregados de 1 min (el detalle crudo
  se queda en el sitio; el ancho de banda satelital no se gasta en datos re-derivables)

## Qué NO se replica jamás
- Tópicos de comando o control (no existen por diseño: la nube manda planes por C3
  como archivos firmados, nunca comandos en caliente).

## Configuración de referencia
Ver `mm2.properties`. Naming: el tópico llega a nube prefijado con el sitio
(`sitio-01.trazabilidad.lotes`), que es lo que permite consolidar N sitios en un
mismo clúster de nube sin colisiones (escalabilidad horizontal de sitios).
