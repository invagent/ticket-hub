"""每日看板（2026-09）— 按天 + 按处理人的运营视角统计。

与 analytics.py（月度/全量研发管理视角）不同，这里按单个北京自然日聚合：
接收/完成/退回KSM/KSM打回/补充资料五类事件，各自按 `tickets.handler_user_id`
（当前实际持有人）拆到处理人维度。实时查询，非物化。

口径说明：
- 接收：当日 `ticket.received_at` 落在该日的工单数。
- 完成：hub_issue 当日进入终态——研发类 `status_history(to_status='released')`，
  或 Operation 类 `status_history(to_status='closed', changed_by LIKE 'op:%')`；
  一个 hub 可能挂多条来源 ticket，每条各计一次（跟"接收"同一统计单位：工单数）。
- 退回KSM：`sync_outbox(kind='return', status='sent')` 当日 `sent_at`（KSM 确认
  送达成功才计，不计仅入队未送达的）。
- KSM打回（客户驳回）/ 补充资料：ticket/hub_issue 上无专门字段，均从
  `status_history.reason` 文本匹配识别（见 ksm_ingester.py 对应分支写入的
  固定文案），不新增埋点字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from datetime import date as date_cls
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import StatusHistory, SyncOutbox, Ticket, User
from app.services.metrics.workbench import _IN_PROGRESS_STATUSES, _RESOLVED_STATUSES

BEIJING_OFFSET_HOURS = 8
_UNASSIGNED_NAME = "(未分配)"


@dataclass(slots=True, frozen=True)
class DailyTotals:
    received: int
    completed: int
    returned_to_ksm: int
    ksm_rejected: int
    supplemented: int


@dataclass(slots=True, frozen=True)
class DailyByAssignee:
    user_id: int | None
    name: str
    received: int = 0
    completed: int = 0
    returned_to_ksm: int = 0
    ksm_rejected: int = 0
    supplemented: int = 0


@dataclass(slots=True, frozen=True)
class LifetimeTotals:
    total: int
    in_progress: int
    completed: int
    returned_to_ksm_total: int


@dataclass(slots=True)
class DailyDashboard:
    date: str
    totals: DailyTotals
    lifetime: LifetimeTotals
    by_assignee: list[DailyByAssignee] = field(default_factory=list)


def _day_bounds(date_str: str) -> tuple[datetime, datetime]:
    """北京自然日 YYYY-MM-DD → [start, end) UTC 边界。"""
    d = date_cls.fromisoformat(date_str)
    start_local = datetime(d.year, d.month, d.day)
    start = start_local - timedelta(hours=BEIJING_OFFSET_HOURS)
    end = start + timedelta(days=1)
    return start.replace(tzinfo=UTC), end.replace(tzinfo=UTC)


def _mutable_bucket() -> dict[int | None, dict[str, Any]]:
    return {}


def _bucket_get(
    buckets: dict[int | None, dict[str, Any]], user_id: int | None, name: str
) -> dict[str, Any]:
    if user_id not in buckets:
        buckets[user_id] = {
            "user_id": user_id,
            "name": name,
            "received": 0,
            "completed": 0,
            "returned_to_ksm": 0,
            "ksm_rejected": 0,
            "supplemented": 0,
        }
    return buckets[user_id]


def _received_counts(
    db: Session, start: datetime, end: datetime
) -> tuple[int, dict[int | None, int]]:
    rows = db.execute(
        select(Ticket.handler_user_id, func.count(Ticket.id))
        .where(
            Ticket.deleted_at.is_(None),
            Ticket.received_at >= start,
            Ticket.received_at < end,
        )
        .group_by(Ticket.handler_user_id)
    ).all()
    by_handler: dict[int | None, int] = {}
    for uid, cnt in rows:
        by_handler[uid] = cnt
    total = sum(by_handler.values())
    return total, by_handler


def _tickets_for_hub_ids(db: Session, hub_ids: set[int]) -> list[Ticket]:
    if not hub_ids:
        return []
    return list(
        db.scalars(
            select(Ticket).where(Ticket.deleted_at.is_(None), Ticket.hub_issue_id.in_(hub_ids))
        )
    )


def _completed_counts(
    db: Session, start: datetime, end: datetime
) -> tuple[int, dict[int | None, int]]:
    released_ids = set(
        db.scalars(
            select(StatusHistory.entity_id).where(
                StatusHistory.entity_type == "hub_issue",
                StatusHistory.to_status == "released",
                StatusHistory.changed_at >= start,
                StatusHistory.changed_at < end,
            )
        )
    )
    op_closed_ids = set(
        db.scalars(
            select(StatusHistory.entity_id).where(
                StatusHistory.entity_type == "hub_issue",
                StatusHistory.to_status == "closed",
                StatusHistory.changed_by.like("op:%"),
                StatusHistory.changed_at >= start,
                StatusHistory.changed_at < end,
            )
        )
    )
    hub_ids = released_ids | op_closed_ids
    tickets = _tickets_for_hub_ids(db, hub_ids)
    by_handler: dict[int | None, int] = {}
    for t in tickets:
        by_handler[t.handler_user_id] = by_handler.get(t.handler_user_id, 0) + 1
    return len(tickets), by_handler


def _reason_matched_counts(
    db: Session, start: datetime, end: datetime, *, reason_like: str
) -> tuple[int, dict[int | None, int]]:
    hub_ids = set(
        db.scalars(
            select(StatusHistory.entity_id).where(
                StatusHistory.entity_type == "hub_issue",
                StatusHistory.reason.like(reason_like),
                StatusHistory.changed_at >= start,
                StatusHistory.changed_at < end,
            )
        )
    )
    tickets = _tickets_for_hub_ids(db, hub_ids)
    by_handler: dict[int | None, int] = {}
    for t in tickets:
        by_handler[t.handler_user_id] = by_handler.get(t.handler_user_id, 0) + 1
    return len(tickets), by_handler


def _returned_to_ksm_counts(
    db: Session, start: datetime, end: datetime
) -> tuple[int, dict[int | None, int]]:
    rows = db.execute(
        select(Ticket.handler_user_id, func.count(SyncOutbox.id))
        .join(Ticket, SyncOutbox.ticket_id == Ticket.id)
        .where(
            SyncOutbox.kind == "return",
            SyncOutbox.status == "sent",
            SyncOutbox.sent_at >= start,
            SyncOutbox.sent_at < end,
        )
        .group_by(Ticket.handler_user_id)
    ).all()
    by_handler: dict[int | None, int] = {}
    for uid, cnt in rows:
        by_handler[uid] = cnt
    total = sum(by_handler.values())
    return total, by_handler


def _lifetime_totals(db: Session) -> LifetimeTotals:
    total = db.scalar(select(func.count(Ticket.id)).where(Ticket.deleted_at.is_(None))) or 0
    in_progress = (
        db.scalar(
            select(func.count(Ticket.id)).where(
                Ticket.deleted_at.is_(None), Ticket.status.in_(_IN_PROGRESS_STATUSES)
            )
        )
        or 0
    )
    completed = (
        db.scalar(
            select(func.count(Ticket.id)).where(
                Ticket.deleted_at.is_(None), Ticket.status.in_(_RESOLVED_STATUSES)
            )
        )
        or 0
    )
    returned_total = (
        db.scalar(
            select(func.count(StatusHistory.id)).where(
                StatusHistory.entity_type == "hub_issue",
                StatusHistory.reason.like("客户驳回%"),
            )
        )
        or 0
    )
    return LifetimeTotals(
        total=total,
        in_progress=in_progress,
        completed=completed,
        returned_to_ksm_total=returned_total,
    )


def compute_daily_dashboard(db: Session, *, date: str) -> DailyDashboard:
    start, end = _day_bounds(date)

    received_total, received_by = _received_counts(db, start, end)
    completed_total, completed_by = _completed_counts(db, start, end)
    returned_total, returned_by = _returned_to_ksm_counts(db, start, end)
    rejected_total, rejected_by = _reason_matched_counts(db, start, end, reason_like="客户驳回%")
    supplemented_total, supplemented_by = _reason_matched_counts(
        db, start, end, reason_like="%补料回流%"
    )

    user_ids = {
        uid
        for uid in (
            set(received_by)
            | set(completed_by)
            | set(returned_by)
            | set(rejected_by)
            | set(supplemented_by)
        )
        if uid is not None
    }
    names: dict[int, str] = {}
    if user_ids:
        for uid, name in db.execute(select(User.id, User.name).where(User.id.in_(user_ids))):
            names[uid] = name

    buckets = _mutable_bucket()
    for uid, cnt in received_by.items():
        b = _bucket_get(buckets, uid, names.get(uid, _UNASSIGNED_NAME) if uid else _UNASSIGNED_NAME)
        b["received"] += cnt
    for uid, cnt in completed_by.items():
        b = _bucket_get(buckets, uid, names.get(uid, _UNASSIGNED_NAME) if uid else _UNASSIGNED_NAME)
        b["completed"] += cnt
    for uid, cnt in returned_by.items():
        b = _bucket_get(buckets, uid, names.get(uid, _UNASSIGNED_NAME) if uid else _UNASSIGNED_NAME)
        b["returned_to_ksm"] += cnt
    for uid, cnt in rejected_by.items():
        b = _bucket_get(buckets, uid, names.get(uid, _UNASSIGNED_NAME) if uid else _UNASSIGNED_NAME)
        b["ksm_rejected"] += cnt
    for uid, cnt in supplemented_by.items():
        b = _bucket_get(buckets, uid, names.get(uid, _UNASSIGNED_NAME) if uid else _UNASSIGNED_NAME)
        b["supplemented"] += cnt

    by_assignee = [
        DailyByAssignee(
            user_id=b["user_id"],
            name=b["name"],
            received=b["received"],
            completed=b["completed"],
            returned_to_ksm=b["returned_to_ksm"],
            ksm_rejected=b["ksm_rejected"],
            supplemented=b["supplemented"],
        )
        for b in sorted(
            buckets.values(),
            key=lambda b: (
                b["user_id"] is None,
                -sum(
                    b[k]
                    for k in (
                        "received",
                        "completed",
                        "returned_to_ksm",
                        "ksm_rejected",
                        "supplemented",
                    )
                ),
            ),
        )
    ]

    return DailyDashboard(
        date=date,
        totals=DailyTotals(
            received=received_total,
            completed=completed_total,
            returned_to_ksm=returned_total,
            ksm_rejected=rejected_total,
            supplemented=supplemented_total,
        ),
        lifetime=_lifetime_totals(db),
        by_assignee=by_assignee,
    )
