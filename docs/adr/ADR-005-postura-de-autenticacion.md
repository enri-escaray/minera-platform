# ADR-005 — La autenticación falla cerrada
**Estado:** aceptado (2026-07)

## Contexto
El RBAC transversal (`libs/comun/minera_comun/seguridad.py`) elegía su modo con
`os.getenv("AUTH_MODE", "dev")` y su clave con `os.getenv("AUTH_SECRET", "dev-secret")`.
Un despliegue que olvidara una variable de entorno no fallaba: degradaba a firma
HS256 con un secreto escrito en el repositorio. Cualquiera con acceso al código
podía emitir un token con `roles: ["*"]` y crear lotes, registrar pesajes, cerrar
OTs y despachar camiones. Verificado con una prueba de concepto antes de corregir.

El mismo módulo aceptaba tokens sin `exp` (credenciales permanentes), no validaba
`aud` ni `iss` en modo OIDC, y honraba el comodín `"*"` también contra tokens del
IdP corporativo.

## Decisión
La configuración de autenticación es explícita y su ausencia es un error, no un
valor por defecto.

1. **`AUTH_MODE` no tiene default.** Si falta, `verificar_configuracion()` eleva
   `ConfiguracionInsegura` en el `lifespan` y el servicio no llega a *ready*. En
   caliente, la dependencia responde 503: ninguna ruta de código degrada a "permitir".
2. **`AUTH_SECRET` no tiene default** y exige 32 caracteres (RFC 7518 §3.2 para HS256).
3. **`exp` es obligatorio** en todo modo. Un token sin vencimiento es una credencial
   que no se puede revocar sin rotar el secreto de todo el sitio.
4. **En OIDC se validan `aud` e `iss`.** Sin `aud`, un token emitido para otra
   aplicación del mismo realm era aceptado acá (*confused deputy*).
5. **El comodín `"*"` solo concede en modo `dev`.** Si el IdP puede emitir ese rol,
   un grupo mal nombrado en Keycloak es un superusuario silencioso sobre el sitio.
6. **El claim `sitio` se verifica** contra `SITIO_ID`: un token del sitio A no opera
   el rajo del sitio B. Materializa el aislamiento de zonas (regla 7, IEC 62443).
7. **`AUTH_MODE=off` exige una segunda variable** (`AUTH_OFF_CONFIRMADO`), para que
   ningún copiado de configuración deje un sitio sin autenticación por accidente.
8. **El cliente JWKS se cachea por URL.** Se instanciaba por request, atando cada
   autenticación a un fetch contra el IdP: latencia, amplificación de tráfico y —
   lo grave — dependencia del enlace externo justo en el escenario que ADR-001
   declara prioritario. Con caché, la autenticación sobrevive al modo isla.

## Política de lectura
Las consultas (`/lotes`, `/pesajes`, `/ots`, `/estado`, `/reportes/turno`) no
tenían autenticación. Se resuelve por modo, sin variable nueva que se pueda olvidar:

- `dev`: abiertas. El panel de sitio (`apps/panel`) es estático y sin login, y
  `docker compose up` tiene que seguir siendo un solo comando.
- `oidc`: cerradas. La cadena de custodia es dato regulatorio y la regla 7 del
  proyecto es denegación por defecto.

`AUTH_LECTURA=exigir|abierta` fuerza cualquiera de las dos. Consecuencia: en
producción `reportes` necesita credencial propia (`SERVICIO_TOKEN`) para consultar
`/pesajes` en trazabilidad.

## Consecuencias
+ Un error de despliegue produce un servicio caído y visible, no una puerta abierta.
+ La postura de producción deja de depender de que alguien recuerde una variable.
− Todo despliegue debe declarar `AUTH_MODE` explícitamente; los existentes que
  dependían del default dejan de arrancar (que es exactamente la intención).
− El panel de sitio, tal como está, no funciona contra un sitio en modo `oidc`:
  necesita que el operador aporte un token. Pendiente en el roadmap.
− `SERVICIO_TOKEN` es hoy un token estático de larga vida en el Secret. Es la
  mitad del camino: el flujo *client credentials* con refresco automático contra
  el IdP queda pendiente y merece su propio ADR.
