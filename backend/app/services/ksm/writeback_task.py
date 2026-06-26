"""Celery entry point for the KSM writeback drain (D4 第②段).

Beat fires `drain_ksm_writeback` every 2 min; it builds a Redis NoticeStore
(for the post-lock detail re-pull) and a KSM client from settings, then runs
one drain pass. Self-skips when the writeback switch is off or KSM creds are
unset — beat keeps ticking either way.
"""

from __future__ import annotations

from celery import shared_task

from app.config import get_settings
from app.core.logging import get_logger
from app.db import make_session
from app.services.ksm.notice_store import NoticeStore
from app.services.ksm.writeback import drain_ksm_outbox

logger = get_logger(__name__)


@shared_task(name="app.services.ksm.writeback_task.drain_ksm_writeback")  # type: ignore[untyped-decorator]  # celery decorator is untyped
def drain_ksm_writeback() -> dict[str, int]:
    """Own session; swallows everything so beat never dies."""
    settings = get_settings()
    if not settings.ksm_writeback_enabled:
        return {"scanned": 0, "sent": 0, "skipped": 0, "failed": 0, "deferred": 0}

    db = make_session()
    try:
        notice_store: NoticeStore | None = None
        try:
            notice_store = NoticeStore(redis_url=settings.redis_url)
        except Exception:
            logger.warning("ksm_writeback_no_notice_store")
        report = drain_ksm_outbox(db, notice_store=notice_store, settings=settings)
        return {
            "scanned": report.scanned,
            "sent": report.sent,
            "skipped": report.skipped,
            "failed": report.failed,
            "deferred": report.deferred,
        }
    except Exception:
        db.rollback()
        logger.exception("ksm_writeback_unexpected_failure")
        return {"scanned": 0, "sent": 0, "skipped": 0, "failed": 0, "deferred": 0}
    finally:
        db.close()
