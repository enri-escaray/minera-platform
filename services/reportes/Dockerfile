FROM python:3.12-slim
WORKDIR /srv
COPY services/reportes/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY libs/comun ./libs/comun
RUN pip install --no-cache-dir ./libs/comun
COPY services/reportes/app ./app
EXPOSE 8004
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004"]
