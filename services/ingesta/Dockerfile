FROM python:3.12-slim
# Usuario sin privilegios: si alguien logra ejecucion dentro del contenedor,
# root dentro del contenedor es el primer escalon hacia el nodo (montajes,
# capabilities, sockets). El proceso no necesita root para escuchar en >1024.
RUN useradd --system --uid 10001 --no-create-home minera
WORKDIR /srv
COPY services/ingesta/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/ingesta/app ./app
EXPOSE 8001
# Un volumen nombrado hereda en su primera creacion el duenio del directorio
# de la imagen: sin esto, el proceso no-root no puede escribir la outbox.
RUN mkdir -p /var/cola /var/outbox && chown 10001:10001 /var/cola /var/outbox
USER 10001
CMD ["python", "-m", "app.main"]
