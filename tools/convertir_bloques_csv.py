#!/usr/bin/env python3
"""Adaptador de vendor (ADR-004): export CSV de Deswik/Vulcan → contrato de import.

Uso:
    python tools/convertir_bloques_csv.py modelo.csv vulcan-2026-07 > bloques.json
    curl -X POST $TRAZABILIDAD/bloques/importar -H "Authorization: Bearer $TOKEN" \
         -H "Content-Type: application/json" -d @bloques.json

CSV esperado (cabeceras): id,x,y,z,<elemento1>,<elemento2>,...
Las columnas de leyes se detectan automáticamente (todo lo que no sea id/x/y/z).
"""
import csv
import json
import sys
from uuid import uuid4


def convertir(ruta_csv: str, origen_modelo: str) -> dict:
    with open(ruta_csv, newline="", encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    if not filas:
        raise SystemExit("CSV vacío")
    columnas_ley = [c for c in filas[0] if c not in ("id", "x", "y", "z")]
    bloques = [
        {
            "id": fila["id"],
            "centroide_x": float(fila["x"]),
            "centroide_y": float(fila["y"]),
            "cota": float(fila["z"]),
            "leyes_estimadas": {c: float(fila[c]) for c in columnas_ley if fila[c] != ""},
        }
        for fila in filas
    ]
    return {"event_id": str(uuid4()), "origen_modelo": origen_modelo, "bloques": bloques}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    json.dump(convertir(sys.argv[1], sys.argv[2]), sys.stdout, ensure_ascii=False, indent=2)
