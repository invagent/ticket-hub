"""回填旧 KSM 工单的 reporter_company（提单公司）。

从 source_payload._subscribe_callback.customerInfo.customerName 提取提单公司名，
写回 tickets.reporter_company，仅处理 source_code='ksm'、reporter_company 为空、
且 received_at 在最近 N 天内（默认 7 天）的工单。

税号/租户 KSM 不传，不回填（保持空）。

用法（在服务器 backend/ 目录下）：
    .venv/bin/python3.12 ../scripts/backfill_ksm_reporter_company.py [--dry-run] [--days N]
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, ".")
from sqlalchemy import select

from app.db import get_session, init_engine
from app.models import Ticket


def _customer_name(payload: dict) -> str | None:  # type: ignore[type-arg]
    cb = payload.get("_subscribe_callback") or {}
    ci = cb.get("customerInfo") or {}
    name = ci.get("customerName")
    return name if isinstance(name, str) and name.strip() else None


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
                    Ticket.reporter_company.is_(None),
                    Ticket.received_at >= cutoff,
                )
            )
            .scalars()
            .all()
        )

        updated = 0
        for ticket in tickets:
            company = _customer_name(ticket.source_payload or {})
            if not company:
                continue
            print(f"  {ticket.short_code}: reporter_company={company!r}")
            if not dry_run:
                ticket.reporter_company = company
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
    n = 7
    if "--days" in sys.argv:
        n = int(sys.argv[sys.argv.index("--days") + 1])
    main(dry_run=dry, days=n)
