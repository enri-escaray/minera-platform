# Despliegue edge (K3s)

Orden de aplicación:
```bash
kubectl apply -f 00-namespace.yaml
kubectl apply -f 01-secretos.yaml      # reemplazar por sealed-secrets en prod
kubectl apply -f 21-almacenamiento.yaml
kubectl apply -f 10-db.yaml -f 11-redpanda.yaml -f 12-mqtt.yaml
kubectl apply -f 20-servicios.yaml
kubectl apply -f 30-network-policies.yaml
```

Un sitio nuevo = este mismo directorio con otro `SITIO_ID` (patrón de escalabilidad
horizontal de sitios del diseño). En prod esto se gestiona por GitOps (Argo CD/Flux):
el clúster edge hace pull de su rama, nunca la nube hace push al sitio.

Requisitos del clúster: 3 nodos mínimo, storage RWX (Longhorn), LoadBalancer para
el conducto C1 (MQTT desde la VLAN OT).
