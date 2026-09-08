"""Backfill script: 补齐历史因 is_mixed 未毕业工单的 Hub 任务与子任务。

用法：
    python scripts/backfill_mixed_tickets.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import make_session
from app.models import AgentDecision, HubIssue, Ticket
from app.services.hub_issues.creator import _next_hub_short_code, ensure_hub_issue_for_ticket


def backfill_mixed_tickets(db: Session, *, dry_run: bool = False) -> int:
    # 查找所有类型为 Raw、未软删、且 hub_issue_id 为空的有效工单（排除投诉 Complaint）
    tickets = (
        db.execute(
            select(Ticket)
            .where(
                Ticket.type == "Raw",
                Ticket.deleted_at.is_(None),
                Ticket.hub_issue_id.is_(None),
                Ticket.predicted_type != "Complaint",
            )
            .order_by(Ticket.id.asc())
        )
        .scalars()
        .all()
    )

    print(f"找到 {len(tickets)} 条未毕业工单。")
    success_count = 0

    for t in tickets:
        print(f"处理工单 #{t.id} ({t.short_code}): title={t.title!r}")
        if dry_run:
            continue

        try:
            # 1. 毕业主 Hub 任务
            res = ensure_hub_issue_for_ticket(t.id, created_by="system:backfill_mixed", db=db)
            print(f"  -> 毕业主 Hub: {res.hub_issue_short_code} (id={res.hub_issue_id})")

            # 2. 查找是否有 split_ticket 决策记录并自动创建子任务
            dec = (
                db.execute(
                    select(AgentDecision)
                    .where(
                        AgentDecision.subject_type == "ticket",
                        AgentDecision.subject_id == t.id,
                        AgentDecision.decision_type == "split_ticket",
                        AgentDecision.reverted_at.is_(None),
                    )
                    .order_by(AgentDecision.id.desc())
                )
                .scalars()
                .first()
            )

            if dec and dec.proposal and isinstance(dec.proposal.get("sub_issues"), list):
                sub_issues = dec.proposal["sub_issues"]
                assignee_id = t.handler_user_id or t.assigned_user_id
                for sp in sub_issues:
                    st_type = sp.get("type") or "Operation"
                    if st_type not in ("Operation", "Bug_fix", "Demand", "Internal_task"):
                        st_type = "Operation"
                    st_title = str(sp.get("title") or "").strip() or "子任务"
                    st_summary = str(sp.get("summary") or "").strip() or t.body

                    st = HubIssue(
                        short_code=_next_hub_short_code(db),
                        ticket_id=t.id,
                        type=st_type,
                        title=st_title,
                        canonical_body=st_summary,
                        product_line_code=t.product_line_code,
                        module=t.module,
                        status="draft",
                        assigned_user_id=assignee_id,
                        occurrence_count=1,
                    )
                    db.add(st)
                db.commit()
                print(f"  -> 自动创建 {len(sub_issues)} 个子任务")

            success_count += 1
        except Exception as e:
            db.rollback()
            print(f"  -> 毕业/拆单失败: {e}", file=sys.stderr)

    return success_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill ungraduated mixed tickets")
    parser.add_argument("--dry-run", action="store_true", help="只打印不修改数据库")
    args = parser.parse_args()

    db = make_session()
    try:
        count = backfill_mixed_tickets(db, dry_run=args.dry_run)
        print(f"完成！共补齐 {count} 条工单。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
