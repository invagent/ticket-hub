"""回填旧 KSM 工单的 source_ticket_number（来源工单编号）。

从 source_payload._subscribe_callback.billNumber 提取编号，写回
tickets.source_ticket_number，仅处理 source_code='ksm'、source_ticket_number 为空、
且 received_at 在最近 N 天内（默认 90 天）的工单。

老 KSM 工单（当时未推 billNumber）该字段恒空，不会误填。

用法（在服务器 backend/ 目录下）：
    .venv/bin/python3.12 ../scripts/backfill_ksm_source_ticket_number.py [--dry-run] [--days N]
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, ".")
from sqlalchemy import select

from app.db import get_session, init_engine
from app.models import Ticket


def _bill_number(payload: dict) -> str | None:  # type: ignore[type-arg]
    cb = payload.get("_subscribe_callback") or {}
    num = cb.get("billNumber")
    return num if isinstance(num, str) and num.strip() else None


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
                    Ticket.source_ticket_number.is_(None),
                    Ticket.received_at >= cutoff,
                )
            )
            .scalars()
            .all()
        )

        updated = 0
        for ticket in tickets:
            num = _bill_number(ticket.source_payload or {})
            if not num:
                continue
            print(f"  {ticket.short_code}: source_ticket_number={num!r}")
            if not dry_run:
                ticket.source_ticket_number = num
                db.add(ticket)
            updated += 1

        if not dry_run:
            db.commit()
            print(f"\n已更新 {updated} 条工单（最近 {days} 天）。")
        else:
            print(f"\n[dry-run] 将更新 {updated} 条工单（最近 {days} 天），未写入数据库。")
    finally:
        db.close()


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    n = 90
    if "--days" in sys.argv:
        n = int(sys.argv[sys.argv.index("--days") + 1])
    main(dry_run=dry, days=n)
