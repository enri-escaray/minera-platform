"""Servidor estático local para las pruebas de concepto de teleop/ (solo 127.0.0.1).

En Windows, `python -m http.server` puede tomar del registro un tipo MIME
incorrecto para .js, y el navegador se niega a cargar módulos así. Acá se fijan.

Uso:  python teleop/spike/servir.py [puerto]   (por defecto 8090; sirve la carpeta teleop/)
"""
import functools
import http.server
import sys
from pathlib import Path

TIPOS = {".js": "text/javascript", ".mjs": "text/javascript", ".json": "application/json",
         ".html": "text/html; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg", "": "application/octet-stream"}


class Manejador(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, **TIPOS}

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")  # en pruebas, siempre la última versión
        super().end_headers()


if __name__ == "__main__":
    puerto = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    raiz = Path(__file__).resolve().parents[1]
    manejador = functools.partial(Manejador, directory=str(raiz))
    with http.server.ThreadingHTTPServer(("127.0.0.1", puerto), manejador) as srv:
        print(f"Sirviendo {raiz} en http://127.0.0.1:{puerto}/", flush=True)
        srv.serve_forever()
