"""Celery entry: 每 2min drain 智齿 outbox。自跳过当开关关/凭证缺（beat 照常跳）。"""

from __future__ import annotations

from celery import shared_task

from app.config import get_settings
from app.core.logging import get_logger
from app.db import make_session
from app.services.zhichi.writeback import drain_zhichi_outbox

logger = get_logger(__name__)


@shared_task(name="app.services.zhichi.writeback_task.drain_zhichi_writeback")  # type: ignore[untyped-decorator]  # celery decorator is untyped
def drain_zhichi_writeback() -> dict[str, int]:
    """Own session; swallows everything so beat never dies."""
    settings = get_settings()
    if not settings.zhichi_writeback_enabled:
        return {"scanned": 0, "sent": 0, "skipped": 0, "failed": 0, "deferred": 0}

    db = make_session()
    try:
        report = drain_zhichi_outbox(db, settings=settings)
        return {
            "scanned": report.scanned,
            "sent": report.sent,
            "skipped": report.skipped,
            "failed": report.failed,
            "deferred": report.deferred,
        }
    except Exception:
        db.rollback()
        logger.exception("zhichi_writeback_unexpected_failure")
        return {"scanned": 0, "sent": 0, "skipped": 0, "failed": 0, "deferred": 0}
    finally:
        db.close()
