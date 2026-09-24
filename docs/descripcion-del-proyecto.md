# Mineral360 — Descripción del proyecto

Documento único de referencia: qué es, qué problema resuelve, cómo está
construido, qué funciona hoy y qué falta. Para el detalle de cada decisión, los
ADRs en `docs/adr/`. Para trabajar en el código, `CLAUDE.md`.

- **Nombre comercial:** Mineral360
- **Repositorio:** `minera-platform`
- **Autor:** Enrique Escaray
- **Estado:** plataforma funcional de punta a punta en entorno de desarrollo;
  no desplegada en un sitio productivo real.

---

## 1. Qué es

Una plataforma de software para operaciones mineras en sitios remotos. Cubre
cuatro dominios sobre una misma base de eventos:

- **Trazabilidad del mineral** — cadena de custodia lote por lote, desde el
  disparo en el rajo hasta el embarque, con auditoría de cada movimiento.
- **Despacho de flota** — asignación camión-pala determinista, con el motivo de
  cada decisión escrito y auditado.
- **Mantenimiento predictivo** — órdenes de trabajo generadas desde la telemetría
  de los equipos, con reglas de persistencia para no disparar por un pico aislado.
- **Reportería y conciliación** — reporte de turno automático y balance de masas
  mina-planta con alertas.

Lo que la distingue no es la lista de dominios: es **dónde corre**. El sistema
vive dentro de la mina y sigue operando cuando se corta el enlace con el exterior.

## 2. El problema

Una mina remota pierde conectividad con regularidad: enlace satelital, microondas,
clima, cortes de energía. El software de gestión alojado en la nube queda ciego
justo cuando la operación no puede detenerse. Lo que pasa en la práctica: el
despacho vuelve a la radio, los pesajes al papel, y la trazabilidad que exige el
regulador se reconstruye días después a partir de planillas.

El costo no es el tiempo de sistema caído. Es que la información del turno se
pierde o se degrada, y con ella la capacidad de responder una fiscalización,
conciliar un balance de masas o justificar una decisión de despacho.

## 3. Principio rector

> **La mina no puede parar aunque se caiga el enlace con el exterior.**

Todo lo demás en este proyecto se deriva de ahí: por qué hay un clúster por sitio,
por qué la integración es por eventos y no por RPC, por qué cada evento es
idempotente, por qué las apps de campo funcionan sin señal.

## 4. Arquitectura

Edge autónomo por sitio (nivel 3 del modelo Purdue) más una nube consolidadora
(niveles 4–5), separados por una DMZ industrial siguiendo IEC 62443 (zonas y
conductos). La comunicación sitio↔nube es **siempre asíncrona por eventos**
(store-and-forward): nunca base-a-base, nunca RPC síncrono.

### Capas

| Nivel | Qué vive ahí | Rol del software de gestión |
|---|---|---|
| 0–2 (OT) | PLC, SCADA, sensores, flota autónoma | **Solo lectura.** Jamás escribe al control de proceso |
| 3 (Edge, por sitio) | K3s, servicios de dominio, PostgreSQL+Timescale, Kafka, MQTT | Opera indefinidamente en modo isla |
| DMZ | Replicación unidireccional de tópicos | Único punto de paso; denegación por defecto |
| 4–5 (Nube) | ERP, data lake, ML, consolidación multi-sitio | Se entera cuando se puede |

### Conductos autorizados

| # | Origen | Destino | Dirección | Contenido |
|---|---|---|---|---|
| C1 | OT (SCADA/OPC UA) | Edge ingesta | OT→Edge, solo lectura | Telemetría |
| C2 | Edge Kafka | Nube Kafka | Edge→Nube | Eventos de dominio |
| C3 | Nube | Edge | Nube→Edge | Planes, catálogos, config firmada |
| C4 | ERP nube | Servicios nube | Bidireccional | Órdenes de compra, maestros |

Los conductos no son documentación decorativa: están materializados como
NetworkPolicies de Kubernetes en `deploy/k3s/30-network-policies.yaml`, con
deny-all de base. Un servicio nuevo que quiera hablar con otro necesita las dos
mitades declaradas —egress en el cliente e ingress en el servidor— o no habla.

### Consistencia

**AP hacia afuera, CP hacia adentro** (ADR-002). Ante una partición sitio↔nube se
prioriza disponibilidad: el sitio sigue escribiendo y sincroniza después. Dentro
del sitio, las operaciones críticas (despacho, pesajes) usan consistencia fuerte
sobre PostgreSQL local. La contrapartida aceptada: el ERP puede ver producción
desactualizada por minutos u horas, y eso obliga a idempotencia estricta en todo
evento.

## 5. Reglas no negociables

1. AP hacia afuera, CP hacia adentro.
2. Todo evento de dominio lleva `event_id` y los consumidores toleran duplicados.
   Un reintento jamás duplica toneladas ni órdenes.
3. Nada del software de gestión escribe sobre PLC/SCADA.
4. Contratos de eventos versionados; cambio incompatible = versión nueva y doble
   publicación temporal.
5. Offline-first: toda app de campo y todo servicio edge funciona sin conectividad
   y sincroniza al reconectar.
6. Auditoría append-only en toda escritura de negocio.
7. Denegación por defecto entre zonas; ningún puerto nuevo sin conducto registrado.

## 6. Componentes

### Servicios (`services/`)

| Servicio | Puerto | Qué hace |
|---|---|---|
| `trazabilidad` | 8000 | Lotes, pesajes, cadena de custodia, import del modelo de bloques. Es la referencia de estilo del repo |
| `ingesta` | 8001 | Bridge MQTT→Kafka con cola SQLite persistente (store-and-forward) y relay de las outbox de los demás servicios |
| `mantenimiento` | 8002 | Consume telemetría, evalúa reglas con ventana de persistencia, genera y cierra OTs |
| `despacho` | 8003 | Asignación camión-pala determinista y explicable, colas por pala, estados de camión |
| `reportes` | 8004 | Reporte de turno (bajo demanda y por scheduler), conciliación de balance de masas, novedades de campo |

Cada servicio sigue el mismo patrón: FastAPI, modelos Pydantic, patrón repositorio
(`InMemoryRepo` para tests, `PostgresRepo` en runtime), errores de dominio
explícitos, y publicación de eventos a una outbox local.

### Apps (`apps/`)

- **`campo`** — PWA offline-first para novedades de turno. IndexedDB más service
  worker: carga sin señal, encola local y sincroniza al volver la cobertura sin
  duplicar (idempotencia por `event_id` generado en el dispositivo).
- **`panel`** — panel de sitio de solo lectura (puerto 8081): despacho en vivo con
  el motivo de cada asignación, OTs, lotes con custodia navegable y turno en curso.
  No escribe nada.

### Librería común (`libs/comun/minera_comun/`)

- `eventos.py` — outbox transaccional simplificada (JSONL append-only).
- `seguridad.py` — RBAC con JWT: modo `dev` (HS256) y `oidc` (JWKS). Es el borde
  de autenticación de toda la plataforma.
- `auditoria.py` — registro append-only para servicios sin base propia.
- `http.py` — manejo transversal de errores de validación.

### Otros directorios

- `contracts/events/` — 9 esquemas JSON versionados; un validador los verifica en CI.
- `db/migrations/` — 3 migraciones SQL, solo hacia adelante, numeradas.
- `deploy/k3s/` — manifiestos del sitio: 3 nodos, anti-affinity, probes, PVCs,
  NetworkPolicies, securityContext.
- `deploy/replicacion/` — MirrorMaker 2 en la DMZ (conducto C2, unidireccional).
- `tools/` — adaptador CSV del modelo de bloques, validador de contratos, sembrador de demo.
- `marketing/` — kit de ventas: propuesta, deck, capturas, plantillas de contacto,
  guion de grabación de la demo.

## 7. Flujos automatizados

- **P1 — Mantenimiento predictivo.** Telemetría MQTT → Kafka → evaluación de reglas
  por equipo. Una anomalía sostenida (no un pico) genera `ot.generada` con
  diagnóstico y repuestos sugeridos. El cierre es humano y autenticado, y rehabilita
  la vigilancia del equipo. Regla de diseño: el modelo **sugiere**; ninguna OT
  detiene un equipo sin confirmación de una persona.
- **P2 — Reporte de turno.** Agrega pesajes y novedades del turno. Disponible bajo
  demanda en el sitio con o sin enlace, y publicado automáticamente al cierre de
  cada turno. Cero digitación manual.
- **P3 — Reposición de inventario.** Consumo descuenta stock; bajo el punto de
  reorden emite `oc.solicitada` al ERP. *Especificado, no implementado.*
- **P4 — Conciliación mina-planta.** Balance de masas por lote; desvíos sobre
  tolerancia generan alerta a metalurgia. **No bloquea la operación** — una báscula
  puede fallar, y la mina no se detiene por software.

## 8. Seguridad

La postura está en ADR-005 y se resume en una idea: **la autenticación falla
cerrada.** Si la configuración de auth falta o es ambigua, el servicio se niega a
arrancar en lugar de degradar a un modo permisivo.

- `AUTH_MODE` es obligatorio y **no tiene valor por defecto**. Un default
  silencioso convertiría un olvido de configuración en un bypass total.
- `dev` usa HS256 con `AUTH_SECRET` (mínimo 32 caracteres, sin default).
  `oidc` valida contra el JWKS del IdP y exige además audiencia y emisor: sin
  `aud`, un token emitido para otra aplicación del mismo realm sería aceptado.
- Todo token requiere `exp`. Una credencial sin vencimiento no se puede revocar
  sin rotar el secreto de todo el sitio.
- El rol comodín `*` concede **solo en modo dev**. Si el IdP pudiera emitirlo, un
  grupo mal nombrado sería un superusuario silencioso.
- El claim `sitio` se verifica contra `SITIO_ID`: un token del sitio A no opera el
  rajo del sitio B.
- Las claves JWKS se cachean, así la autenticación sobrevive a un corte del enlace
  externo — que es precisamente el escenario que la arquitectura prioriza.

### RBAC

Ocho roles de escritura (`trazabilidad.escribir`, `geologia.importar`,
`mantenimiento.planificar`, `mantenimiento.cerrar`, `despacho.configurar`,
`despacho.operar`, `campo.reportar`, `metalurgia.conciliar`) y cuatro de solo
lectura (`trazabilidad.leer`, `mantenimiento.leer`, `despacho.leer`,
`reportes.leer`). Los roles de escritura también habilitan la lectura de su dominio.

Las consultas van abiertas en `dev` (el panel de sitio es estático y sin login) y
cerradas en `oidc`. La cadena de custodia es dato regulatorio.

### Endurecimiento de infraestructura

Contenedores no-root (uid 10001) con `readOnlyRootFilesystem`, todas las
capabilities descartadas, `seccompProfile: RuntimeDefault` y límites de recursos.
En desarrollo, la base, el bus y el broker MQTT escuchan solo en loopback.

## 9. Despliegue

**Desarrollo.** `docker-compose.yml` levanta un sitio completo en una laptop: diez
contenedores (base, bus, broker, cinco servicios, dos apps). El modo isla se prueba
con `docker compose pause redpanda`. Detalle en `docs/puesta-en-marcha.md`.

**Producción.** K3s de tres nodos mínimo por sitio, con las NetworkPolicies
aplicadas y `AUTH_MODE=oidc`. La replicación hacia la nube es MirrorMaker 2 en la
DMZ, unidireccional. La checklist de go-live está en `docs/puesta-en-marcha.md`.

## 10. Calidad

- **71 tests** en seis suites: la librería común (20, dedicados al borde de
  autenticación) más una por servicio.
- **CI en GitHub Actions:** validación de los contratos de eventos, suite de
  seguridad como job propio, las cinco suites de servicio en matriz, auditoría de
  dependencias con `pip-audit`, y aplicación de las migraciones contra un
  Timescale real.
- Los tests de `libs/comun/tests/` tienen una regla propia: cada uno corresponde a
  una forma concreta de entrar sin permiso. Si uno falla, hay una puerta abierta,
  no un test roto.

## 11. Estado real

### Funciona

Los cinco servicios con persistencia PostgreSQL, el bridge de ingesta con cola
persistente y modo isla verificado, la generación y cierre de OTs, el despacho con
restauración de estado tras reinicio, el reporte de turno y la conciliación, las
dos apps web, el import del modelo de bloques, los manifiestos de K3s, el CI y la
demo reproducible.

El flujo completo se verificó en vivo sobre el stack levantado: telemetría
generando una OT, cadena de custodia navegable, corte y recuperación del enlace con
la cola drenando sola, y una novedad de campo llegando al reporte del turno.

### Falta

Honestamente, y en orden aproximado de importancia:

- **Política de re-registro de equipos en despacho.** El alta repetida de un camión
  pisa su estado; debería ser un 409 o conservar el estado.
- **`SERVICIO_TOKEN` es un token estático de larga vida.** Falta el flujo
  *client credentials* con refresco automático contra el IdP.
- **Sin límite de tasa en ninguna API.** Un cliente autenticado puede saturar un
  servicio del sitio.
- **La auditoría JSONL es append-only por convención, no por construcción.** Quien
  pueda escribir el volumen puede editarla. Para sostener la afirmación de
  "auditoría inmutable" ante un regulador falta encadenamiento por hash.
- **El panel de sitio no funciona contra un sitio en `oidc`**: necesita que el
  operador aporte un token.
- Persistencia PostgreSQL de las novedades en `reportes` (hoy en memoria).
- Job automático de conciliación P4 (hoy es un endpoint manual).
- Hora local del sitio en el scheduler de turnos (hoy UTC).
- Flujo P3 (reposición de inventario): especificado, sin implementar.
- Observabilidad: falta el panel Grafana con `cola_pendientes` y tasa de OTs.
- Infraestructura como código: ADR abierto (Terraform + GitOps propuesto).

### Lo que este proyecto no es

No es un ERP ni un software de geología: ambos se compran e integran (ADR-004). No
es un sistema de control de proceso: no escribe a PLC ni SCADA, por diseño. Y no
está validado en un sitio productivo real — está validado en un entorno que simula
uno.

## 12. Dónde seguir leyendo

| Documento | Para qué |
|---|---|
| `CLAUDE.md` | Mapa del repo, convenciones de código y estado actualizado. Empezar acá para trabajar en el código |
| `docs/arquitectura.md` | Capas, zonas y tabla de conductos |
| `docs/stack-tecnologico.md` | Tecnología elegida por capa y el por qué de cada una |
| `docs/modelo-datos-trazabilidad.md` | Modelo de datos de la cadena de custodia |
| `docs/procesos-automatizados.md` | Especificación de los flujos P1–P4 |
| `docs/puesta-en-marcha.md` | Levantar el entorno y checklist de go-live |
| `docs/adr/` | Las cinco decisiones de arquitectura con sus consecuencias |
| `contracts/events/README.md` | Convención de tópicos y esquemas de eventos |
| `marketing/guion-demo-ejecutable.md` | Guion de grabación de la demo |

**Regla del proyecto:** toda decisión de arquitectura, servicio nuevo o cambio de
convención se refleja en `CLAUDE.md` y/o en un ADR **en el mismo PR**.
