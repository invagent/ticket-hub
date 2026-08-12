"""Celery entry point for SLA monitoring.

Beat fires `run_sla_monitor` on an interval; it runs two phases in order,
sharing one session:

  1. SLAWatcher.scan()      — detect overdue tickets/hub_issues, write one
                              sla_overdue notification per overdue entity.
  2. EscalationWorker.escalate_pending() — re-target notifications that have
                              sat unacknowledged past the escalation window
                              to the recipient's deputy/supervisor.

Self-skips when `sla_watcher_enabled` is off — beat keeps ticking either way.
This is deliberately gated behind a default-off switch: the watcher/worker
code shipped long ago but was never scheduled, so first enablement will emit
a burst of notifications for the current overdue backlog. Enable off-peak.
"""

from __future__ import annotations

from celery import shared_task

from app.config import get_settings
from app.core.logging import get_logger
from app.db import make_session
from app.services.sla.escalation import EscalationWorker
from app.services.sla.watcher import SLAWatcher

logger = get_logger(__name__)


@shared_task(name="app.services.sla.sla_task.run_sla_monitor")  # type: ignore[untyped-decorator]  # celery decorator is untyped
def run_sla_monitor() -> dict[str, int]:
    """Own session; swallows everything so beat never dies."""
    settings = get_settings()
    if not settings.sla_watcher_enabled:
        return {"notifications_written": 0, "escalated": 0, "skipped": 0}

    fallback = settings.sla_fallback_recipient_id or settings.default_pool_user_id
    db = make_session()
    try:
        scan = SLAWatcher(db, fallback_recipient_id=fallback).scan()
        db.commit()
        escalation = EscalationWorker(db).escalate_pending()
        db.commit()
        result = {
            "notifications_written": scan.notifications_written,
            "skipped": scan.skipped_unassigned,
            "escalated": len(escalation.escalated),
        }
        logger.info("sla_monitor_done", **result)
        return result
    except Exception:
        db.rollback()
        logger.exception("sla_monitor_unexpected_failure")
        return {"notifications_written": 0, "escalated": 0, "skipped": 0}
    finally:
        db.close()
