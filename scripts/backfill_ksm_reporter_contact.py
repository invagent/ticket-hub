"""回填旧 KSM 工单的 reporter 联系方式（mobile/email/tel）。

从 source_payload._subscribe_callback.customerInfo 提取客户公司联系人的
mobile/email/phone，写回 tickets.reporter JSON（仅补空字段），优先 customerInfo、
回落反馈人顶层 feedbackPhone/feedbackEmail/feedbackTel（与新入库口径一致）。

仅处理 source_code='ksm'、reporter 里 mobile/email/tel 有空、且 received_at 在最近
N 天内（默认 5 天）的工单。

用法（在服务器 backend/ 目录下）：
    .venv/bin/python3.12 ../scripts/backfill_ksm_reporter_contact.py [--dry-run] [--days N]
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, ".")
from sqlalchemy import select

from app.db import get_session, init_engine
from app.models import Ticket


def _contact(payload: dict) -> tuple[str | None, str | None, str | None]:  # type: ignore[type-arg]
    """从 source_payload 提取 (mobile, email, tel)，优先 customerInfo 回落 feedback*。"""
    cb = payload.get("_subscribe_callback") or {}
    ci = cb.get("customerInfo") or {}
    mobile = ci.get("mobile") or cb.get("feedbackPhone")
    email = ci.get("email") or cb.get("feedbackEmail")
    tel = ci.get("phone") or cb.get("feedbackTel")

    def _s(v) -> str | None:  # type: ignore[no-untyped-def]
        return v if isinstance(v, str) and v.strip() else None

    return _s(mobile), _s(email), _s(tel)


def main(*, dry_run: bool, days: int) -> None:
    init_engine()
    db = next(get_session())
    try:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        tickets = (
            db.execute(
                select(Ticket).where(
                    Ticket.source_code == "ksm",
                    Ticket.deleted_at.is_(None),
                    Ticket.source_payload.is_not(None),
                    Ticket.received_at >= cutoff,
                )
            )
            .scalars()
            .all()
        )

        updated = 0
        for ticket in tickets:
            reporter = ticket.reporter or {}
            if not isinstance(reporter, dict):
                reporter = {}
            mobile, email, tel = _contact(ticket.source_payload or {})
            changed = False
            if not reporter.get("mobile") and mobile:
                reporter["mobile"] = mobile
                changed = True
            if not reporter.get("email") and email:
                reporter["email"] = email
                changed = True
            if not reporter.get("tel") and tel:
                reporter["tel"] = tel
                changed = True
            if not changed:
                continue
            print(
                f"  {ticket.short_code}: mobile={mobile!r} email={email!r} tel={tel!r}"
            )
            if not dry_run:
                ticket.reporter = reporter
                db.add(ticket)
            updated += 1

        if not dry_run:
            db.commit()
        print(f"\n{'[dry-run] ' if dry_run else ''}回填 {updated} 条（最近 {days} 天）。")
    finally:
        db.close()


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    n = 5
    if "--days" in sys.argv:
        n = int(sys.argv[sys.argv.index("--days") + 1])
    main(dry_run=dry, days=n)
