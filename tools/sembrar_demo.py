# -*- coding: utf-8 -*-
"""Siembra datos de demostración en el stack local (docker compose up -d).

Uso:  python tools/sembrar_demo.py
Idempotente por día: si ya se corrió hoy, los conflictos (409) se informan y
se continúa. Los pesajes se fechan dentro del turno en curso para que el panel
muestre toneladas en "Turno en curso". La telemetría NO se siembra acá: dispararla
en vivo es parte de la demo (ver marketing/guion-demo-ejecutable.md).
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from uuid import uuid4

TRAZABILIDAD = os.getenv("DEMO_TRAZABILIDAD", "http://localhost:8000")
DESPACHO = os.getenv("DEMO_DESPACHO", "http://localhost:8003")
REPORTES = os.getenv("DEMO_REPORTES", "http://localhost:8004")


def token() -> str:
    """Token de demo: mismo secreto que docker-compose, con vencimiento.

    `exp` es obligatorio (los servicios rechazan tokens eternos) y el rol `*`
    solo concede algo con AUTH_MODE=dev.
    """
    import jwt  # PyJWT

    ahora = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": "demo", "roles": ["*"], "sitio": "sitio-dev",
         "iat": ahora, "exp": ahora + timedelta(hours=1)},
        os.getenv("AUTH_SECRET", "dev-secret-solo-para-desarrollo-local"),
        algorithm="HS256",
    )


def post(url: str, cuerpo: dict, tok: str) -> tuple[int, dict]:
    req = urllib.request.Request(
        url, data=json.dumps(cuerpo).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except (ValueError, OSError):
            return e.code, {}


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # consola Windows
    tok = token()
    ahora = datetime.now(timezone.utc)
    # El turno se etiqueta con el día en que EMPEZÓ (misma convención que el
    # scheduler de reportes y que el panel): entre medianoche y las 07:00 el
    # turno noche en curso arrancó ayer a las 19:00. Con la fecha de hoy, la
    # novedad quedaba colgada de una ventana futura y no salía en el reporte.
    inicio_turno = ahora - timedelta(days=1) if ahora.hour < 7 else ahora
    fecha = inicio_turno.strftime("%Y-%m-%d")
    dia = ahora.strftime("%Y%m%d")
    turno = "dia" if 7 <= ahora.hour < 19 else "noche"

    def paso(nombre, status):
        marca = "ok" if status < 300 else ("ya existía / no disponible" if status == 409 else f"ERROR {status}")
        print(f"  {nombre}: {marca}")

    print(f"Sembrando demo — {fecha}, turno {turno} (UTC)")

    print("Despacho:")
    # No re-registrar lo que ya existe: el alta pisa el estado del equipo
    # (y un camión en ciclo volvería a figurar "disponible").
    estado = get(f"{DESPACHO}/estado")
    palas_existentes = {p["id"] for p in estado["palas"]}
    camiones = {c["id"]: c["estado"] for c in estado["camiones"]}
    for pala in ({"id": "pala-01", "ubicacion_id": "fase-2-banco-3050", "tiempo_carga_min": 3.5, "prioridad_mezcla": 2},
                 {"id": "pala-02", "ubicacion_id": "fase-1-banco-3020", "tiempo_carga_min": 4.0, "prioridad_mezcla": 1}):
        if pala["id"] in palas_existentes:
            paso(pala["id"], 409)
            continue
        st, _ = post(f"{DESPACHO}/palas", pala, tok); paso(pala["id"], st)
    for c in ("cam-101", "cam-102", "cam-103"):
        if c in camiones:
            paso(c, 409)
            continue
        st, _ = post(f"{DESPACHO}/camiones", {"id": c, "capacidad_ton": 220}, tok)
        paso(c, st)
        camiones[c] = "disponible"
    for camion, viajes in (("cam-101", {"pala-01": 6, "pala-02": 9}),
                           ("cam-102", {"pala-01": 5, "pala-02": 7})):
        if camiones.get(camion) != "disponible":
            paso(f"asignación {camion} (ya en ciclo)", 409)
            continue
        st, _ = post(f"{DESPACHO}/asignaciones", {"camion_id": camion, "viaje_min": viajes}, tok)
        paso(f"asignación {camion}", st)

    print("Trazabilidad:")
    ids = {}
    for suf in ("A", "B"):
        st, cuerpo = post(f"{TRAZABILIDAD}/lotes",
                          {"codigo": f"LT-{dia}-{suf}", "tipo": "rom", "event_id": str(uuid4())}, tok)
        paso(f"LT-{dia}-{suf}", st)
        if st < 300:
            ids[suf] = cuerpo["id"]
    if ids:
        medido = (ahora - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for suf, instrumento, masa in (("A", "bascula-01", 218500), ("A", "bascula-01", 224000),
                                       ("B", "pesometro-c1", 198700)):
            if suf in ids:
                st, _ = post(f"{TRAZABILIDAD}/pesajes",
                             {"event_id": str(uuid4()), "lote_id": ids[suf], "instrumento_id": instrumento,
                              "masa_kg": masa, "medido_en": medido}, tok)
                paso(f"pesaje {masa/1000:.1f} t ({suf})", st)
        if len(ids) == 2:
            st, _ = post(f"{TRAZABILIDAD}/lotes",
                         {"codigo": f"LT-STOCK-{dia}", "tipo": "stock",
                          "lotes_origen": [ids["A"], ids["B"]], "event_id": str(uuid4())}, tok)
            paso(f"LT-STOCK-{dia} (mezcla)", st)

    print("Reportes:")
    st, _ = post(f"{REPORTES}/novedades",
                 {"event_id": str(uuid4()), "fecha": fecha, "turno": turno, "categoria": "seguridad",
                  "texto": "Derrame menor contenido en pala PH-01; área señalizada"}, tok)
    paso("novedad de seguridad", st)

    print(f"\nListo. Panel: http://localhost:8081  (custodia directa: ?lote=LT-STOCK-{dia})")


if __name__ == "__main__":
    sys.exit(main())
