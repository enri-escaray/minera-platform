# Stack tecnológico por capa

## Edge (nivel 3 Purdue — en cada sitio minero)
| Componente | Tecnología | Por qué |
|---|---|---|
| Orquestación | K3s (3+ nodos) | Kubernetes liviano, HA con quorum, opera sin internet |
| Servicios de dominio | Python 3.12 + FastAPI | Velocidad de desarrollo, tipado con Pydantic, ecosistema de datos |
| Base transaccional | PostgreSQL 16 (réplica síncrona local) | ACID para custodia; sobrevive fallo de nodo |
| Series de tiempo | TimescaleDB (extensión de la misma PG) | Un solo motor que operar en sitio remoto |
| Bus de eventos | Kafka (Redpanda: binario único, sin ZooKeeper) | Cola persistente = buffer natural para modo isla |
| Ingesta de campo | MQTT (Mosquitto) + bridge a Kafka | Estándar de sensores/IoT industrial, QoS con reintentos |
| Lectura OT | OPC UA client (asyncua) — SOLO LECTURA | Estándar SCADA/PLC; jamás se escribe a OT |
| Apps de campo | PWA offline-first (IndexedDB + cola de sync) | Tablets en el rajo sin señal |

## DMZ industrial (IEC 62443)
- Replicación unidireccional de tópicos Kafka sitio→nube (MirrorMaker 2 / Redpanda replication).
- Firewall con denegación por defecto; cada conducto documentado en `docs/arquitectura.md`.
- Opcional para sitios de alta criticidad: diodo de datos por hardware para el flujo OT→IT.

## Nube (niveles 4–5)
| Componente | Tecnología |
|---|---|
| Consolidación multi-sitio | Kafka gestionado + data lake (Parquet/Iceberg en objeto S3) |
| Analítica / ML | Warehouse (BigQuery/Snowflake/ClickHouse) + entrenamiento de modelos predictivos |
| ERP | Comprado (SAP/Dynamics u otro) — integración por API + eventos, nunca duplicado |
| Geología | Comprado (Deswik/Vulcan/Leapfrog) — import del modelo de bloques vía contrato `bloque.importado` |
| Identidad | OIDC (Keycloak/Entra) con RBAC por sitio y rol |
| Observabilidad | Prometheus + Grafana en edge, federado a nube; logs con Loki |

## Desarrollo local
`docker-compose.yml` levanta: TimescaleDB, Redpanda, Mosquitto y el servicio de trazabilidad.
Simula el "sitio" completo en una laptop. El modo isla se prueba apagando la red del compose.
