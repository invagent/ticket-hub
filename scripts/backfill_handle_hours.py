"""回填历史飞书导入工单的 handle_hours / sla_standard_hours / actual_resolved_at。

从 source_payload._feishu_import 读「工单耗用时间」「处理时长标准」，写入原生列；
handle_hours 有值且 actual_resolved_at 空 → actual_resolved_at = received_at + handle_hours。
幂等（handle_hours 已填则跳过）。

用法（backend/ 目录）：
    python3 ../scripts/backfill_handle_hours.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from decimal import Decimal, InvalidOperation

sys.path.insert(0, ".")
from sqlalchemy import select

from app.db import get_session, init_engine
from app.models import Ticket


def _num(v) -> Decimal | None:
    s = str(v or "").strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def main(dry_run: bool) -> None:
    init_engine()
    db = next(get_session())
    try:
        tickets = (
            db.execute(select(Ticket).where(Ticket.source_payload.is_not(None)))
            .scalars()
            .all()
        )
        filled = skipped = 0
        for t in tickets:
            fi = (t.source_payload or {}).get("_feishu_import")
            if not fi or t.handle_hours is not None:
                skipped += 1
                continue
            hh = _num(fi.get("工单耗用时间"))
            std = _num(fi.get("处理时长标准"))
            if hh is None and std is None:
                skipped += 1
                continue
            if not dry_run:
                t.handle_hours = hh
                t.sla_standard_hours = std
                if hh is not None and t.actual_resolved_at is None and t.received_at is not None:
                    t.actual_resolved_at = t.received_at + timedelta(hours=float(hh))
            filled += 1
        if not dry_run:
            db.commit()
        print(f"{'[dry-run] ' if dry_run else ''}filled={filled} skipped={skipped}")
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    main(ap.parse_args().dry_run)
