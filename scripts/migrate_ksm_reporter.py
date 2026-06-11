"""补填旧 KSM 工单的 reporter.feedback_user 和 reporter.linkman 字段。

从 source_payload 里提取 feedbackUser 和 _subscribe_callback.customerInfo.linkman，
写回 tickets.reporter JSON，仅处理 source_code='ksm' 且 reporter 缺少这两个字段的工单。

用法（在服务器 backend/ 目录下）：
    .venv/bin/python3.12 ../scripts/migrate_ksm_reporter.py [--dry-run]
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session, init_engine
from app.models import Ticket


def extract_reporter_fields(payload: dict) -> dict:
    feedback_user = payload.get("feedbackUser")
    cb = payload.get("_subscribe_callback") or {}
    linkman = cb.get("customerInfo", {}).get("linkman")
    return {"feedback_user": feedback_user, "linkman": linkman}


def main(dry_run: bool) -> None:
    init_engine()
    db = next(get_session())
    try:
        tickets = (
            db.execute(
                select(Ticket).where(
                    Ticket.source_code == "ksm",
                    Ticket.deleted_at.is_(None),
                    Ticket.source_payload.is_not(None),
                )
            )
            .scalars()
            .all()
        )

        updated = 0
        for ticket in tickets:
            reporter = ticket.reporter or {}
            if (
                reporter.get("feedback_user") is not None
                or reporter.get("linkman") is not None
            ):
                continue
            payload = ticket.source_payload or {}
            extra = extract_reporter_fields(payload)
            if not extra["feedback_user"] and not extra["linkman"]:
                continue
            new_reporter = dict(reporter)
            new_reporter.update(extra)
            fu = extra["feedback_user"]
            lm = extra["linkman"]
            print(
                "  "
                + ticket.short_code
                + ": feedback_user="
                + repr(fu)
                + ", linkman="
                + repr(lm)
            )
            if not dry_run:
                ticket.reporter = new_reporter
                db.add(ticket)
            updated += 1

        if not dry_run:
            db.commit()
            print("\n已更新 " + str(updated) + " 条工单。")
        else:
            print("\n[dry-run] 将更新 " + str(updated) + " 条工单，未写入数据库。")
    finally:
        db.close()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    main(dry_run=dry_run)
