"""GET /api/metrics/dashboard + /api/metrics/workbench.

dashboard — D1 verification dashboard（全量累计，保留兼容）。
workbench — 2026-07 后台重构工作台看板：按今日/本周/本月的漏斗 + SLO 环比 + 来源分布。

Auth: any logged-in user (read-only system-wide aggregate; no PII).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps.auth import AuthedUser, require_user
from app.db import get_session
from app.services.metrics.dashboard import get_dashboard_metrics
from app.services.metrics.workbench import compute_workbench_metrics

router = APIRouter()


class CountsOut(BaseModel):
    tickets_total: int
    tickets_active: int
    hub_issues_total: int
    customers_total: int
    users_total: int
    notifications_pending: int


class RoutingOut(BaseModel):
    tickets_total: int
    auto_assigned: int
    auto_hit_rate: float
    target: str


class SupervisorOut(BaseModel):
    linked_tickets: int
    relink_count: int
    relink_rate: float
    target: str


class CustomerDedupOut(BaseModel):
    identities_total: int
    identities_matched: int
    match_rate: float
    target: str


class SLAOut(BaseModel):
    notifications_total: int
    pending: int
    acknowledged: int
    escalated: int
    acknowledgement_rate: float
    target: str


class WebhookIntakeOut(BaseModel):
    window_hours: int
    by_source: dict[str, int]
    total: int
    deduped_total: int


class DashboardOut(BaseModel):
    counts: CountsOut
    routing: RoutingOut
    supervisor: SupervisorOut
    customer_dedup: CustomerDedupOut
    sla: SLAOut
    webhook_intake: WebhookIntakeOut


@router.get("/dashboard", response_model=DashboardOut)
def dashboard_metrics(
    _user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> DashboardOut:
    m = get_dashboard_metrics(db)
    return DashboardOut(
        counts=CountsOut(**asdict(m.counts)),
        routing=RoutingOut(**asdict(m.routing)),
        supervisor=SupervisorOut(**asdict(m.supervisor)),
        customer_dedup=CustomerDedupOut(**asdict(m.customer_dedup)),
        sla=SLAOut(**asdict(m.sla)),
        webhook_intake=WebhookIntakeOut(**asdict(m.webhook_intake)),
    )


class FunnelOut(BaseModel):
    received: int
    classified: int
    assigned: int
    in_progress: int
    resolved: int


class SloTrendPoint(BaseModel):
    date: str  # 北京日期 YYYY-MM-DD
    value: float  # 0.0–1.0


class SloItemOut(BaseModel):
    key: str
    name: str
    value: float  # 0.0–1.0
    delta_pt: float | None  # 环比上一周期（百分点）；上期无数据为 null
    good: bool
    # 最近 7 天真实快照（beat 每日积累；<2 点前端不画线）
    trend: list[SloTrendPoint] = []


class WorkbenchOut(BaseModel):
    range: str
    range_start: datetime
    prev_start: datetime
    funnel: FunnelOut
    slo: list[SloItemOut]
    sources: dict[str, int]


@router.get("/workbench", response_model=WorkbenchOut)
def workbench_metrics(
    _user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
    range: Literal["today", "week", "month"] = Query("today"),
) -> WorkbenchOut:
    m = compute_workbench_metrics(db, range)
    return WorkbenchOut(
        range=m.range,
        range_start=m.range_start,
        prev_start=m.prev_start,
        funnel=FunnelOut(**asdict(m.funnel)),
        slo=[SloItemOut(**asdict(s)) for s in m.slo],
        sources=m.sources,
    )
