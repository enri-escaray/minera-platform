# Puesta en marcha

Guía de configuración de la plataforma, de la laptop al sitio productivo.

## 1. Desarrollo local (probar todo hoy)

Requisitos: Docker + Docker Compose.

```bash
git init && git add -A && git commit -m "plataforma minera v0.1"
docker compose up -d --build
```

Servicios levantados: trazabilidad :8000, ingesta :8001, mantenimiento :8002,
despacho :8003, reportes :8004, PWA de campo :8080, Postgres :5432,
Redpanda :9092, MQTT :1883. Las migraciones de `db/migrations/` se aplican
solas en el primer arranque de la base.

Token de prueba con todos los roles:
```bash
pip install pyjwt && python -c "import os; os.environ['AUTH_SECRET']='dev-secret'; \
from jwt import encode; print(encode({'sub':'admin','roles':['*']}, 'dev-secret', algorithm='HS256'))"
```

Flujo completo de punta a punta:
```bash
TOKEN=<el de arriba>
# 1. Importar modelo de bloques
python tools/convertir_bloques_csv.py ejemplo.csv vulcan-2026-07 > bloques.json
curl -X POST localhost:8000/bloques/importar -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" -d @bloques.json
# 2. Telemetría → OT predictiva (3 lecturas sostenidas disparan)
for i in 1 2 3; do mosquitto_pub -h localhost -t 'telemetria/cam-101/vibracion_rms' \
     -m "{\"ts\":\"2026-07-18T12:0$i:00Z\",\"valor\":8.5}"; done
curl localhost:8002/ots | jq
# 3. Reporte de turno
curl "localhost:8004/reportes/turno?fecha=2026-07-18&turno=dia" | jq
# 4. Modo isla: docker compose pause redpanda → curl localhost:8001/health
#    (la cola crece) → docker compose unpause redpanda → (drena sola)
```

## 2. Configuración obligatoria antes de producción

| Qué | Dónde | Valor |
|---|---|---|
| `AUTH_MODE` | todos los servicios | `oidc` (jamás `dev` fuera de desarrollo) |
| `OIDC_JWKS_URL` | Secret `minera-secretos` | JWKS del realm de Keycloak/Entra |
| `AUTH_SECRET` | eliminar | no se usa en modo oidc |
| `DATABASE_URL` | Secret | credenciales reales, usuario por servicio |
| `SITIO_ID` | por sitio | único y estable (`sitio-01`, `sitio-02`, …) — prefija los tópicos en nube |
| Contraseñas MQTT + TLS | mosquitto.conf | obligatorio: los sensores autentican |
| Certificados TLS mutuo | DMZ ambos extremos | emitidos por la CA interna, para MirrorMaker |
| `registry.interno` | deploy/k3s/*.yaml | reemplazar por el registry real de imágenes |
| CIDR VLAN OT | deploy/k3s/30-network-policies.yaml | el rango real de la red de sensores (conducto C1) |

## 3. Identidad (Keycloak o Entra ID)

1. Crear realm `minera`, un cliente por servicio (audiencia) y los roles exactos
   del CLAUDE.md: `trazabilidad.escribir`, `geologia.importar`,
   `mantenimiento.planificar`, `mantenimiento.cerrar`, `despacho.configurar`,
   `despacho.operar`, `campo.reportar`, `metalurgia.conciliar`.
2. Mapear el claim `roles` (lista plana) y `sitio` en el token.
3. Grupos sugeridos: Operadores (trazabilidad.escribir, campo.reportar),
   Despachadores (despacho.*), Técnicos (mantenimiento.cerrar),
   Planificación (mantenimiento.planificar), Metalurgia (metalurgia.conciliar),
   Geología (geologia.importar).

## 4. Sitio productivo (edge)

Hardware mínimo por sitio: 3 nodos (8 vCPU / 32 GB / SSD NVMe 1 TB c/u),
switch industrial redundante, UPS. Enlace WAN doble: satelital + celular donde exista.

```bash
# En cada nodo (el primero como server, el resto join):
curl -sfL https://get.k3s.io | sh -s - server --cluster-init
# Storage RWX y balanceador:
kubectl apply -f https://raw.githubusercontent.com/longhorn/longhorn/master/deploy/longhorn.yaml
# Desplegar la plataforma (ver deploy/k3s/README.md para el orden):
kubectl apply -f deploy/k3s/00-namespace.yaml ... etc
```

Reemplazar `01-secretos.yaml` por sealed-secrets o external-secrets: el archivo
del repo es plantilla, nunca contiene valores reales.

## 5. Conexión con OT (conducto C1)

- Los gateways de sensores publican MQTT a `telemetria/{equipo}/{metrica}`
  con payload `{"ts": ISO8601, "valor": número}`, QoS 1.
- Para SCADA/PLC: un colector OPC UA **de solo lectura** en la VLAN OT que
  re-publica a MQTT. Jamás credenciales de escritura hacia OT (regla 3).
- Firewall OT→Edge: solo el puerto 1883 desde el CIDR de la VLAN OT.

## 6. DMZ y nube (conductos C2/C3)

- MirrorMaker 2 corre EN la DMZ con `deploy/replicacion/mm2.properties`:
  completar bootstrap del clúster de nube y certificados.
- En nube: crear los tópicos espejados (`sitio-01.trazabilidad.lotes`, …),
  data lake que consuma de ahí, y la integración ERP que lea `*.mantenimiento.ots`
  (repuestos → órdenes de compra) y publique catálogos por C3.
- C3 (nube→sitio): archivos firmados (planes, reglas predictivas nuevas, catálogos)
  entregados por GitOps al repo del sitio; nunca comandos en caliente.

## 7. Operación diaria

- Métrica centinela: `GET :8001/health` → `cola_pendientes`. Estable ≈ enlace sano;
  crecimiento sostenido = modo isla (esperable) o broker caído (revisar).
- Reglas predictivas: editar `services/mantenimiento/app/reglas.json` vía PR
  (en prod se monta como ConfigMap); el evaluador soporta condición
  `mayor`/`menor` y condiciones adicionales (reglas compuestas).
- Conciliación P4: metalurgia ejecuta `POST /conciliaciones` con los balances
  del día (o se automatiza como job cuando el modelo de procesos esté cargado).
- Backups: `pg_dump` diario del Postgres del sitio a almacenamiento local +
  copia a nube al reconectar; la auditoría JSONL y la outbox están en PVCs
  que Longhorn snapshotea.
- Simulacro trimestral de modo isla: cortar el enlace WAN una hora y verificar
  que despacho, pesajes, OTs y la PWA siguen operando y que todo drena al volver.

## 8. Checklist go-live

- [ ] AUTH_MODE=oidc en todos los servicios y token dev revocado
- [ ] TLS en MQTT, Kafka y APIs; certificados con rotación programada
- [ ] NetworkPolicies aplicadas y verificadas (intentos de conexión no declarada fallan)
- [ ] MirrorMaker replicando y tópicos visibles en nube con prefijo de sitio
- [ ] Simulacro de modo isla ejecutado y documentado
- [ ] Backups probados con una restauración real
- [ ] Roles cargados en el IdP y probados con usuarios reales de cada grupo
- [ ] CI verde en main (5 suites + contratos + migraciones)
