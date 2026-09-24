"""Manejo transversal de errores HTTP.

El handler de validación por defecto de FastAPI incluye en la respuesta el valor
recibido. Eso trae dos problemas:

1. Hay entradas que el rechazo no puede serializar de vuelta. `json.loads` de
   Python acepta los literales `Infinity`, `-Infinity` y `NaN` (JSON estándar no),
   así que un cuerpo con `"masa_kg": Infinity` pasa el parser, es correctamente
   rechazado por el modelo... y después revienta al armar el 422, quedando en un
   500 no manejado. Un error del cliente no debe poder convertirse en un error
   del servidor.
2. Devolver la entrada tal cual es eco de datos no confiables hacia quien los mandó.

Acá se responde 422 con campo y motivo, sin reflejar el valor.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def registrar_errores_de_validacion(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validacion(_, exc: RequestValidationError) -> JSONResponse:
        detalles = [
            {"campo": ".".join(str(parte) for parte in error.get("loc", ())),
             "error": str(error.get("msg", ""))}
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={"error": "ValidacionInvalida", "detalles": detalles},
        )
