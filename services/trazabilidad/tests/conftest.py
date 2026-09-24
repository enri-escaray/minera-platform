"""Fixtures compartidas: outbox aislada por test y tokens de dev para RBAC."""
import os

import pytest
from fastapi.testclient import TestClient

from minera_comun import emitir_token_dev


@pytest.fixture(autouse=True)
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTBOX_DIR", str(tmp_path / "outbox"))
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("AUTH_SECRET", "secreto-de-test-no-usar-en-produccion")


@pytest.fixture()
def token_operador():
    return emitir_token_dev("operador-1", ["trazabilidad.escribir"])


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c
