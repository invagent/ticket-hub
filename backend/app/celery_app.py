"""Celery application + beat schedule (D2-B).

Single worker process drains both periodic tasks (beat) and ad-hoc enqueues.
Broker + result backend both reuse the existing Redis URL from settings.

Run modes:
    # Worker (consumes tasks)
    celery -A app.celery_app worker --loglevel=INFO

    # Beat (emits scheduled tasks; only one of these per cluster)
    celery -A app.celery_app beat --loglevel=INFO

In production we run worker and beat as separate systemd units to keep
restart semantics independent.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "ticket_hub",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.services.metrics.materializer"],  # eagerly import task modules
)

# Conservative defaults — D6 will revisit (concurrency, retry, ack policies)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# Beat schedule: keep tasks here so a single source of truth.
celery_app.conf.beat_schedule = {
    "refresh_dashboard_metrics_every_5min": {
        "task": "app.services.metrics.materializer.refresh_dashboard_metrics",
        "schedule": crontab(minute="*/5"),
    },
}
