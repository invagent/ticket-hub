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
    # eagerly import task modules
    include=[
        "app.services.metrics.materializer",
        "app.services.hub_issues.linear_status_sync",
        "app.services.ksm.writeback_task",
    ],
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
    # D4 第①段: Linear 状态回同步（poll；linear_api_key 未配则任务内自动跳过）
    "poll_linear_statuses_every_5min": {
        "task": "app.services.hub_issues.linear_status_sync.poll_linear_statuses",
        "schedule": crontab(minute="*/5"),
    },
    # D4 第②段: KSM 出站回写 drain（ksm_writeback_enabled 未开则任务内自动跳过）
    "drain_ksm_writeback_every_2min": {
        "task": "app.services.ksm.writeback_task.drain_ksm_writeback",
        "schedule": crontab(minute="*/2"),
    },
    # 智齿出站回写 drain（zhichi_writeback_enabled 未开则任务内自动跳过）
    "drain_zhichi_writeback_every_2min": {
        "task": "app.services.zhichi.writeback_task.drain_zhichi_writeback",
        "schedule": crontab(minute="*/2"),
    },
}
