"""RBAC transversal con JWT.

Modos (env AUTH_MODE):
- "dev"  (default): HS256 con AUTH_SECRET. Solo para desarrollo y tests.
- "oidc": RS256 validado contra el JWKS del proveedor (Keycloak/Entra), env OIDC_JWKS_URL.
- "off":  sin autenticación. Prohibido fuera de pruebas locales explícitas.

Claims esperados: sub, roles (lista), sitio. Un rol "*" concede todo (solo dev).
La verificación de rol es por intersección: el endpoint declara qué roles lo habilitan.
"""
from __future__ import annotations

import os

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

_bearer = HTTPBearer(auto_error=False)


class Principal(BaseModel):
    sub: str
    roles: list[str]
    sitio: str = "*"


def _decodificar(token: str) -> dict:
    import jwt  # PyJWT, import perezoso

    modo = os.getenv("AUTH_MODE", "dev")
    if modo == "dev":
        return jwt.decode(
            token,
            os.getenv("AUTH_SECRET", "dev-secret"),
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
    if modo == "oidc":
        cliente = jwt.PyJWKClient(os.environ["OIDC_JWKS_URL"])
        clave = cliente.get_signing_key_from_jwt(token)
        return jwt.decode(token, clave.key, algorithms=["RS256"], options={"verify_aud": False})
    raise HTTPException(status_code=500, detail=f"AUTH_MODE inválido: {modo}")


def requerir_rol(*roles_permitidos: str):
    """Dependencia FastAPI: exige un JWT válido con alguno de los roles dados."""

    async def dependencia(
        cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> Principal:
        if os.getenv("AUTH_MODE", "dev") == "off":
            return Principal(sub="anonimo", roles=["*"])
        if cred is None:
            raise HTTPException(status_code=401, detail="token requerido")
        try:
            claims = _decodificar(cred.credentials)
        except HTTPException:
            raise
        except Exception as exc:  # firma inválida, expirado, malformado
            raise HTTPException(status_code=401, detail="token inválido") from exc
        principal = Principal(
            sub=str(claims.get("sub", "")),
            roles=list(claims.get("roles", [])),
            sitio=str(claims.get("sitio", "*")),
        )
        if "*" not in principal.roles and not set(roles_permitidos) & set(principal.roles):
            raise HTTPException(status_code=403, detail="rol insuficiente")
        return principal

    return dependencia


def emitir_token_dev(sub: str, roles: list[str], sitio: str = "sitio-dev") -> str:
    """Solo para tests y desarrollo local (HS256 con AUTH_SECRET)."""
    import jwt

    return jwt.encode(
        {"sub": sub, "roles": roles, "sitio": sitio},
        os.getenv("AUTH_SECRET", "dev-secret"),
        algorithm="HS256",
    )
