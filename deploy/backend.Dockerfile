# ticket-hub backend image (SIT/prod docker deploy).
# Build context = repo root (see deploy/docker-compose.sit.yml).
# Deps install in a cached layer keyed on pyproject; source copied after.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_INPUT=1 \
    PIP_PROGRESS_BAR=off \
    PYTHONPATH=/app \
    # 国内源 + 超时/重试：官方 pypi.org 从国内拉大 wheel 反复 stall，
    # 冷构建（缓存未命中重装依赖）会卡死。aliyun 镜像稳定可达。
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.aliyun.com \
    PIP_DEFAULT_TIMEOUT=60 \
    PIP_RETRIES=5

WORKDIR /app

# Dependency layer — only busts when pyproject changes.
COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir .

# Application source (baked in — no volume mount; git pull + rebuild to update).
COPY backend/ ./

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--loop", "asyncio"]
