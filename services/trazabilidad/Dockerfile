FROM python:3.12-slim
WORKDIR /srv
COPY services/trazabilidad/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY libs/comun ./libs/comun
RUN pip install --no-cache-dir ./libs/comun
COPY services/trazabilidad/app ./app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
