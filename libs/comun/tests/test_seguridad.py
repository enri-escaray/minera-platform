"""Tests del borde de autenticación.

Cada test acá corresponde a una forma concreta de entrar sin permiso. No prueban
que el código "funcione": prueban que el código **se niega**. Si alguno de estos
empieza a fallar, hay una puerta abierta, no un test roto.
"""
from __future__ import annotations

import time

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from minera_comun import (
    ConfiguracionInsegura,
    Principal,
    emitir_token_dev,
    requerir_lectura,
    requerir_rol,
    verificar_configuracion,
)
from minera_comun.seguridad import _cliente_jwks

SECRETO = "secreto-de-test-no-usar-en-produccion"


@pytest.fixture(autouse=True)
def limpiar_entorno(monkeypatch):
    for v in ("AUTH_MODE", "AUTH_SECRET", "SITIO_ID", "AUTH_LECTURA",
              "AUTH_OFF_CONFIRMADO", "OIDC_JWKS_URL", "OIDC_AUDIENCE", "OIDC_ISSUER"):
        monkeypatch.delenv(v, raising=False)
    _cliente_jwks.cache_clear()


def app_con(guarda) -> TestClient:
    app = FastAPI()

    @app.get("/recurso")
    async def recurso(p=Depends(guarda)):
        return {"sub": getattr(p, "sub", None)}

    return TestClient(app)


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------- configuración insegura

def test_sin_auth_mode_no_arranca():
    """El fallo original: sin AUTH_MODE se caía a dev + secreto publicado en el
    código, así que cualquiera firmaba un token con rol '*'."""
    with pytest.raises(ConfiguracionInsegura):
        verificar_configuracion()


def test_sin_auth_mode_la_peticion_es_503_no_200(monkeypatch):
    cliente = app_con(requerir_rol("cualquiera"))
    r = cliente.get("/recurso", headers=auth("lo-que-sea"))
    assert r.status_code == 503  # jamás degradar a "permitir"


def test_dev_sin_secreto_no_arranca(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "dev")
    with pytest.raises(ConfiguracionInsegura):
        verificar_configuracion()


def test_dev_con_secreto_corto_no_arranca(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("AUTH_SECRET", "corto")
    with pytest.raises(ConfiguracionInsegura):
        verificar_configuracion()


def test_auth_mode_desconocido_no_arranca(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "produccion")  # typo plausible
    with pytest.raises(ConfiguracionInsegura):
        verificar_configuracion()


def test_off_exige_confirmacion_explicita(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "off")
    with pytest.raises(ConfiguracionInsegura):
        verificar_configuracion()
    monkeypatch.setenv("AUTH_OFF_CONFIRMADO", "si-entiendo-el-riesgo")
    assert verificar_configuracion() == "off"


def test_oidc_exige_audiencia_y_emisor(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "oidc")
    monkeypatch.setenv("OIDC_JWKS_URL", "https://idp.interno/certs")
    with pytest.raises(ConfiguracionInsegura):
        verificar_configuracion()  # falta OIDC_AUDIENCE
    monkeypatch.setenv("OIDC_AUDIENCE", "minera-sitio")
    with pytest.raises(ConfiguracionInsegura):
        verificar_configuracion()  # falta OIDC_ISSUER
    monkeypatch.setenv("OIDC_ISSUER", "https://idp.interno/realms/minera")
    assert verificar_configuracion() == "oidc"


# ------------------------------------------------------------ validación de token

def test_token_firmado_con_otro_secreto_es_401(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("AUTH_SECRET", SECRETO)
    ajeno = jwt.encode({"sub": "atacante", "roles": ["*"], "exp": time.time() + 60},
                       "otro-secreto-cualquiera", algorithm="HS256")
    r = app_con(requerir_rol("algo")).get("/recurso", headers=auth(ajeno))
    assert r.status_code == 401


def test_token_sin_exp_es_401(monkeypatch):
    """Un token sin vencimiento es una credencial permanente: si se filtra, no
    hay forma de revocarla salvo rotar el secreto de todo el sitio."""
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("AUTH_SECRET", SECRETO)
    eterno = jwt.encode({"sub": "yo", "roles": ["algo"]}, SECRETO, algorithm="HS256")
    r = app_con(requerir_rol("algo")).get("/recurso", headers=auth(eterno))
    assert r.status_code == 401


def test_token_vencido_es_401(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("AUTH_SECRET", SECRETO)
    vencido = emitir_token_dev("yo", ["algo"], vigencia_min=-1)
    r = app_con(requerir_rol("algo")).get("/recurso", headers=auth(vencido))
    assert r.status_code == 401


def test_token_alg_none_es_401(monkeypatch):
    """Ataque clásico: cambiar el alg a 'none' para saltear la firma."""
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("AUTH_SECRET", SECRETO)
    sin_firma = jwt.encode({"sub": "atacante", "roles": ["*"], "exp": time.time() + 60},
                           key="", algorithm="none")
    r = app_con(requerir_rol("algo")).get("/recurso", headers=auth(sin_firma))
    assert r.status_code == 401


def test_sin_token_es_401(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("AUTH_SECRET", SECRETO)
    assert app_con(requerir_rol("algo")).get("/recurso").status_code == 401


def test_rol_insuficiente_es_403(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("AUTH_SECRET", SECRETO)
    r = app_con(requerir_rol("despacho.operar")).get(
        "/recurso", headers=auth(emitir_token_dev("x", ["reportes.leer"])))
    assert r.status_code == 403


# ------------------------------------------------------------------- rol comodín

def test_comodin_funciona_en_dev(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("AUTH_SECRET", SECRETO)
    r = app_con(requerir_rol("lo.que.sea")).get(
        "/recurso", headers=auth(emitir_token_dev("dev", ["*"])))
    assert r.status_code == 200


def test_comodin_no_concede_nada_fuera_de_dev(monkeypatch):
    """Si el IdP puede emitir roles:['*'], un grupo mal nombrado en Keycloak es
    un superusuario silencioso sobre todo el sitio."""
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("AUTH_SECRET", SECRETO)
    token = emitir_token_dev("x", ["*"])

    # El mismo token, evaluado con el servicio en modo producción.
    monkeypatch.setenv("AUTH_MODE", "oidc")
    monkeypatch.setenv("OIDC_JWKS_URL", "https://idp.interno/certs")
    monkeypatch.setenv("OIDC_AUDIENCE", "minera-sitio")
    monkeypatch.setenv("OIDC_ISSUER", "https://idp.interno/realms/minera")
    r = app_con(requerir_rol("despacho.operar")).get("/recurso", headers=auth(token))
    assert r.status_code == 401  # ni siquiera valida: no está firmado por el IdP


# -------------------------------------------------------------- aislamiento sitio

def test_token_de_otro_sitio_es_403(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("AUTH_SECRET", SECRETO)
    monkeypatch.setenv("SITIO_ID", "sitio-01")
    ajeno = emitir_token_dev("operador", ["despacho.operar"], sitio="sitio-02")
    r = app_con(requerir_rol("despacho.operar")).get("/recurso", headers=auth(ajeno))
    assert r.status_code == 403


def test_token_del_sitio_propio_pasa(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("AUTH_SECRET", SECRETO)
    monkeypatch.setenv("SITIO_ID", "sitio-01")
    propio = emitir_token_dev("operador", ["despacho.operar"], sitio="sitio-01")
    r = app_con(requerir_rol("despacho.operar")).get("/recurso", headers=auth(propio))
    assert r.status_code == 200


# ---------------------------------------------------------- política de lectura

def test_lectura_abierta_en_dev(monkeypatch):
    """El panel de sitio es estático y sin login: en dev la consulta no pide token."""
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("AUTH_SECRET", SECRETO)
    assert app_con(requerir_lectura("x.leer")).get("/recurso").status_code == 200


def test_lectura_exigida_cierra_la_consulta(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("AUTH_SECRET", SECRETO)
    monkeypatch.setenv("AUTH_LECTURA", "exigir")
    cliente = app_con(requerir_lectura("x.leer"))
    assert cliente.get("/recurso").status_code == 401
    r = cliente.get("/recurso", headers=auth(emitir_token_dev("lector", ["x.leer"])))
    assert r.status_code == 200


def test_jwks_se_cachea_entre_peticiones(monkeypatch):
    """Instanciar el cliente JWKS por request ataba cada autenticación a un
    fetch HTTP contra el IdP: latencia, amplificación y dependencia del enlace
    externo justo en el modo isla (ADR-001)."""
    monkeypatch.setenv("OIDC_JWKS_TTL_S", "3600")
    primero = _cliente_jwks("https://idp.interno/certs")
    segundo = _cliente_jwks("https://idp.interno/certs")
    assert primero is segundo
