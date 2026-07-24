# CLAUDE.md — Plataforma Minera

> Este archivo guía a Claude Code (y a cualquier persona nueva) para trabajar en este repo.
> **Regla del proyecto: cada vez que se tome una decisión de arquitectura, se agregue un servicio
> o cambie una convención, este archivo y/o los ADRs en `docs/adr/` deben actualizarse en el mismo PR.**

## Qué es este proyecto

Plataforma de software para operaciones mineras en entornos críticos y remotos. Cubre:
trazabilidad del mineral en tiempo real (mina → planta → puerto), despacho de flota,
mantenimiento predictivo, integración con ERP/cadena de suministro y con datos geológicos.

Principio rector: **la mina no puede parar aunque se caiga el enlace con el exterior.**

## Arquitectura en una línea

Edge autónomo por sitio (clúster local, nivel 3 Purdue) + nube consolidadora (niveles 4–5),
separados por una DMZ industrial (IEC 62443, zonas y conductos). Comunicación entre sitio y
nube **siempre asíncrona por eventos** (store-and-forward), nunca base-a-base ni RPC síncrono.

Detalle completo: `docs/arquitectura.md`. Decisiones y sus porqués: `docs/adr/`.

## Reglas de arquitectura (no negociables)

1. **AP hacia afuera, CP hacia adentro.** Entre sitio y nube: consistencia eventual.
   Dentro del sitio (despacho, seguridad, pesajes): consistencia fuerte sobre el clúster local.
2. **Todo evento de dominio es idempotente.** Cada evento lleva `event_id` (UUID v7) y los
   consumidores deben tolerar duplicados. Los reintentos jamás pueden duplicar toneladas ni órdenes.
3. **Nada del software de gestión escribe sobre PLC/SCADA.** Solo lectura de la capa OT
   (OPC UA / MQTT). El control de proceso vive en los controladores industriales.
4. **Contratos versionados.** Esquemas de eventos en `contracts/events/*.vN.json`. Cambios
   compatibles hacia atrás dentro de la misma versión mayor; cambios incompatibles = nueva versión
   y período de doble publicación.
5. **Offline-first.** Toda app de campo y todo servicio edge debe funcionar sin conectividad
   externa y sincronizar al reconectar (cola persistente local).
6. **Auditoría inmutable.** Toda escritura de negocio genera un registro de auditoría append-only
   (quién, qué, cuándo, desde dónde). Requerido para trazabilidad regulatoria del mineral.
7. **Denegación por defecto entre zonas.** Ningún servicio nuevo abre puertos hacia otra zona
   sin registrar el conducto en `docs/arquitectura.md` (sección "Conductos").

## Stack tecnológico (resumen — detalle en docs/stack-tecnologico.md)

| Capa | Tecnología | Nota |
|---|---|---|
| Servicios | Python 3.12 + FastAPI | Un servicio por dominio en `services/<nombre>/` |
| Base transaccional/series | PostgreSQL 16 + TimescaleDB | Migraciones SQL puras en `db/migrations/` |
| Bus de eventos (sitio) | Kafka (Redpanda en dev) | Tópicos: ver `contracts/events/README.md` |
| Telemetría de campo | MQTT (Mosquitto) | Gateway → bridge a Kafka |
| Edge runtime | Docker Compose (dev) → K3s (prod) | 3 nodos mínimo por sitio en prod |
| Sync sitio↔nube | Replicación de tópicos vía DMZ | MirrorMaker/Redpanda replication |
| Infra como código | Pendiente (ADR abierto) | Terraform + GitOps propuesto |

## Estructura del repo

```
minera-platform/
├── CLAUDE.md              ← este archivo. Mantener actualizado.
├── docs/
│   ├── arquitectura.md    ← capas, zonas, conductos, flujos
│   ├── modelo-datos-trazabilidad.md
│   ├── stack-tecnologico.md
│   ├── procesos-automatizados.md   ← flujos: despacho, mantenimiento, reposición
│   └── adr/               ← ADR-NNN-*.md, nunca se borran, se reemplazan (status: superseded)
├── contracts/events/      ← esquemas JSON de eventos, versionados
├── db/migrations/         ← SQL numerado NNN_nombre.sql, solo forward (sin down)
├── libs/comun/            ← minera_comun: outbox, RBAC (JWT dev/OIDC), auditoría JSONL
├── services/
│   ├── trazabilidad/      ← cadena de custodia (referencia de estilo; Postgres + RBAC)
│   ├── ingesta/           ← bridge MQTT→Kafka + relay de outbox (store-and-forward)
│   ├── mantenimiento/     ← flujo P1: telemetría → OTs predictivas (consumidor Kafka)
│   ├── despacho/          ← asignación camión-pala (CP local, decisiones explicables)
│   └── reportes/          ← flujos P2 (reporte de turno) y P4 (conciliación)
├── apps/
│   ├── campo/             ← PWA offline-first para novedades de turno
│   └── panel/             ← panel de sitio solo lectura (despacho, OTs, custodia, turno)
├── tools/                 ← adaptador CSV de bloques, validador de contratos (CI)
├── .github/workflows/     ← CI: 5 suites + contratos + migraciones
├── deploy/
│   ├── k3s/               ← manifiestos edge: 3 nodos, NetworkPolicies deny-all
│   └── replicacion/       ← MirrorMaker 2 en la DMZ (conducto C2, unidireccional)
└── docker-compose.yml     ← entorno de desarrollo local completo
```

## Roles RBAC (claims `roles` del JWT)

| Rol | Habilita |
|---|---|
| `trazabilidad.escribir` | crear lotes, registrar pesajes |
| `mantenimiento.planificar` | agendar OTs |
| `mantenimiento.cerrar` | cerrar OTs (acción humana del flujo P1) |
| `despacho.configurar` | alta de palas y camiones |
| `despacho.operar` | solicitar asignaciones, liberar, enviar a mantenimiento |
| `geologia.importar` | importar el modelo de bloques |
| `campo.reportar` | registrar novedades desde la PWA de campo |
| `metalurgia.conciliar` | ejecutar la conciliación de balance de masas (P4) |

`AUTH_MODE=dev` (HS256 + `AUTH_SECRET`) solo en desarrollo; producción usa
`AUTH_MODE=oidc` contra el JWKS de Keycloak/Entra. Tokens de prueba:
`python -c "from minera_comun import emitir_token_dev; print(emitir_token_dev('yo', ['*']))"`

## Comandos

```bash
# entorno de desarrollo completo (Postgres+Timescale, Redpanda, Mosquitto, servicios)
docker compose up -d

# correr el servicio de trazabilidad local sin docker (usa repo en memoria si no hay DATABASE_URL)
cd services/trazabilidad && pip install -r requirements.txt && uvicorn app.main:app --reload

# tests (las 4 suites; instala antes la lib común: pip install -e libs/comun)
for s in trazabilidad ingesta mantenimiento despacho reportes; do (cd services/$s && python -m pytest -q); done

# simular telemetría contra el sitio local
mosquitto_pub -h localhost -t 'telemetria/cam-101/carga_ton' -m '{"ts":"2026-07-18T12:00:00Z","valor":218.5}'

# ver profundidad de cola (si crece sostenido = modo isla)
curl localhost:8001/health

# aplicar migraciones (dev; en compose se aplican solas al primer arranque)
for m in db/migrations/*.sql; do psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$m"; done

# validar contratos de eventos (lo mismo que corre CI)
python tools/validar_contratos.py
```

## Convenciones de código

- Español para dominio de negocio (tablas, eventos, endpoints: `lote`, `pesaje`, `disparo`);
  inglés para infraestructura técnica (variables internas, librerías).
- Servicios: patrón repositorio (`InMemoryRepo` para tests, `PostgresRepo` en runtime).
- Endpoints REST solo para consultas y comandos de usuario; integración entre servicios = eventos.
- Errores de dominio explícitos (excepciones propias), nunca `except Exception` silencioso.
- Todo endpoint de escritura exige header `Idempotency-Key` o `event_id` en el cuerpo.

## Estado actual (actualizar al avanzar)

- [x] Arquitectura base y ADRs 001–004
- [x] Modelo de datos de trazabilidad + migración 001
- [x] Contratos de eventos v1: `lote.creado`, `pesaje.registrado`, `ot.generada`
- [x] Servicio `trazabilidad`: crear lote, registrar pesaje, consultar cadena de custodia (repo en memoria + Postgres)
- [x] docker-compose de desarrollo
- [x] Servicio `ingesta`: bridge MQTT→Kafka con cola persistente SQLite (store-and-forward),
      relay de outbox de servicios, backoff en modo isla, idempotencia por uuid5 determinístico.
      Tests cubren: modo isla completo, caída a mitad de lote, relay con offsets.
- [x] Librería `minera_comun`: outbox compartida, RBAC JWT (dev HS256 / prod OIDC-JWKS),
      auditoría append-only JSONL. Todos los servicios la usan (DRY).
- [x] Servicio `mantenimiento`: consumidor de `telemetria.equipos`, reglas con ventana de
      persistencia (un pico no dispara), antirrebote por OT viva, cierre humano autenticado
      que rehabilita la vigilancia. Contratos `ot.generada` / `ot.cerrada`.
- [x] Servicio `despacho`: asignación camión-pala determinista y explicable (motivo en cada
      asignación), colas por pala, prioridad de mezcla como desempate, estados de camión.
      Contrato `asignacion.creada`.
- [x] RBAC transversal aplicado a todas las escrituras + auditoría en cada acción humana.
- [x] Manifiestos K3s (deploy/k3s): anti-affinity, probes, PVCs, NetworkPolicies deny-all
      que materializan los conductos C1-C4. Replicación sitio→nube con MirrorMaker 2 en DMZ.

- [x] Persistencia Postgres de `mantenimiento` (migración 002, índice único parcial que
      garantiza el antirrebote en la base) y de `despacho` (migración 003, write-through +
      `restaurar()` que reconstruye colas y asignaciones vivas tras un reinicio).
- [x] Evaluador extendido: condición `mayor`/`menor` (umbrales inferiores) y reglas
      compuestas vía `condiciones_adicionales` (contexto de otras métricas del equipo).
- [x] Servicio `reportes` (P2 + P4): reporte de turno bajo demanda y por scheduler al
      cierre de turno; conciliación de balance de masas con alertas (no bloqueos);
      novedades de campo idempotentes por event_id del dispositivo.
- [x] PWA de campo offline-first (`apps/campo/`): IndexedDB + service worker; sincroniza
      novedades al volver la señal sin duplicar.
- [x] Import del modelo de bloques: contrato `bloque.importado`, endpoint con rol
      `geologia.importar`, adaptador `tools/convertir_bloques_csv.py` (ADR-004: el
      formato del vendor jamás entra al dominio).
- [x] CI (GitHub Actions): 5 suites en matriz + validación de contratos
      (`tools/validar_contratos.py`) + aplicación de migraciones contra Timescale real.
- [x] Guía de puesta en marcha: `docs/puesta-en-marcha.md`.
- [x] Panel de sitio solo lectura (`apps/panel/`, :8081): despacho en vivo con motivo
      explicable de cada asignación, OTs, lotes con cadena de custodia navegable y
      turno en curso. Sin escrituras: consume `GET /estado`, `GET /ots`, `GET /lotes`
      (nuevo), `GET /lotes/{id}/custodia` y `GET /reportes/turno`. CORS de solo GET
      en trazabilidad/mantenimiento/despacho (mismo patrón que reportes).

### Pendiente (siguiente iteración — sé honesto con esto al retomar)
- [ ] Persistencia Postgres de novedades en `reportes` (hoy memoria; el evento ya viaja a nube)
- [ ] Job automático de conciliación P4 alimentado por `proceso_planta` (hoy es endpoint manual)
- [ ] Hora local del sitio en el scheduler de turnos (hoy UTC)
- [ ] Panel Grafana con `cola_pendientes` y tasa de OTs como primeras métricas

## Cómo pedir ayuda a Claude Code en este repo

- Antes de tocar código, leer el ADR relevante; si la tarea contradice un ADR, escribir uno nuevo.
- Al crear un servicio nuevo, copiar la estructura de `services/trazabilidad/`.
- Al terminar una tarea: actualizar la sección "Estado actual" de este archivo.
