"""一次性历史数据修正：把某处理人在指定时间段内还未关闭的工单（不限类型），
批量转交给另一处理人。

与 reassign_operation_handler.py 的区别：那个脚本只覆盖 Operation 类型且
op_status 在 processing/reviewing/supplementing/exception 的窄口径；这个脚本
不区分 hub 类型，只要 ticket.status 不在终态（done/closed/rejected/
superseded）即算「未关闭」，覆盖未毕业、pending_review 待确认分类等所有场景。

匹配口径：Ticket.handler_user_id == from_user_id（工单列表页「处理人」列
读的正是这个字段，是唯一权威）。同步改 HubIssue.op_handler_user_id（若该
hub 当前 op_handler_user_id 也等于 from_user_id，一并转，保持两处一致；不
强制覆盖已经指向别人的 op_handler_user_id，避免误改已被上级流程重新分派的单）。
每条 ticket 写一条 status_history 审计（不改 ticket.status/op_status 本身，
只记录处理人变更），涉及 hub 层字段变更时 hub 也写一条。

用法（在服务器 backend/ 目录下）：
    .venv/bin/python3.12 ../scripts/reassign_handler_by_date.py \
        --from-user-id 1 --to-user-id 42 \
        --start 2026-08-15 --end 2026-09-01 [--dry-run]
"""

from __future__ import annotations

import argparse
from datetime import datetime

from sqlalchemy import select

from app.db import get_session, init_engine
from app.models import HubIssue, Ticket, User
from app.repositories.status_history import StatusHistoryRepository

_TICKET_TERMINAL_STATUSES = frozenset({"done", "closed", "rejected", "superseded"})


def main(*, from_user_id: int, to_user_id: int, start: datetime, end: datetime, dry_run: bool) -> None:
    init_engine()
    db = next(get_session())
    try:
        from_user = db.get(User, from_user_id)
        to_user = db.get(User, to_user_id)
        if from_user is None or to_user is None:
            raise SystemExit(f"用户不存在：from={from_user_id} to={to_user_id}")
        from_name, to_name = from_user.name, to_user.name

        ticket_ids = (
            db.execute(
                select(Ticket.id)
                .where(
                    Ticket.handler_user_id == from_user_id,
                    Ticket.deleted_at.is_(None),
                    Ticket.status.notin_(list(_TICKET_TERMINAL_STATUSES)),
                    Ticket.received_at >= start,
                    Ticket.received_at < end,
                )
            )
            .scalars()
            .all()
        )
        tickets = [db.get(Ticket, tid) for tid in ticket_ids]

        print(
            f"匹配到 {len(tickets)} 条 ticket（{from_name} -> {to_name}，"
            f"{start.date()}~{end.date()}，未关闭不限类型）："
        )
        history = StatusHistoryRepository(db)
        reason = f"历史数据批量转交处理人：{from_name} → {to_name}（{start.date()}~{end.date()} 未关闭工单）"
        hub_touched: set[int] = set()

        for ticket in tickets:
            hub = db.get(HubIssue, ticket.hub_issue_id) if ticket.hub_issue_id else None
            print(
                f"  {ticket.short_code} (status={ticket.status}) "
                f"hub={hub.short_code if hub else None} type={hub.type if hub else None}"
            )
            if dry_run:
                continue

            history.record(
                entity_type="ticket",
                entity_id=ticket.id,
                from_status=ticket.status,
                to_status=ticket.status,
                changed_by="system:reassign_handler_by_date",
                reason=reason,
                metadata={"handler_user_id": to_user_id, "prev_handler_user_id": from_user_id},
            )
            ticket.handler_user_id = to_user_id

            if (
                hub is not None
                and hub.op_handler_user_id == from_user_id
                and hub.id not in hub_touched
            ):
                hub_touched.add(hub.id)
                history.record(
                    entity_type="hub_issue",
                    entity_id=hub.id,
                    from_status=hub.op_status,
                    to_status=hub.op_status,
                    changed_by="system:reassign_handler_by_date",
                    reason=reason,
                    metadata={"op_handler_user_id": to_user_id, "prev_op_handler_user_id": from_user_id},
                )
                hub.op_handler_user_id = to_user_id
                hub.op_handler = to_name

            db.commit()

        print(f"\n{'[dry-run] ' if dry_run else ''}共处理 {len(tickets)} 条 ticket，{len(hub_touched)} 个 hub。")
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
