"""Prueba de concepto (Fase 0): ¿los comandos llegan con un enlace de radio pobre?

Simulación determinista, a eventos discretos, de un enlace de radio full dúplex
con la política de diseño del módulo de teleoperación:
  - prioridad estricta: control > datos > video;
  - el video se parte en fragmentos de 1200 B (lo urgente se intercala entre
    fragmentos) y solo vale el ÚLTIMO cuadro: si llega uno nuevo, el viejo se descarta;
  - un comando que espera más que su TTL (300 ms) se descarta: jamás se ejecuta tarde;
  - escalón de video según el ancho de banda (A, B, C o D = solo mapa).

Mide, por escenario: comandos entregados, demora de comandos y de estado,
cuadros de video por segundo y utilización del enlace.

Uso:  python teleop/spike/enlace_spike.py [resultados_camara.json]
      (del JSON de la cámara toma el peso real del JPEG de cada escalón)
"""
from __future__ import annotations

import heapq
import json
import random
import sys
from dataclasses import dataclass, field

SOBRECARGA = 60          # B por mensaje: marco WebSocket + TCP/IP (aprox.)
FRAGMENTO = 1200         # B por fragmento de video
LATENCIA_BASE = 0.020    # s de propagación y procesamiento en cada sentido
TTL_MANDO = 0.300        # s
DURACION = 120.0         # s simulados por escenario

JPEG_POR_DEFECTO = {"A": 14000, "B": 8500, "C": 4000}   # B; se reemplazan con la medición real
ESCALONES = {  # nombre: (fps, ancho de banda mínimo en kbps para usarlo)
    "A": (8, 1300), "B": (6, 600), "C": (4, 200), "D": (0, 0),
}


@dataclass(order=True)
class Mensaje:
    prioridad: int
    creado: float
    seq: int
    clase: str = field(compare=False)
    tam: int = field(compare=False)
    ttl: float | None = field(compare=False, default=None)
    cuadro: int | None = field(compare=False, default=None)


def escalon_para(kbps: float) -> str:
    for nombre in ("A", "B", "C"):
        if kbps >= ESCALONES[nombre][1]:
            return nombre
    return "D"


def simular(kbps: float, perdida: float, jpeg: dict, semilla: int = 1) -> dict:
    rnd = random.Random(semilla)
    bps = kbps * 1000 / 8                      # bytes por segundo en cada sentido
    esc = escalon_para(kbps)
    fps = ESCALONES[esc][0]
    degradado = esc == "D"
    periodo_estado = 0.2 if degradado else 0.1  # en modo degradado el estado va a 5 Hz

    eventos: list[tuple[float, int, str, dict]] = []
    n = 0

    def programar(t, tipo, datos=None):
        nonlocal n
        n += 1
        heapq.heappush(eventos, (t, n, tipo, datos or {}))

    # generadores periódicos
    t = 0.0
    while t < DURACION:
        programar(t, "gen", {"sentido": "subida", "clase": "mando"})          # consola -> máquina, 10 Hz
        t += 0.1
    t = 0.0
    while t < DURACION:
        programar(t, "gen", {"sentido": "bajada", "clase": "estado"})
        t += periodo_estado
    t = 0.0
    while t < DURACION:
        programar(t, "gen", {"sentido": "bajada", "clase": "detecciones"})    # 4 Hz
        t += 0.25
    if fps:
        t, k = 0.0, 0
        while t < DURACION:
            programar(t, "gen", {"sentido": "bajada", "clase": "video", "cuadro": k})
            t += 1 / fps
            k += 1

    colas = {"subida": [], "bajada": []}
    libre = {"subida": 0.0, "bajada": 0.0}      # cuándo termina lo que se está transmitiendo
    ocupado = {"subida": 0.0, "bajada": 0.0}
    seq = 0
    stats = {"mando_enviados": 0, "mando_entregados": 0, "mando_vencidos": 0, "mando_perdidos": 0,
             "demora_mando": [], "demora_estado": [], "cuadros_entregados": 0, "cuadros_descartados": 0,
             "max_sin_mando": 0.0}
    fragmentos_pendientes: dict[int, int] = {}
    ultimo_mando_ok = 0.0

    def encolar(sentido, m):
        heapq.heappush(colas[sentido], m)

    def intentar_enviar(sentido, ahora):
        nonlocal ultimo_mando_ok
        if libre[sentido] > ahora or not colas[sentido]:
            return
        m = heapq.heappop(colas[sentido])
        if m.ttl is not None and ahora - m.creado > m.ttl:
            stats["mando_vencidos"] += 1          # un comando vencido JAMÁS se ejecuta
            programar(ahora, "listo", {"sentido": sentido})
            return
        dur = (m.tam + SOBRECARGA) / bps
        libre[sentido] = ahora + dur
        ocupado[sentido] += dur
        llega = ahora + dur + LATENCIA_BASE
        perdido = rnd.random() < perdida
        programar(ahora + dur, "listo", {"sentido": sentido})
        if not perdido:
            programar(llega, "entrega", {"m": m})
        elif m.clase == "mando":
            stats["mando_perdidos"] += 1

    while eventos:
        ahora, _, tipo, d = heapq.heappop(eventos)
        if ahora > DURACION + 5:
            break
        if tipo == "gen":
            seq += 1
            clase = d["clase"]
            if clase == "mando":
                stats["mando_enviados"] += 1
                encolar("subida", Mensaje(0, ahora, seq, "mando", 210, ttl=TTL_MANDO))
            elif clase == "estado":
                encolar("bajada", Mensaje(1, ahora, seq, "estado", 350))
            elif clase == "detecciones":
                encolar("bajada", Mensaje(1, ahora, seq, "detecciones", 400))
            else:
                # solo el último cuadro: se descartan los fragmentos de cuadros viejos todavía en cola
                viejos = [m for m in colas["bajada"] if m.clase == "video"]
                if viejos:
                    stats["cuadros_descartados"] += len({m.cuadro for m in viejos})
                    colas["bajada"] = [m for m in colas["bajada"] if m.clase != "video"]
                    heapq.heapify(colas["bajada"])
                tam = jpeg[esc]
                partes = max(1, -(-tam // FRAGMENTO))
                fragmentos_pendientes[d["cuadro"]] = partes
                for i in range(partes):
                    encolar("bajada", Mensaje(2, ahora, seq, "video", min(FRAGMENTO, tam - i * FRAGMENTO), cuadro=d["cuadro"]))
            intentar_enviar("subida" if clase == "mando" else "bajada", ahora)
        elif tipo == "listo":
            intentar_enviar(d["sentido"], ahora)
        elif tipo == "entrega":
            m = d["m"]
            demora = ahora - m.creado
            if m.clase == "mando":
                if demora <= m.ttl:
                    stats["mando_entregados"] += 1
                    stats["demora_mando"].append(demora)
                    if ultimo_mando_ok:
                        stats["max_sin_mando"] = max(stats["max_sin_mando"], ahora - ultimo_mando_ok)
                    ultimo_mando_ok = ahora
                else:
                    stats["mando_vencidos"] += 1
            elif m.clase == "estado":
                stats["demora_estado"].append(demora)
            elif m.clase == "video":
                fragmentos_pendientes[m.cuadro] -= 1
                if fragmentos_pendientes[m.cuadro] == 0:
                    stats["cuadros_entregados"] += 1

    def pct(v, p):
        v = sorted(v)
        return round(1000 * v[min(len(v) - 1, int(len(v) * p))], 1) if v else None

    return {
        "kbps": kbps, "perdida_pct": perdida * 100, "escalon_video": esc,
        "comandos_entregados_pct": round(100 * stats["mando_entregados"] / stats["mando_enviados"], 2),
        "comandos_vencidos_descartados": stats["mando_vencidos"],
        "demora_comando_ms_p50": pct(stats["demora_mando"], 0.5), "demora_comando_ms_p99": pct(stats["demora_mando"], 0.99),
        "mayor_hueco_sin_comando_ms": round(1000 * stats["max_sin_mando"]),
        "vigilante_500ms_dispararia": stats["max_sin_mando"] > 0.5,
        "demora_estado_ms_p50": pct(stats["demora_estado"], 0.5), "demora_estado_ms_p99": pct(stats["demora_estado"], 0.99),
        "video_cuadros_por_s": round(stats["cuadros_entregados"] / DURACION, 1),
        "uso_enlace_bajada_pct": round(100 * ocupado["bajada"] / DURACION, 1),
        "uso_enlace_subida_pct": round(100 * ocupado["subida"] / DURACION, 1),
    }


def main() -> None:
    jpeg = dict(JPEG_POR_DEFECTO)
    fuente = "valores por defecto"
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f:
            jpeg.update(json.load(f)["jpeg_bytes"])
        fuente = sys.argv[1]
    print(f"Peso del JPEG por escalón ({fuente}): {jpeg}")
    escenarios = [(1000, 0.0), (256, 0.0), (64, 0.0), (64, 0.02), (32, 0.0)]
    filas = [simular(k, p, jpeg) for k, p in escenarios]
    print(json.dumps(filas, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
