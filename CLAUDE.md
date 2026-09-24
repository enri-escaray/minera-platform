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

Roles de solo lectura (`trazabilidad.leer`, `mantenimiento.leer`, `despacho.leer`,
`reportes.leer`); los roles de escritura también habilitan la lectura de su dominio.

**`AUTH_MODE` es obligatorio y no tiene valor por defecto** (ADR-005): si falta, el
servicio no arranca. `dev` = HS256 con `AUTH_SECRET` (mínimo 32 caracteres, sin
default); `oidc` = JWKS de Keycloak/Entra y exige además `OIDC_AUDIENCE` y
`OIDC_ISSUER`. Todo token requiere `exp`. El rol comodín `*` solo concede en `dev`.
Si el proceso declara `SITIO_ID`, un token de otro sitio se rechaza con 403.

Las consultas van abiertas en `dev` (el panel es estático y sin login) y cerradas
en `oidc`; `AUTH_LECTURA=exigir|abierta` fuerza cualquiera de las dos.

Tokens de prueba:
`AUTH_MODE=dev AUTH_SECRET=dev-secret-solo-para-desarrollo-local python -c "from minera_comun import emitir_token_dev; print(emitir_token_dev('yo', ['*']))"`

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
      El compose cablea `DATABASE_URL` en ambos (antes solo trazabilidad: corrían en
      memoria sin querer). El consumidor Kafka de mantenimiento reintenta la conexión
      si el broker no está listo al arrancar (antes quedaba pegado para siempre).
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

- [x] Demo reproducible: `tools/sembrar_demo.py` (siembra idempotente y consciente del
      estado, pesajes en el turno en curso) + guion de grabación en
      `marketing/guion-demo-ejecutable.md` (único guion: comandos, narración y coreografía).
- [x] Endurecimiento de seguridad (ADR-005). La autenticación falla cerrada:
      `AUTH_MODE`/`AUTH_SECRET` sin default (antes un olvido de configuración
      degradaba a un secreto publicado en el repo y cualquiera emitía rol `*`),
      `exp` obligatorio, `aud`/`iss` validados en OIDC, comodín solo en dev,
      claim `sitio` verificado, JWKS cacheado (antes un fetch al IdP por request,
      lo que ataba la autenticación al enlace externo). Suite `libs/comun/tests/`
      dedicada al borde de auth, en CI como job propio.
      Límites de entrada en todos los modelos (fan-out de custodia, matriz de
      viaje, import de bloques, conciliación, novedades en memoria) y cotas
      superiores en los `float` (aceptaban `Infinity`). Handler de validación
      común: un `Infinity` en el cuerpo provocaba un 500 no manejado.
      Contenedores no-root con `securityContext` y límites de recursos; Postgres,
      Kafka y MQTT solo en loopback en dev.
      NetworkPolicies corregidas: el deny-all declaraba `Egress` pero ninguna
      política lo concedía —ni siquiera DNS—, y faltaba el conducto a la base
      para `mantenimiento` y `despacho`, que persisten desde las migraciones
      002/003. Tal como estaban, el sitio no levantaba en K3s.

- [x] Fix de turno en el panel, la PWA y el sembrador: el turno se etiqueta con
      el día en que EMPEZÓ (convención que el scheduler de `reportes` ya usaba).
      Entre medianoche y las 07:00 UTC los tres pedían la noche con la fecha de
      hoy —una ventana futura— y el panel mostraba 0 toneladas en "Turno en
      curso". Verificado en vivo sobre el stack levantado.
      `apps/campo/sw.js` es cache-first: subir `CACHE` en cada cambio del shell.

### Pendiente (siguiente iteración — sé honesto con esto al retomar)
- [ ] Política de re-registro de equipos en despacho: el alta repetida de un camión
      pisa su estado (el índice único de la base frena el duplicado con 500; debería
      ser un error de dominio 409 o conservar el estado).
- [ ] Persistencia Postgres de novedades en `reportes` (hoy memoria; el evento ya viaja a nube)
- [ ] Job automático de conciliación P4 alimentado por `proceso_planta` (hoy es endpoint manual)
- [ ] Hora local del sitio en el scheduler de turnos (hoy UTC)
- [ ] Panel Grafana con `cola_pendientes` y tasa de OTs como primeras métricas
- [ ] El panel de sitio no funciona contra un sitio en `oidc` (lectura cerrada):
      necesita que el operador aporte un token
- [ ] `SERVICIO_TOKEN` es un token estático de larga vida; falta el flujo
      *client credentials* con refresco automático contra el IdP (ADR propio)
- [ ] Sin límite de tasa (rate limiting) en ninguna API: un cliente autenticado
      puede saturar un servicio del sitio
- [ ] La auditoría JSONL es append-only por convención, no por construcción:
      quien pueda escribir el PVC puede editarla. Para la afirmación de
      "auditoría inmutable" regulatoria falta encadenamiento por hash

## Cómo pedir ayuda a Claude Code en este repo

- Antes de tocar código, leer el ADR relevante; si la tarea contradice un ADR, escribir uno nuevo.
- Al crear un servicio nuevo, copiar la estructura de `services/trazabilidad/`.
- Al terminar una tarea: actualizar la sección "Estado actual" de este archivo.
