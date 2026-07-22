# ADR-001 — Arquitectura híbrida edge + nube
**Estado:** aceptado (2026-07)

## Contexto
Sitios remotos con conectividad satelital/celular intermitente y cara. La continuidad operativa
es el requisito dominante: una parada de mina cuesta órdenes de magnitud más que cualquier
infraestructura local.

## Decisión
Cada sitio corre un clúster edge autónomo (K3s, 3+ nodos) con todos los servicios necesarios
para operar: despacho, trazabilidad, pesajes, apps de campo. La nube consolida, analiza y aloja
ERP. Sincronización asíncrona vía DMZ.

## Consecuencias
+ Modo isla indefinido; latencia local <10ms para despacho.
+ Ancho de banda satelital reducido (solo eventos y agregados, no telemetría cruda).
− Hardware y operación en sitio; despliegues más complejos (GitOps obligatorio).
− Datos corporativos con retraso (aceptado: ver ADR-002).
