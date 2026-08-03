"""把历史导入的 Bug_fix/Demand 工单批量毕业成 hub_issue（不推 Linear）。

背景：飞书 Q2 工单直接建库(tickets)未毕业 hub_issue，研发协同页(/hub-issues)
只显示 hub_issue 故看不到。本脚本对研发两类(Bug_fix/Demand)的 Raw 工单调用
ensure_hub_issue_for_ticket 毕业——该函数本身不推 Linear(推送是调用方另外触发)。

- created_by="user:backfill-graduate"：user: 前缀跳过 hub_dedup，453 条不会被相似度误合并，
  也不依赖 embedding/LLM 网络。
- 幂等：已毕业(hub_issue_id 非空)的 ticket 再调用直接返回 created=False，可重跑。
- 不推 Linear：不调用 push_hub_issue_to_linear。

用法（backend/ 目录）：
    python3 ../scripts/graduate_dev_tickets.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")
from sqlalchemy import select

from app.db import get_session, init_engine
from app.models import Ticket
from app.services.hub_issues.creator import HubIssueCreateError, ensure_hub_issue_for_ticket

_DEV_TYPES = ("Bug_fix", "Demand")
_CREATED_BY = "user:backfill-graduate"


def main(dry_run: bool) -> None:
    init_engine()
    db = next(get_session())
    try:
        ticket_ids = list(
            db.execute(
                select(Ticket.id).where(
                    Ticket.predicted_type.in_(_DEV_TYPES),
                    Ticket.type == "Raw",
                    Ticket.hub_issue_id.is_(None),
                    Ticket.deleted_at.is_(None),
                )
            )
            .scalars()
            .all()
        )
        print(f"待毕业 Bug_fix/Demand 工单: {len(ticket_ids)}")
        if dry_run:
            print("[dry-run] 不写库")
            return

        created = skipped = failed = 0
        for i, tid in enumerate(ticket_ids, 1):
            try:
                r = ensure_hub_issue_for_ticket(tid, created_by=_CREATED_BY, db=db)
                if r.created:
                    created += 1
                else:
                    skipped += 1
            except HubIssueCreateError as e:
                failed += 1
                print(f"  ticket {tid} 毕业失败: {e}")
            if i % 100 == 0:
                db.commit()
                print(f"  已处理 {i}/{len(ticket_ids)}...")
        db.commit()
        print(f"毕业完成：created={created} skipped={skipped} failed={failed}")
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    main(ap.parse_args().dry_run)
