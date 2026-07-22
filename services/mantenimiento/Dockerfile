FROM python:3.12-slim
WORKDIR /srv
COPY services/mantenimiento/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY libs/comun ./libs/comun
RUN pip install --no-cache-dir ./libs/comun
COPY services/mantenimiento/app ./app
EXPOSE 8002
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
