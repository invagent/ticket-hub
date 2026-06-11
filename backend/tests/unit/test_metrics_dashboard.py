"""Tests for /api/metrics/dashboard."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import (
    Customer,
    CustomerIdentity,
    HubIssue,
    NotificationLog,
    Source,
    Ticket,
    TicketHubIssueHistory,
    User,
)


def _bearer(uid: int = 1, *, role: str = "assignee") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(uid), name="t", role=role)
    return {"Authorization": f"Bearer {token}"}


# ---- auth ---------------------------------------------------------------


def test_dashboard_requires_auth(app_client) -> None:
    assert app_client.get("/api/metrics/dashboard").status_code == 401


# ---- empty database -----------------------------------------------------


def test_dashboard_empty_returns_zero_rates(app_client) -> None:
    """No tickets / identities / notifications → all rates are 0.0, no divide-by-zero."""
    r = app_client.get("/api/metrics/dashboard", headers=_bearer())
    assert r.status_code == 200
    body = r.json()

    assert body["counts"]["tickets_total"] == 0
    assert body["routing"]["auto_hit_rate"] == 0.0
    assert body["supervisor"]["relink_rate"] == 0.0
    assert body["customer_dedup"]["match_rate"] == 0.0
    assert body["sla"]["acknowledgement_rate"] == 0.0

    # Targets always present (UI displays them next to actuals)
    assert body["routing"]["target"] == "≥ 0.95"
    assert body["supervisor"]["target"] == "< 0.10"
    assert body["customer_dedup"]["target"] == "≥ 0.90"
    assert body["sla"]["target"] == "≥ 0.90"


# ---- routing -----------------------------------------------------------


@pytest.fixture
def seeded_routing(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(User(id=1, feishu_uid="ou_a", name="alice", role="assignee"))
    db_session.flush()
    # 8 tickets total: 6 auto-assigned, 2 unassigned (multi_match unfilled)
    for i in range(8):
        db_session.add(
            Ticket(
                short_code=f"TKT-{i}",
                source_code="ksm",
                source_ticket_id=f"ksm-{i}",
                type="Raw",
                status="received",
                assigned_user_id=1 if i < 6 else None,
            )
        )
    db_session.commit()
    return db_session


def test_routing_auto_hit_rate(app_client, seeded_routing: Session) -> None:
    r = app_client.get("/api/metrics/dashboard", headers=_bearer())
    body = r.json()
    assert body["routing"]["tickets_total"] == 8
    assert body["routing"]["auto_assigned"] == 6
    assert body["routing"]["auto_hit_rate"] == 0.75


def test_soft_deleted_tickets_excluded_from_routing(app_client, seeded_routing: Session) -> None:
    seeded_routing.add(
        Ticket(
            short_code="TKT-DEL",
            source_code="ksm",
            source_ticket_id="ksm-del",
            type="Raw",
            status="done",
            assigned_user_id=1,
            deleted_at=datetime.now(UTC),
        )
    )
    seeded_routing.commit()
    r = app_client.get("/api/metrics/dashboard", headers=_bearer())
    body = r.json()
    # Soft-deleted ticket should NOT inflate either side
    assert body["routing"]["tickets_total"] == 8
    assert body["routing"]["auto_assigned"] == 6


# ---- supervisor relink -------------------------------------------------


def test_relink_rate(app_client, db_session: Session) -> None:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(
        HubIssue(id=10, short_code="HUB-A", type="Operation", title="A", status="created")
    )
    db_session.flush()
    # 4 tickets linked, 1 of which had a relink
    for i in range(4):
        db_session.add(
            Ticket(
                id=100 + i,
                short_code=f"TKT-{i}",
                source_code="ksm",
                source_ticket_id=f"ksm-{i}",
                type="Raw",
                status="linked",
                hub_issue_id=10,
            )
        )
    db_session.flush()
    # ticket 100 had a relink: history row with effective_to NOT NULL
    db_session.add(
        TicketHubIssueHistory(
            ticket_id=100,
            hub_issue_id=10,
            effective_to=datetime.now(UTC),  # closed = relinked away
            change_reason="initial",
        )
    )
    db_session.add(
        TicketHubIssueHistory(ticket_id=100, hub_issue_id=10, change_reason="relinked back")
    )
    db_session.commit()

    body = app_client.get("/api/metrics/dashboard", headers=_bearer()).json()
    assert body["supervisor"]["linked_tickets"] == 4
    assert body["supervisor"]["relink_count"] == 1  # only the closed history row
    assert body["supervisor"]["relink_rate"] == 0.25


# ---- customer dedup ----------------------------------------------------


def test_customer_dedup_match_rate(app_client, db_session: Session) -> None:
    db_session.add(Source(code="ksm", name="KSM"))
    cust = Customer(display_name="x")
    db_session.add(cust)
    db_session.flush()
    # 5 identities: 4 matched (erp_uid/mobile/email/manual), 1 'none' (new customer)
    for key in ("erp_uid", "mobile", "email", "manual", "none"):
        db_session.add(
            CustomerIdentity(
                customer_id=cust.id,
                source_code="ksm",
                source_user_id=f"u-{key}",
                resolved_by_key=key,
            )
        )
    db_session.commit()

    body = app_client.get("/api/metrics/dashboard", headers=_bearer()).json()
    assert body["customer_dedup"]["identities_total"] == 5
    assert body["customer_dedup"]["identities_matched"] == 4
    assert body["customer_dedup"]["match_rate"] == 0.8


# ---- SLA / notifications ----------------------------------------------


def test_sla_acknowledgement_rate(app_client, db_session: Session) -> None:
    db_session.add(User(id=1, feishu_uid="ou_a", name="alice", role="assignee"))
    db_session.flush()
    now = datetime.now(UTC)
    # 6 notifications:
    #   3 acked (closed → counted in denominator + numerator)
    #   1 escalated (closed → in denominator only)
    #   2 pending (excluded from rate calculation; included in `pending` count)
    db_session.add_all(
        [
            NotificationLog(
                recipient_user_id=1,
                channel="feishu_bot",
                notify_type="sla_overdue",
                payload={},
                acknowledged_at=now,
            ),
            NotificationLog(
                recipient_user_id=1,
                channel="feishu_bot",
                notify_type="sla_overdue",
                payload={},
                acknowledged_at=now,
            ),
            NotificationLog(
                recipient_user_id=1,
                channel="feishu_bot",
                notify_type="sla_overdue",
                payload={},
                acknowledged_at=now,
            ),
            NotificationLog(
                recipient_user_id=1,
                channel="feishu_bot",
                notify_type="escalation",
                payload={},
                escalated_at=now - timedelta(hours=1),
            ),
            NotificationLog(
                recipient_user_id=1,
                channel="feishu_bot",
                notify_type="sla_overdue",
                payload={},
            ),
            NotificationLog(
                recipient_user_id=1,
                channel="feishu_bot",
                notify_type="sla_overdue",
                payload={},
            ),
        ]
    )
    db_session.commit()

    body = app_client.get("/api/metrics/dashboard", headers=_bearer()).json()
    sla = body["sla"]
    assert sla["notifications_total"] == 6
    assert sla["acknowledged"] == 3
    assert sla["escalated"] == 1
    assert sla["pending"] == 2
    # 3 acked / (3 acked + 1 escalated) = 0.75
    assert sla["acknowledgement_rate"] == 0.75


# ---- counts -----------------------------------------------------------


def test_counts_block_active_status_filter(app_client, db_session: Session) -> None:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add_all(
        [
            Ticket(
                short_code="A",
                source_code="ksm",
                source_ticket_id="a",
                type="Raw",
                status="received",
            ),
            Ticket(
                short_code="B",
                source_code="ksm",
                source_ticket_id="b",
                type="Raw",
                status="done",
            ),
            Ticket(
                short_code="C",
                source_code="ksm",
                source_ticket_id="c",
                type="Raw",
                status="superseded",
            ),
        ]
    )
    db_session.commit()
    body = app_client.get("/api/metrics/dashboard", headers=_bearer()).json()
    assert body["counts"]["tickets_total"] == 3
    assert body["counts"]["tickets_active"] == 1  # only "received" is active
