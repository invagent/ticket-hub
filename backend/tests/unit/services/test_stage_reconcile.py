"""ADR-0017 Task 1.4：stage 对账。"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import HubIssue, StatusHistory, Ticket
from app.services.state.reconcile import find_drift, reconcile


def test_find_drift_pure() -> None:
    hub_ok = SimpleNamespace(
        id=1,
        type="Operation",
        status="created",
        op_status="answered",
        linear_status=None,
        stage="answered",
    )
    hub_bad = SimpleNamespace(
        id=2,
        type="Bug_fix",
        status="in_progress",
        op_status=None,
        linear_status="Done",
        stage="in_dev",
    )
    t_ok = SimpleNamespace(
        id=10,
        status="received",
        type="Raw",
        predicted_type=None,
        hub_issue_id=None,
        stage="received",
    )
    t_bad = SimpleNamespace(
        id=11, status="received", type="Raw", predicted_type=None, hub_issue_id=2, stage=None
    )
    drifts = find_drift([(t_ok, None), (t_bad, hub_bad)], [hub_ok, hub_bad])
    assert [(d.entity, d.entity_id, d.stored, d.derived) for d in drifts] == [
        ("hub_issue", 2, "in_dev", "released"),
        ("ticket", 11, None, "released"),
    ]


def test_reconcile_detects_and_fixes_raw_sql_drift(db_session: Session) -> None:
    hub = HubIssue(
        short_code="HUB-000001",
        type="Operation",
        title="h",
        status="created",
        op_status="processing",
    )
    db_session.add(hub)
    db_session.flush()
    t = Ticket(
        short_code="TKT-000001",
        source_code="ksm",
        source_ticket_id="k1",
        type="Raw",
        status="received",
        title="t",
        hub_issue_id=hub.id,
    )
    db_session.add(t)
    db_session.commit()
    assert t.stage == "processing"

    # 绕过 ORM 监听器的裸 SQL 写入 → 漂移
    db_session.execute(
        text("UPDATE hub_issues SET op_status='answered' WHERE id=:i"), {"i": hub.id}
    )
    db_session.commit()
    db_session.expire_all()

    report = reconcile(db_session)
    assert report.hubs_checked == 1 and report.tickets_checked == 1
    assert {(d.entity, d.derived) for d in report.drifts or []} == {
        ("hub_issue", "answered"),
        ("ticket", "answered"),
    }

    report = reconcile(db_session, fix=True)
    db_session.commit()
    assert report.fixed == 2
    assert db_session.get(HubIssue, hub.id).stage == "answered"  # type: ignore[union-attr]
    assert db_session.get(Ticket, t.id).stage == "answered"  # type: ignore[union-attr]
    rows = list(
        db_session.execute(
            select(StatusHistory).where(StatusHistory.entity_type == "ticket_stage")
        ).scalars()
    )
    assert rows[-1].changed_by == "system:stage_reconcile"
    assert reconcile(db_session).drifts == []
