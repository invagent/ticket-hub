"""Celery entry point for the T+N Operation auto-close beat.

Beat fires `close_answered_operations` once a day; it scans Operation
hub_issues stuck in `answered` past `operation_auto_close_days` (no rejection
came in to bounce them back to `processing`) and closes them. Self-skips when
the switch is off — beat keeps ticking either way.
"""

from __future__ import annotations

from celery import shared_task

from app.config import get_settings
from app.core.logging import get_logger
from app.db import make_session
from app.services.hub_issues.op_status import close_overdue_answered

logger = get_logger(__name__)


@shared_task(name="app.services.agents.op_close_task.close_answered_operations")  # type: ignore[untyped-decorator]  # celery decorator is untyped
def close_answered_operations_task() -> dict[str, int]:
    """Own session; swallows everything so beat never dies."""
    settings = get_settings()
    if not settings.operation_auto_close_enabled:
        return {"closed": 0}

    db = make_session()
    try:
        closed = close_overdue_answered(db, settings=settings)
        db.commit()
        return {"closed": closed}
    except Exception:
        db.rollback()
        logger.exception("operation_auto_close_unexpected_failure")
        return {"closed": 0}
    finally:
        db.close()
