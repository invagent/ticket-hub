"""stage 对账（ADR-0017 Task 1.4）：比对「存储的 stage」与「按旧字段派生的 stage」。

监听器覆盖不到的写入路径（裸 SQL、外部脚本、迁移前存量）会在这里暴露为 drift。
纯函数 `find_drift` 便于单测；`reconcile` 负责查库/修正，`scripts/reconcile_stage.py`
是薄 CLI。修正时 actor 固定 `system:stage_reconcile`。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.state.listeners import STAGE_ACTOR_KEY, sync_hub_stage, sync_ticket_stage
from app.services.state.stage import derive_hub_stage, derive_ticket_stage

RECONCILE_ACTOR = "system:stage_reconcile"


@dataclass(slots=True, frozen=True)
class Drift:
    entity: str  # 'ticket' | 'hub_issue'
    entity_id: int
    stored: str | None
    derived: str


@dataclass(slots=True)
class ReconcileReport:
    tickets_checked: int = 0
    hubs_checked: int = 0
    drifts: list[Drift] | None = None
    fixed: int = 0

    def __post_init__(self) -> None:
        if self.drifts is None:
            self.drifts = []


def find_drift(tickets: list[tuple[Any, Any | None]], hubs: list[Any]) -> list[Drift]:
    """纯函数：输入 (ticket, hub|None) 对与 hub 列表，输出不一致清单。"""
    out: list[Drift] = []
    for hub in hubs:
        d = derive_hub_stage(hub)
        if hub.stage != d:
            out.append(Drift("hub_issue", hub.id, hub.stage, d))
    for t, hub in tickets:
        d = derive_ticket_stage(t, hub)
        if t.stage != d:
            out.append(Drift("ticket", t.id, t.stage, d))
    return out


def reconcile(db: Session, *, fix: bool = False, limit: int | None = None) -> ReconcileReport:
    from app.models import HubIssue, Ticket

    report = ReconcileReport()
    hub_stmt = select(HubIssue).where(HubIssue.deleted_at.is_(None)).order_by(HubIssue.id)
    t_stmt = select(Ticket).where(Ticket.deleted_at.is_(None)).order_by(Ticket.id)
    if limit:
        hub_stmt = hub_stmt.limit(limit)
        t_stmt = t_stmt.limit(limit)
    hubs = list(db.execute(hub_stmt).scalars())
    tickets = list(db.execute(t_stmt).scalars())
    hub_by_id = {h.id: h for h in hubs}
    pairs: list[tuple[Any, Any | None]] = []
    for t in tickets:
        hub = None
        if t.hub_issue_id is not None:
            hub = hub_by_id.get(t.hub_issue_id) or db.get(HubIssue, t.hub_issue_id)
        pairs.append((t, hub))

    report.hubs_checked = len(hubs)
    report.tickets_checked = len(tickets)
    report.drifts = find_drift(pairs, hubs)

    if fix and report.drifts:
        db.info[STAGE_ACTOR_KEY] = RECONCILE_ACTOR
        now = datetime.now(UTC)
        for d in report.drifts:
            if d.entity == "hub_issue":
                hub = hub_by_id[d.entity_id]
                if sync_hub_stage(db, hub, now=now):
                    report.fixed += 1
            else:
                t = next(t for t, _ in pairs if t.id == d.entity_id)
                hub = hub_by_id.get(t.hub_issue_id) if t.hub_issue_id is not None else None
                if hub is None and t.hub_issue_id is not None:
                    hub = db.get(HubIssue, t.hub_issue_id)
                if sync_ticket_stage(db, t, hub, now=now):
                    report.fixed += 1
        db.flush()
    return report
