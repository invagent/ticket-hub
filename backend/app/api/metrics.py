"""GET /api/metrics/dashboard — D1 verification dashboard.

Returns volume counts + 4 quantitative SLO indicators per upgrade_plan §12.

Auth: any logged-in user (read-only system-wide aggregate; no PII).
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps.auth import AuthedUser, require_user
from app.db import get_session
from app.services.metrics.dashboard import get_dashboard_metrics

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
