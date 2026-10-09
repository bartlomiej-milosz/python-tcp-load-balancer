FROM python:3.13-slim

WORKDIR /app
COPY src/ ./src/
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

CMD ["python", "-m", "app.run_http_server", "8080"]
