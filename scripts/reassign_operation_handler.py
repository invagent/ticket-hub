"""一次性历史数据修正：把某处理人 9/1-9/2 期间还没答复完成/关闭的运营工单，
批量转交给另一处理人。

同步改 hub.op_handler_user_id + hub.op_handler（字符串）+ 该 hub 下所有关联
ticket.handler_user_id（三处保持一致，同 set_hub_tickets_handler 的口径），并
给 hub 和每条 ticket 都写一条 status_history 审计（不改 op_status/ticket.status
本身，只记录处理人变更）。

用法（在服务器 backend/ 目录下）：
    .venv/bin/python3.12 ../scripts/reassign_operation_handler.py \
        --from-user-id 3 --to-user-id 39 \
        --start 2026-09-01 --end 2026-09-03 [--dry-run]
"""

from __future__ import annotations

import argparse
from datetime import datetime

from sqlalchemy import select

from app.db import get_session, init_engine
from app.models import HubIssue, Ticket, User
from app.repositories.status_history import StatusHistoryRepository

_OPEN_OP_STATUSES = ("processing", "reviewing", "supplementing", "exception")


def main(*, from_user_id: int, to_user_id: int, start: datetime, end: datetime, dry_run: bool) -> None:
    init_engine()
    db = next(get_session())
    try:
        from_user = db.get(User, from_user_id)
        to_user = db.get(User, to_user_id)
        if from_user is None or to_user is None:
            raise SystemExit(f"用户不存在：from={from_user_id} to={to_user_id}")
        from_name, to_name = from_user.name, to_user.name

        hub_ids = (
            db.execute(
                select(Ticket.hub_issue_id)
                .join(HubIssue, HubIssue.id == Ticket.hub_issue_id)
                .where(
                    HubIssue.type == "Operation",
                    HubIssue.op_handler_user_id == from_user_id,
                    HubIssue.op_status.in_(_OPEN_OP_STATUSES),
                    HubIssue.deleted_at.is_(None),
                    Ticket.deleted_at.is_(None),
                    Ticket.received_at >= start,
                    Ticket.received_at < end,
                )
                .distinct()
            )
            .scalars()
            .all()
        )
        hubs = [db.get(HubIssue, hid) for hid in hub_ids]

        print(f"匹配到 {len(hubs)} 个 hub（{from_name} -> {to_name}，{start.date()}~{end.date()}）：")
        history = StatusHistoryRepository(db)
        reason = f"历史数据批量转交处理人：{from_name} → {to_name}（{start.date()}~{end.date()} 未完成运营工单）"
        for hub in hubs:
            tickets = (
                db.query(Ticket)
                .filter(Ticket.hub_issue_id == hub.id, Ticket.deleted_at.is_(None))
                .all()
            )
            codes = ", ".join(t.short_code for t in tickets)
            print(f"  {hub.short_code} (op_status={hub.op_status}) tickets=[{codes}]")
            if dry_run:
                continue

            history.record(
                entity_type="hub_issue",
                entity_id=hub.id,
                from_status=hub.op_status,
                to_status=hub.op_status,
                changed_by="system:reassign_handler",
                reason=reason,
                metadata={"op_handler_user_id": to_user_id, "prev_op_handler_user_id": from_user_id},
            )
            hub.op_handler_user_id = to_user_id
            hub.op_handler = to_name
            for t in tickets:
                history.record(
                    entity_type="ticket",
                    entity_id=t.id,
                    from_status=t.status,
                    to_status=t.status,
                    changed_by="system:reassign_handler",
                    reason=reason,
                    metadata={"handler_user_id": to_user_id, "prev_handler_user_id": t.handler_user_id},
                )
                t.handler_user_id = to_user_id
            db.commit()

        print(f"\n{'[dry-run] ' if dry_run else ''}共处理 {len(hubs)} 个 hub。")
    finally:
        db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--from-user-id", type=int, required=True)
    p.add_argument("--to-user-id", type=int, required=True)
    p.add_argument("--start", type=str, required=True, help="YYYY-MM-DD，含")
    p.add_argument("--end", type=str, required=True, help="YYYY-MM-DD，不含")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    main(
        from_user_id=args.from_user_id,
        to_user_id=args.to_user_id,
        start=datetime.strptime(args.start, "%Y-%m-%d"),
        end=datetime.strptime(args.end, "%Y-%m-%d"),
        dry_run=args.dry_run,
    )
