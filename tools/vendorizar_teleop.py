"""Descarga las librerías del módulo de teleoperación a teleop/web/vendor/.

La demo tiene que funcionar sin internet (modo isla), así que nada se carga desde
una CDN en tiempo de ejecución: todo queda copiado en el repo.

Verificación:
- los archivos de npm se comparan contra la huella SHA-256 que publica jsDelivr
  (copiada abajo, en NPM);
- el modelo y los textos de licencia no tienen huella publicada: la primera
  descarga la fija en vendor.lock.json, y a partir de ahí se verifica contra el
  lock (si el servidor cambia el archivo, la descarga falla en vez de aceptarlo).

Uso:
    python tools/vendorizar_teleop.py              descarga lo que falte y verifica todo
    python tools/vendorizar_teleop.py --verificar  solo verifica, sin usar la red
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DESTINO = RAIZ / "teleop" / "web" / "vendor"
LOCK = DESTINO / "vendor.lock.json"
CDN = "https://cdn.jsdelivr.net/npm/{paquete}@{version}/{ruta}"

# (paquete, versión, ruta en el paquete, destino, sha256 publicado por jsDelivr)
NPM = [
    ("three", "0.186.1", "build/three.module.js", "three/three.module.js",
     "9052042d676cb0fdc1ddfefe193053f34b7ac0513a616fdac4535d49987812ea"),
    ("three", "0.186.1", "build/three.core.js", "three/three.core.js",
     "9edde002b066a9a05676a6127f67735b62baf399bdea529f2f7e31657da769e6"),
    ("three", "0.186.1", "examples/jsm/controls/OrbitControls.js", "three/addons/OrbitControls.js",
     "3d79d07ecb686b4e5d93232eedab255331c1beef711e13164eaa1f68655a5f2b"),
    ("three", "0.186.1", "LICENSE", "three/LICENSE",
     "8b378ebe60e2fe500158cb0ac71cb5e8b7d92953c2abcc63a0eb90499653b5bc"),
    ("@tensorflow/tfjs", "4.22.0", "dist/tf.min.js", "tfjs/tf.min.js",
     "300dfae273d20b4046f46a06d735688f03675a807561e9bcb5f664eb2f3d2831"),
    ("@tensorflow-models/coco-ssd", "2.2.3", "dist/coco-ssd.min.js", "coco-ssd/coco-ssd.min.js",
     "8875b8c1bb57723990c9d6506226e0e51cd21b8b6943eb53201086096de52df5"),
]

# Sin huella publicada: se fijan en vendor.lock.json en la primera descarga.
LICENCIAS_APACHE = [
    ("https://raw.githubusercontent.com/tensorflow/tfjs/master/LICENSE", "tfjs/LICENSE"),
    ("https://raw.githubusercontent.com/tensorflow/tfjs-models/master/LICENSE", "coco-ssd/LICENSE"),
]
MODELO_URL = "https://storage.googleapis.com/tfjs-models/savedmodel/ssdlite_mobilenet_v2/"
MODELO_DESTINO = "modelos/ssdlite_mobilenet_v2"

LICENCIAS_MD = """# Librerías de terceros incluidas en teleop/web/vendor/

Se descargan con `python tools/vendorizar_teleop.py` y se verifican con SHA-256
(ver `vendor.lock.json`). Van copiadas en el repo para que la demo funcione sin internet.

| Componente | Versión | Licencia | Origen | Archivos |
|---|---|---|---|---|
| three.js | 0.186.1 | MIT (`three/LICENSE`) | npm `three` | `three/three.module.js`, `three/three.core.js`, `three/addons/OrbitControls.js` |
| TensorFlow.js | 4.22.0 | Apache-2.0 (`tfjs/LICENSE`) | npm `@tensorflow/tfjs` | `tfjs/tf.min.js` |
| COCO-SSD | 2.2.3 | Apache-2.0 (`coco-ssd/LICENSE`) | npm `@tensorflow-models/coco-ssd` | `coco-ssd/coco-ssd.min.js` |
| Modelo SSDLite MobileNet v2 (COCO) | publicado por TensorFlow.js | Apache-2.0 (proyecto tfjs-models) | storage.googleapis.com/tfjs-models | `modelos/ssdlite_mobilenet_v2/*` |

Se declaran también en la entrega del hackatón (declaración de IA y de componentes de terceros).
"""


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 16), b""):
            h.update(bloque)
    return h.hexdigest()


def bajar(url: str, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(destino.suffix + ".parcial")
    req = urllib.request.Request(url, headers={"User-Agent": "mineral360-vendorizar/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        while bloque := r.read(1 << 16):
            f.write(bloque)
    tmp.replace(destino)


class ErrorVerificacion(RuntimeError):
    pass


def asegurar(url: str, rel: str, esperado: str | None, lock: dict, solo_verificar: bool) -> int:
    """Descarga `url` a vendor/`rel` si falta y verifica su huella. Devuelve los bytes."""
    ruta = DESTINO / rel
    esperado = esperado or lock.get(rel, {}).get("sha256")
    if ruta.exists() and esperado and sha256(ruta) == esperado:
        return ruta.stat().st_size
    if solo_verificar:
        raise ErrorVerificacion(f"{rel}: falta o no coincide con la huella fijada")
    bajar(url, ruta)
    real = sha256(ruta)
    if esperado and real != esperado:
        ruta.unlink()
        raise ErrorVerificacion(f"{rel}: la huella no coincide (esperada {esperado[:12]}…, recibida {real[:12]}…)")
    lock[rel] = {"url": url, "sha256": real, "bytes": ruta.stat().st_size}
    return ruta.stat().st_size


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--verificar", action="store_true", help="solo verificar, sin descargar")
    args = ap.parse_args()

    lock = json.loads(LOCK.read_text(encoding="utf-8")) if LOCK.exists() else {}
    total = 0
    try:
        for paquete, version, ruta, rel, huella in NPM:
            url = CDN.format(paquete=paquete, version=version, ruta=ruta)
            total += asegurar(url, rel, huella, lock, args.verificar)
        for url, rel in LICENCIAS_APACHE:
            total += asegurar(url, rel, None, lock, args.verificar)
        rel_json = f"{MODELO_DESTINO}/model.json"
        total += asegurar(MODELO_URL + "model.json", rel_json, None, lock, args.verificar)
        manifiesto = json.loads((DESTINO / rel_json).read_text(encoding="utf-8"))
        for grupo in manifiesto.get("weightsManifest", []):
            for nombre in grupo["paths"]:
                total += asegurar(MODELO_URL + nombre, f"{MODELO_DESTINO}/{nombre}", None, lock, args.verificar)
    except ErrorVerificacion as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not args.verificar:
        LOCK.write_text(json.dumps(dict(sorted(lock.items())), indent=2) + "\n", encoding="utf-8")
        (DESTINO / "LICENCIAS.md").write_text(LICENCIAS_MD, encoding="utf-8")
    print(f"OK: {len(lock)} archivos verificados en {DESTINO.relative_to(RAIZ)} ({total / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
