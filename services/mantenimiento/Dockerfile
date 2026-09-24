FROM python:3.12-slim
# Usuario sin privilegios: si alguien logra ejecucion dentro del contenedor,
# root dentro del contenedor es el primer escalon hacia el nodo (montajes,
# capabilities, sockets). El proceso no necesita root para escuchar en >1024.
RUN useradd --system --uid 10001 --no-create-home minera
WORKDIR /srv
COPY services/mantenimiento/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY libs/comun ./libs/comun
RUN pip install --no-cache-dir ./libs/comun
COPY services/mantenimiento/app ./app
EXPOSE 8002
# Un volumen nombrado hereda en su primera creacion el duenio del directorio
# de la imagen: sin esto, el proceso no-root no puede escribir la outbox.
RUN mkdir -p /var/outbox /var/auditoria && chown 10001:10001 /var/outbox /var/auditoria
USER 10001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
