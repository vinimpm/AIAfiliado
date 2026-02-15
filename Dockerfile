FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PORT=8080

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ /app/src/
COPY alembic/ /app/alembic/
COPY alembic.ini /app/alembic.ini
COPY entrypoint.sh /app/entrypoint.sh
COPY start-dashboard.sh /app/start-dashboard.sh

RUN chmod +x /app/entrypoint.sh /app/start-dashboard.sh

RUN useradd --create-home --shell /bin/bash appuser
USER appuser

# Set working directory to src so Python finds modules directly
WORKDIR /app/src

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["celery", "-A", "app.celery_app", "worker", "--loglevel=info", "--concurrency=2"]
