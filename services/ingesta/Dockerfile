FROM python:3.12-slim
WORKDIR /srv
COPY services/ingesta/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/ingesta/app ./app
EXPOSE 8001
CMD ["python", "-m", "app.main"]
