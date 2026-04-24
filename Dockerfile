# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

# System deps: pymupdf benötigt keine weiteren libs bei Python 3.12-slim,
# aber build-essential hilft falls Wheel nicht verfügbar ist.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Dependencies-Cache — pyproject.toml zuerst, damit Layer cached wird
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir -e .

EXPOSE 8000
# Railway liefert $PORT, lokal per docker-compose ist 8000 default
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
