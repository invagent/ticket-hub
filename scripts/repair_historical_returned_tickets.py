"""一次性历史数据校正：将因历史退回 KSM 成功而留存为 closed 的工单及关联 Hub 校正为 transferred_return / returned。

背景：
在 9 月 8 日上线「双层单一事实源架构与对客 7 态规范」之前，KSM 退回成功（outbox kind='return', status='sent'）
的代码直接将工单与 Hub 置为了 closed。现将这批工单校正为标准的 transferred_return（转单退回），
关联 Hub 状态校正为 returned（已退回），补齐审计日志。

用法（在服务器 backend/ 目录下）：
    .venv/bin/python ../scripts/repair_historical_returned_tickets.py [--dry-run]
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import get_session, init_engine
from app.models import HubIssue, StatusHistory, SyncOutbox, Ticket


def main(*, dry_run: bool) -> None:
    init_engine()
    db = next(get_session())
    try:
        now = datetime.now(UTC)

        # 1. 查找退回成功（outbox kind='return', status='sent'）对应的 ticket_id
        # 使用 select(SyncOutbox.ticket_id).distinct() 避免在含 JSON 列的 tickets 表上做 DISTINCT
        stmt = (
            select(SyncOutbox.ticket_id)
            .where(
                SyncOutbox.kind == "return",
                SyncOutbox.status == "sent",
                SyncOutbox.ticket_id.isnot(None),
            )
            .distinct()
        )
        returned_ticket_ids = [r[0] for r in db.execute(stmt).all()]
        print(f"找到历史成功退回的工单 ID 数量: {len(returned_ticket_ids)}")

        # 2. 筛出当前状态仍为 closed 的工单
        tickets = (
            db.query(Ticket)
            .filter(
                Ticket.id.in_(returned_ticket_ids),
                Ticket.status == "closed",
                Ticket.deleted_at.is_(None),
            )
            .all()
        )
        print(f"需校正的工单数量 (status='closed'): {len(tickets)}")

        ticket_count = 0
        hub_count = 0

        for t in tickets:
            prev_status = t.status
            if not dry_run:
                t.status = "transferred_return"
                db.add(
                    StatusHistory(
                        entity_type="ticket",
                        entity_id=t.id,
                        from_status=prev_status,
                        to_status="transferred_return",
                        changed_by="system:data_repair_transferred_return",
                        reason="历史退回工单状态校正：由 closed 校正为 transferred_return（对齐新状态机规范）",
                        changed_at=now,
                    )
                )
            ticket_count += 1

            # 关联 Hub 校正
            hub_ids = set()
            if t.hub_issue_id:
                hub_ids.add(t.hub_issue_id)
            sub_hubs = db.query(HubIssue.id).filter(HubIssue.ticket_id == t.id).all()
            for s in sub_hubs:
                hub_ids.add(s[0])

            for hid in hub_ids:
                h = db.get(HubIssue, hid)
                if h and h.deleted_at is None:
                    h_changed = False
                    prev_h_status = h.status
                    if h.status != "returned":
                        if not dry_run:
                            h.status = "returned"
                        h_changed = True
                    if h.type == "Operation" and h.op_status != "transferred_return":
                        if not dry_run:
                            h.op_status = "transferred_return"
                        h_changed = True

                    if h_changed:
                        hub_count += 1
                        if not dry_run:
                            db.add(
                                StatusHistory(
                                    entity_type="hub_issue",
                                    entity_id=h.id,
                                    from_status=prev_h_status,
                                    to_status="returned",
                                    changed_by="system:data_repair_transferred_return",
                                    reason="历史退回工单任务状态校正为 returned（对齐新状态机规范）",
                                    changed_at=now,
                                )
                            )

        if dry_run:
            print(f"[DRY-RUN] 预览校正工单数: {ticket_count}, 关联 Hub 任务数: {hub_count}")
        else:
            db.commit()
            print(f"成功完成校正！共校正工单数: {ticket_count}, 关联 Hub 任务数: {hub_count}")

    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="校正历史退回工单状态为 transferred_return")
    parser.add_argument("--dry-run", action="store_true", help="只演练不写库")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
