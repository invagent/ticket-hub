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

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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
    # 最近 7 天真实快照（materializer beat 每日 UPSERT 积累；不足 2 点前端不画线）
    trend: list[dict[str, Any]] = field(default_factory=list)  # [{date, value}]


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

    # ---- SLO 本期 vs 上期 + 7 日趋势 -----------------------------------
    cur = _window_slo(db, start, None)
    prev = _window_slo(db, prev_start, start)
    trends = load_slo_trend(db, now=now)
    today_key = (now or datetime.now(UTC)).astimezone(BEIJING).date().isoformat()
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
        # 今天的点用实时值（快照最多滞后一个 beat 周期）
        trend = [p for p in trends.get(key, []) if p["date"] != today_key]
        trend.append({"date": today_key, "value": _ratio(*cur[key])})
        slo.append(SloItem(key=key, name=name, value=value, delta_pt=delta, good=good, trend=trend))

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


# ---- SLO 每日快照（真实趋势线的数据源） ------------------------------------
#
# materializer beat（5 分钟）顺带调 snapshot_today_slo：把「今日」四项 SLO
# UPSERT 进 materialized_metrics(slot_key='slo:{北京日期}')——每天一行，当日
# 内反复覆盖，跨天自然留痕。积累 ≥2 天后前端才画 sparkline，绝不编造历史。

_SLO_SLOT_PREFIX = "slo:"
_TREND_DAYS = 7


def snapshot_today_slo(db: Session, *, now: datetime | None = None) -> str:
    """UPSERT 今日 SLO 快照行，返回 slot_key。Caller commits。"""
    from app.models import MaterializedMetrics

    now = now or datetime.now(UTC)
    day = now.astimezone(BEIJING).date().isoformat()
    slot = f"{_SLO_SLOT_PREFIX}{day}"
    # 窗口 [当日零点, now)：快照=「截至 now 的当日值」；封顶防止给历史日期
    # 落快照时把之后的数据算进去（生产 now=当下，等价于不封顶）
    cur = _window_slo(db, range_window("today", now=now)[0], now)
    payload = {key: _ratio(*cur[key]) for key, _, _ in _SLO_DEFS}
    row = db.execute(
        select(MaterializedMetrics).where(MaterializedMetrics.slot_key == slot)
    ).scalar_one_or_none()
    if row is None:
        db.add(MaterializedMetrics(slot_key=slot, metrics_json=payload))
    else:
        row.metrics_json = payload
        row.refreshed_at = now
    db.flush()
    return slot


def load_slo_trend(
    db: Session, *, now: datetime | None = None, days: int = _TREND_DAYS
) -> dict[str, list[dict[str, Any]]]:
    """读最近 N 天快照 → {slo_key: [{date, value}...]}（按日期升序，缺天跳过）。"""
    from app.models import MaterializedMetrics

    now = now or datetime.now(UTC)
    today = now.astimezone(BEIJING).date()
    slots = [f"{_SLO_SLOT_PREFIX}{(today - timedelta(days=i)).isoformat()}" for i in range(days)]
    rows = db.execute(
        select(MaterializedMetrics).where(MaterializedMetrics.slot_key.in_(slots))
    ).scalars()
    by_date: dict[str, dict[str, Any]] = {
        r.slot_key.removeprefix(_SLO_SLOT_PREFIX): r.metrics_json for r in rows
    }
    out: dict[str, list[dict[str, Any]]] = {key: [] for key, _, _ in _SLO_DEFS}
    for date_key in sorted(by_date):
        payload = by_date[date_key]
        for key, _, _ in _SLO_DEFS:
            if isinstance(payload, dict) and key in payload:
                out[key].append({"date": date_key, "value": float(payload[key])})
    return out
