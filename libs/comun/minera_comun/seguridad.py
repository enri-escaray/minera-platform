"""RBAC transversal con JWT.

Modos (env AUTH_MODE — **obligatorio, sin default**):
- "dev":  HS256 con AUTH_SECRET. Solo para desarrollo y tests.
- "oidc": RS256/ES256 validado contra el JWKS del proveedor (Keycloak/Entra).
- "off":  sin autenticación. Exige además AUTH_OFF_CONFIRMADO=si-entiendo-el-riesgo.

Por qué AUTH_MODE no tiene default: un default silencioso convierte un olvido de
configuración en un bypass total de autenticación. Si la variable falta, el
servicio se niega a arrancar (fallo cerrado) en vez de aceptar tokens firmados
con un secreto que está publicado en el código fuente.

Claims esperados: sub, roles (lista), sitio, exp. El rol "*" concede todo pero
**solo en modo dev**: un IdP real jamás debe poder emitir un superusuario.
La verificación de rol es por intersección: el endpoint declara qué roles lo habilitan.

Aislamiento por sitio (regla 7 de CLAUDE.md, zonas y conductos IEC 62443): si el
proceso declara SITIO_ID, un token emitido para otro sitio se rechaza. Un
operador del sitio A no opera el rajo del sitio B.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

log = logging.getLogger("minera.seguridad")

_bearer = HTTPBearer(auto_error=False)

MODOS_VALIDOS = ("dev", "oidc", "off")
_LARGO_MINIMO_SECRETO = 32  # RFC 7518 §3.2: la clave HMAC no debe ser menor al hash (SHA-256)


class ConfiguracionInsegura(RuntimeError):
    """La configuración de autenticación es ambigua o insegura: no se arranca.

    Se eleva al arrancar (verificar_configuracion) y también en caliente, para
    que ninguna ruta de código pueda terminar autenticando por accidente.
    """


class Principal(BaseModel):
    sub: str
    roles: list[str]
    sitio: str = "*"


def modo_auth() -> str:
    """Modo declarado. Falla si falta o es desconocido: nunca adivina."""
    modo = os.getenv("AUTH_MODE")
    if not modo:
        raise ConfiguracionInsegura(
            "AUTH_MODE no está definido. Declararlo explícitamente: 'oidc' en "
            "producción, 'dev' solo en desarrollo. No hay valor por defecto a "
            "propósito: un default silencioso convertiría un olvido de "
            "configuración en un bypass de autenticación."
        )
    if modo not in MODOS_VALIDOS:
        raise ConfiguracionInsegura(
            f"AUTH_MODE inválido: {modo!r}. Válidos: {', '.join(MODOS_VALIDOS)}."
        )
    return modo


def _secreto_dev() -> str:
    secreto = os.getenv("AUTH_SECRET")
    if not secreto:
        raise ConfiguracionInsegura(
            "AUTH_MODE=dev exige AUTH_SECRET. Sin él, el secreto sería un valor "
            "conocido y cualquiera podría emitir tokens con rol '*'."
        )
    if len(secreto) < _LARGO_MINIMO_SECRETO:
        raise ConfiguracionInsegura(
            f"AUTH_SECRET debe tener al menos {_LARGO_MINIMO_SECRETO} caracteres "
            f"(tiene {len(secreto)}); RFC 7518 §3.2 para HS256."
        )
    return secreto


def _requerido(nombre: str, motivo: str) -> str:
    valor = os.getenv(nombre)
    if not valor:
        raise ConfiguracionInsegura(f"AUTH_MODE=oidc exige {nombre}: {motivo}")
    return valor


@lru_cache(maxsize=4)
def _cliente_jwks(url: str):
    """Cliente JWKS cacheado por URL.

    Instanciarlo por request (como se hacía antes) obliga a un fetch HTTP al IdP
    en cada llamada: agrega latencia, amplifica tráfico contra el IdP y hace que
    toda la autenticación del sitio dependa del enlace — inaceptable en modo isla
    (ADR-001). Con caché, las claves sobreviven a un corte del enlace externo.
    """
    import jwt

    return jwt.PyJWKClient(url, cache_keys=True, lifespan=int(os.getenv("OIDC_JWKS_TTL_S", "3600")))


def _decodificar(token: str) -> dict:
    import jwt  # PyJWT, import perezoso

    modo = modo_auth()
    # exp obligatorio en todo modo: un token sin vencimiento es una credencial
    # permanente, y una credencial permanente filtrada no se puede revocar.
    comunes = {"require": ["exp", "sub"], "verify_exp": True}
    if modo == "dev":
        return jwt.decode(
            token,
            _secreto_dev(),
            algorithms=["HS256"],
            options={**comunes, "verify_aud": False},
        )
    if modo == "oidc":
        url = _requerido("OIDC_JWKS_URL", "endpoint JWKS del proveedor")
        audiencia = _requerido(
            "OIDC_AUDIENCE",
            "sin validar 'aud' se acepta cualquier token del mismo realm, "
            "aunque haya sido emitido para otra aplicación (confused deputy)",
        )
        emisor = _requerido("OIDC_ISSUER", "sin validar 'iss' se acepta cualquier emisor")
        clave = _cliente_jwks(url).get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            clave.key,
            algorithms=["RS256", "RS384", "RS512", "ES256", "ES384"],
            audience=audiencia,
            issuer=emisor,
            options={**comunes, "verify_aud": True, "verify_iss": True},
        )
    raise ConfiguracionInsegura(f"AUTH_MODE inválido: {modo}")


def _verificar_sitio(principal: Principal, modo: str) -> None:
    """Aislamiento entre zonas: el token debe pertenecer a este sitio."""
    sitio_local = os.getenv("SITIO_ID")
    if not sitio_local:
        return  # proceso sin sitio declarado (tests, herramientas): no aplica
    if principal.sitio == sitio_local:
        return
    # El comodín cruza sitios; en producción eso rompe el aislamiento de zonas.
    if principal.sitio == "*" and modo == "dev":
        return
    raise HTTPException(
        status_code=403,
        detail=f"token emitido para el sitio {principal.sitio!r}, este es {sitio_local!r}",
    )


def verificar_configuracion() -> str:
    """Valida la configuración de auth al arrancar. Llamar desde el lifespan.

    Que el servicio no llegue a estado 'ready' con auth mal configurada es lo que
    convierte un error de despliegue en una falla visible en vez de en una
    puerta abierta.
    """
    modo = modo_auth()
    if modo == "dev":
        _secreto_dev()
        log.warning(
            "AUTH_MODE=dev: firma HS256 con secreto compartido. Solo desarrollo; "
            "en producción usar AUTH_MODE=oidc."
        )
    elif modo == "oidc":
        _requerido("OIDC_JWKS_URL", "endpoint JWKS del proveedor")
        _requerido("OIDC_AUDIENCE", "identificador de esta aplicación en el IdP")
        _requerido("OIDC_ISSUER", "emisor esperado del token")
    elif modo == "off":
        if os.getenv("AUTH_OFF_CONFIRMADO") != "si-entiendo-el-riesgo":
            raise ConfiguracionInsegura(
                "AUTH_MODE=off deja todas las escrituras sin autenticación. Si es "
                "realmente lo que se quiere, declarar además "
                "AUTH_OFF_CONFIRMADO=si-entiendo-el-riesgo."
            )
        log.error("AUTH_MODE=off: SIN AUTENTICACIÓN. Prohibido fuera de pruebas locales.")
    return modo


def requerir_rol(*roles_permitidos: str):
    """Dependencia FastAPI: exige un JWT válido con alguno de los roles dados."""

    async def dependencia(
        cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> Principal:
        try:
            modo = modo_auth()
            if modo == "off":
                verificar_configuracion()  # exige la confirmación explícita
                return Principal(sub="anonimo", roles=["*"])
        except ConfiguracionInsegura as exc:
            # Nunca degradar a "permitir": config mal puesta = servicio no disponible.
            log.error("configuración de auth inválida: %s", exc)
            raise HTTPException(status_code=503, detail="auth mal configurada") from exc
        if cred is None:
            raise HTTPException(status_code=401, detail="token requerido")
        try:
            claims = _decodificar(cred.credentials)
        except ConfiguracionInsegura as exc:
            log.error("configuración de auth inválida: %s", exc)
            raise HTTPException(status_code=503, detail="auth mal configurada") from exc
        except HTTPException:
            raise
        except Exception as exc:  # firma inválida, expirado, sin exp, malformado
            raise HTTPException(status_code=401, detail="token inválido") from exc
        principal = Principal(
            sub=str(claims.get("sub", "")),
            roles=[str(r) for r in claims.get("roles", [])],
            sitio=str(claims.get("sitio", "*")),
        )
        _verificar_sitio(principal, modo)
        # El comodín es una comodidad de desarrollo, no una credencial de producción:
        # si el IdP puede emitir roles:["*"], un grupo mal nombrado es un superusuario.
        comodin = modo == "dev" and "*" in principal.roles
        if not comodin and not set(roles_permitidos) & set(principal.roles):
            raise HTTPException(status_code=403, detail="rol insuficiente")
        return principal

    return dependencia


def requerir_lectura(*roles_permitidos: str):
    """Guarda de los endpoints de consulta.

    En `dev` las consultas quedan abiertas: el panel de sitio (apps/panel) es una
    página estática sin login y el demo debe correr con `docker compose up`.
    En `oidc` (producción) se exige token: la cadena de custodia es dato
    regulatorio y la regla 7 del proyecto es denegación por defecto entre zonas.
    Se puede forzar cualquiera de los dos con AUTH_LECTURA=exigir|abierta.
    """

    guarda = requerir_rol(*roles_permitidos)

    async def dependencia(
        cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> Principal | None:
        politica = os.getenv("AUTH_LECTURA", "").lower()
        if politica == "abierta":
            return None
        if politica != "exigir" and modo_auth() != "oidc":
            return None
        return await guarda(cred)

    return dependencia


def emitir_token_dev(
    sub: str,
    roles: list[str],
    sitio: str = "sitio-dev",
    *,
    vigencia_min: int = 60,
) -> str:
    """Solo para tests y desarrollo local (HS256 con AUTH_SECRET).

    Emite siempre `exp`: un token de dev sin vencimiento termina pegado en un
    script, un README o el historial de una terminal, y sigue sirviendo un año después.
    """
    import jwt

    ahora = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": sub,
            "roles": roles,
            "sitio": sitio,
            "iat": ahora,
            "exp": ahora + timedelta(minutes=vigencia_min),
        },
        _secreto_dev(),
        algorithm="HS256",
    )
