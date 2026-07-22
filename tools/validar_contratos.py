#!/usr/bin/env python3
"""Valida que todos los contratos de eventos sean JSON Schema Draft-07 válidos
y cumplan las reglas del repo (envelope obligatorio). Corre en CI."""
import glob
import json
import sys

from jsonschema import Draft7Validator

ENVELOPE = {"event_id", "event_type", "version", "sitio_id", "occurred_at", "data"}
errores = 0
for ruta in sorted(glob.glob("contracts/events/*.json")):
    esquema = json.load(open(ruta, encoding="utf-8"))
    try:
        Draft7Validator.check_schema(esquema)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR {ruta}: esquema inválido: {exc}")
        errores += 1
        continue
    faltan = ENVELOPE - set(esquema.get("required", []))
    if faltan:
        print(f"ERROR {ruta}: faltan campos de envelope en required: {sorted(faltan)}")
        errores += 1
    else:
        print(f"ok    {ruta}")
sys.exit(1 if errores else 0)
