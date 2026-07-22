"""Celery entry point for the Operation auto-reply drain.

Beat fires `drain_operation_auto_reply` every 2 min; it scans un-answered
Operation hub_issues and runs ai_cs replay for each. Moved off the ingest hot
path because replay is slow (~138s/单) and would block the worker. Doubles as a
compensating retry for transient replay failures. Self-skips when the switch is
off — beat keeps ticking either way.
"""

from __future__ import annotations

from celery import shared_task

from app.config import get_settings
from app.core.logging import get_logger
from app.db import make_session
from app.services.agents.operation_answer import drain_operation_auto_reply

logger = get_logger(__name__)


@shared_task(name="app.services.agents.operation_answer_task.drain_operation_auto_reply")  # type: ignore[untyped-decorator]  # celery decorator is untyped
def drain_operation_auto_reply_task() -> dict[str, int]:
    """Own session; swallows everything so beat never dies."""
    settings = get_settings()
    if not settings.operation_auto_reply_enabled:
        return {"scanned": 0, "answered": 0, "failed": 0}

    db = make_session()
    try:
        report = drain_operation_auto_reply(db, settings=settings)
        return {
            "scanned": report.scanned,
            "answered": report.answered,
            "failed": report.failed,
        }
    except Exception:
        db.rollback()
        logger.exception("operation_auto_reply_drain_unexpected_failure")
        return {"scanned": 0, "answered": 0, "failed": 0}
    finally:
        db.close()
