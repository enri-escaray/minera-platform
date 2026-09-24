# Mineral360 — Plataforma Minera

Software para operaciones mineras en entornos críticos y remotos: trazabilidad del
mineral, despacho de flota, mantenimiento predictivo e integración ERP.
Arquitectura edge + nube segmentada según IEC 62443.

**La mina no puede parar aunque se caiga el enlace con el exterior.** El sistema
corre dentro del sitio y sigue operando sin conectividad; sincroniza al reconectar.

```bash
docker compose up -d            # sitio completo local (10 contenedores)
curl localhost:8001/health      # cola de ingesta: la métrica centinela del enlace
```

Panel de sitio en `http://localhost:8081`, app de campo en `http://localhost:8080`.

## Por dónde empezar

- **`docs/descripcion-del-proyecto.md`** — qué es, cómo está construido, qué
  funciona y qué falta. El panorama completo en un solo documento.
- **`CLAUDE.md`** — mapa del repo y convenciones. Empezar acá para tocar código.
- **`docs/puesta-en-marcha.md`** — levantar el entorno y checklist de go-live.
