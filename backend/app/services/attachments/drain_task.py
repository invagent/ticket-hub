"""Celery beat：定期 drain queued 附件（KSM 附件下载 → MinIO → vision OCR）。

key/enabled 未配自动跳过（drain 内部已 guard）。镜像
app/services/ksm/writeback_task.py 的结构：own session, commit, close in finally。
"""

from __future__ import annotations

from celery import shared_task

from app.core.logging import get_logger
from app.db import make_session
from app.services.attachments.pipeline import drain_pending_attachments

logger = get_logger(__name__)


@shared_task(name="app.services.attachments.drain_task.drain_attachments")  # type: ignore[untyped-decorator]  # celery decorator is untyped
def drain_attachments() -> dict[str, int]:
    """Own session; swallows everything so beat never dies."""
    db = make_session()
    try:
        report = drain_pending_attachments(db)
        db.commit()
        logger.info(
            "drain_attachments_done",
            scanned=report.scanned,
            extracted=report.extracted,
            skipped=report.skipped,
            failed=report.failed,
        )
        return {
            "scanned": report.scanned,
            "extracted": report.extracted,
            "skipped": report.skipped,
            "failed": report.failed,
        }
    except Exception:
        db.rollback()
        logger.exception("drain_attachments_unexpected_failure")
        return {"scanned": 0, "extracted": 0, "skipped": 0, "failed": 0}
    finally:
        db.close()
