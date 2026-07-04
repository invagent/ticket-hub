"""工作台看板指标（2026-07 后台重构，屏幕1）.

与 dashboard.py 的全量累计指标不同，这里一切按时间范围（今日/本周/本月，
北京时区切界）计算，并给出与上一同长周期的真实环比 delta——不编造趋势线：
没有历史快照表，sparkline 不做；环比用「本期窗口 vs 上一窗口」重算，真实可信。

漏斗语义（对本期新建工单的递进子集，允许非严格递减）：
    received    本期新建（含所有状态）
    classified  其中 AI 已给出 predicted_type 的
    assigned    其中已有处理人的
    in_progress 状态推进到处理链路中的
    resolved    状态已终结（done/closed/superseded/rejected）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CustomerIdentity, NotificationLog, Ticket, TicketHubIssueHistory
from app.services.sla.workday import BEIJING

_RANGES = ("today", "week", "month")

_IN_PROGRESS_STATUSES = (
    "linked",
    "waiting_reply",
    "waiting_schedule",
    "scheduled",
    "in_progress",
    "code_merged",
    "released",
)
_RESOLVED_STATUSES = ("done", "closed", "superseded", "rejected")


@dataclass(slots=True, frozen=True)
class FunnelBlock:
    received: int
    classified: int
    assigned: int
    in_progress: int
    resolved: int


@dataclass(slots=True, frozen=True)
class SloItem:
    key: str
    name: str
    value: float  # 0.0–1.0（本期）
    delta_pt: float | None  # 与上一周期的差值（百分点）；上期无数据为 None
    good: bool  # delta 方向是否向好（调整率下降=好）


@dataclass(slots=True, frozen=True)
class WorkbenchMetrics:
    range: str
    range_start: datetime
    prev_start: datetime
    funnel: FunnelBlock
    slo: list[SloItem]
    sources: dict[str, int]


def range_window(range_key: str, *, now: datetime | None = None) -> tuple[datetime, datetime]:
    """[start, prev_start]：本期起点 + 上一同长周期起点（北京时区切界）。"""
    if range_key not in _RANGES:
        raise ValueError(f"invalid range {range_key!r}; must be one of {_RANGES}")
    from datetime import UTC

    now = now or datetime.now(UTC)
    local = now.astimezone(BEIJING)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if range_key == "today":
        start = midnight
        prev_start = start - timedelta(days=1)
    elif range_key == "week":
        start = midnight - timedelta(days=local.weekday())  # 本周一
        prev_start = start - timedelta(days=7)
    else:  # month
        start = midnight.replace(day=1)
        prev_month_end = start - timedelta(days=1)
        prev_start = prev_month_end.replace(day=1)
    return start, prev_start


def _ratio(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


def _window_slo(db: Session, start: datetime, end: datetime | None) -> dict[str, tuple[int, int]]:
    """一个窗口内四项 SLO 的 (分子, 分母)。end=None 表示到当前。"""

    def _tickets(*extra: Any) -> int:
        q = select(func.count(Ticket.id)).where(
            Ticket.deleted_at.is_(None), Ticket.created_at >= start, *extra
        )
        if end is not None:
            q = q.where(Ticket.created_at < end)
        return db.scalar(q) or 0

    total = _tickets()
    auto = _tickets(Ticket.assigned_user_id.is_not(None))

    linked = _tickets(Ticket.hub_issue_id.is_not(None))
    relink_q = select(func.count(TicketHubIssueHistory.id)).where(
        TicketHubIssueHistory.effective_to.is_not(None),
        TicketHubIssueHistory.effective_to >= start,
    )
    if end is not None:
        relink_q = relink_q.where(TicketHubIssueHistory.effective_to < end)
    relinks = db.scalar(relink_q) or 0

    ident_q = select(func.count(CustomerIdentity.id)).where(
        CustomerIdentity.deleted_at.is_(None), CustomerIdentity.created_at >= start
    )
    ident_matched_q = ident_q.where(CustomerIdentity.resolved_by_key != "none")
    if end is not None:
        ident_q = ident_q.where(CustomerIdentity.created_at < end)
        ident_matched_q = ident_matched_q.where(CustomerIdentity.created_at < end)
    idents = db.scalar(ident_q) or 0
    idents_matched = db.scalar(ident_matched_q) or 0

    def _notif(*extra: Any) -> int:
        q = select(func.count(NotificationLog.id)).where(NotificationLog.sent_at >= start, *extra)
        if end is not None:
            q = q.where(NotificationLog.sent_at < end)
        return db.scalar(q) or 0

    acked = _notif(NotificationLog.acknowledged_at.is_not(None))
    escalated = _notif(NotificationLog.escalated_at.is_not(None))

    return {
        "auto_hit": (auto, total),
        "relink": (relinks, linked),
        "identity": (idents_matched, idents),
        "sla_ack": (acked, acked + escalated),
    }


_SLO_DEFS = [
    # key, 名称, 值越大越好？
    ("auto_hit", "自动分配命中率", True),
    ("relink", "主管调整率", False),
    ("identity", "客户识别准度", True),
    ("sla_ack", "SLA 确认率", True),
]


def compute_workbench_metrics(
    db: Session, range_key: str, *, now: datetime | None = None
) -> WorkbenchMetrics:
    start, prev_start = range_window(range_key, now=now)

    # ---- 漏斗（本期新建工单的递进子集） --------------------------------
    def _count(*extra: Any) -> int:
        return (
            db.scalar(
                select(func.count(Ticket.id)).where(
                    Ticket.deleted_at.is_(None), Ticket.created_at >= start, *extra
                )
            )
            or 0
        )

    funnel = FunnelBlock(
        received=_count(),
        classified=_count(Ticket.predicted_type.is_not(None)),
        assigned=_count(Ticket.assigned_user_id.is_not(None)),
        in_progress=_count(Ticket.status.in_(_IN_PROGRESS_STATUSES)),
        resolved=_count(Ticket.status.in_(_RESOLVED_STATUSES)),
    )

    # ---- SLO 本期 vs 上期 ---------------------------------------------
    cur = _window_slo(db, start, None)
    prev = _window_slo(db, prev_start, start)
    slo: list[SloItem] = []
    for key, name, higher_better in _SLO_DEFS:
        c_num, c_den = cur[key]
        p_num, p_den = prev[key]
        value = _ratio(c_num, c_den)
        if p_den == 0:
            delta = None
            good = True
        else:
            delta = round((value - _ratio(p_num, p_den)) * 100, 1)  # 百分点
            good = delta >= 0 if higher_better else delta <= 0
        slo.append(SloItem(key=key, name=name, value=value, delta_pt=delta, good=good))

    # ---- 来源分布 ------------------------------------------------------
    rows = db.execute(
        select(Ticket.source_code, func.count(Ticket.id))
        .where(Ticket.deleted_at.is_(None), Ticket.created_at >= start)
        .group_by(Ticket.source_code)
    ).all()
    sources = {str(r[0] or "unknown"): int(r[1]) for r in rows}

    return WorkbenchMetrics(
        range=range_key,
        range_start=start,
        prev_start=prev_start,
        funnel=funnel,
        slo=slo,
        sources=sources,
    )
