# ticket-hub backend image (SIT/prod docker deploy).
# Build context = repo root (see deploy/docker-compose.sit.yml).
# Deps install in a cached layer keyed on pyproject; source copied after.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_INPUT=1 \
    PIP_PROGRESS_BAR=off \
    PYTHONPATH=/app

WORKDIR /app

# Dependency layer — only busts when pyproject changes.
COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir .

# Application source (baked in — no volume mount; git pull + rebuild to update).
COPY backend/ ./

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--loop", "asyncio"]
