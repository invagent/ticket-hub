"""Tests for GET /api/tickets/{id}/history."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import (
    HubIssue,
    Source,
    StatusHistory,
    Ticket,
    TicketHubIssueHistory,
    User,
)


def _bearer(uid: int = 1, *, role: str = "assignee") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(uid), name="t", role=role)
    return {"Authorization": f"Bearer {token}"}


T0 = datetime(2026, 5, 6, 10, 0, tzinfo=UTC)


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(User(id=1, feishu_uid="ou_a", name="alice", role="assignee"))
    db_session.add_all(
        [
            HubIssue(id=10, short_code="HUB-A", type="Operation", title="A", status="created"),
            HubIssue(id=20, short_code="HUB-B", type="Operation", title="B", status="created"),
        ]
    )
    db_session.flush()
    db_session.add(
        Ticket(
            id=100,
            short_code="TKT-1",
            source_code="ksm",
            source_ticket_id="ksm-1",
            type="Raw",
            status="received",
            received_at=T0,
        )
    )
    db_session.commit()
    return db_session


# ---- shape -----------------------------------------------------------------


def test_history_404_when_ticket_missing(app_client, world: Session) -> None:
    resp = app_client.get("/api/tickets/9999/history", headers=_bearer())
    assert resp.status_code == 404


def test_history_requires_auth(app_client, world: Session) -> None:
    assert app_client.get("/api/tickets/100/history").status_code == 401


def test_history_empty_returns_empty_items(app_client, world: Session) -> None:
    r = app_client.get("/api/tickets/100/history", headers=_bearer())
    assert r.status_code == 200
    body = r.json()
    assert body["ticket_id"] == 100
    assert body["items"] == []


# ---- merge + ordering ------------------------------------------------------


def test_status_only_history(app_client, world: Session) -> None:
    world.add_all(
        [
            StatusHistory(
                entity_type="ticket",
                entity_id=100,
                from_status=None,
                to_status="received",
                changed_by="system:ingest",
                reason="ksm webhook",
                changed_at=T0,
            ),
            StatusHistory(
                entity_type="ticket",
                entity_id=100,
                from_status="received",
                to_status="linked",
                changed_by="agent:dedup",
                changed_at=T0 + timedelta(minutes=5),
            ),
        ]
    )
    world.commit()

    r = app_client.get("/api/tickets/100/history", headers=_bearer())
    items = r.json()["items"]
    assert len(items) == 2
    assert all(i["kind"] == "status" for i in items)
    assert items[0]["to_status"] == "received"
    assert items[1]["from_status"] == "received"
    assert items[1]["to_status"] == "linked"


def test_relink_only_history(app_client, world: Session) -> None:
    world.add_all(
        [
            TicketHubIssueHistory(
                ticket_id=100,
                hub_issue_id=10,
                effective_from=T0 + timedelta(minutes=10),
                effective_to=T0 + timedelta(hours=2),
                change_reason="initial dedup",
            ),
            TicketHubIssueHistory(
                ticket_id=100,
                hub_issue_id=20,
                effective_from=T0 + timedelta(hours=2),
                effective_to=None,
                change_reason="supervisor relink",
                human_confirmed=True,
            ),
        ]
    )
    world.commit()

    r = app_client.get("/api/tickets/100/history", headers=_bearer())
    items = r.json()["items"]
    assert len(items) == 2
    assert all(i["kind"] == "hub_issue_link" for i in items)
    assert items[0]["hub_issue_id"] == 10
    assert items[0]["effective_to"] is not None  # closed
    assert items[1]["hub_issue_id"] == 20
    assert items[1]["effective_to"] is None  # current
    assert items[1]["human_confirmed"] is True


def test_merged_history_chronological(app_client, world: Session) -> None:
    """Status + relink events interleaved by occurred_at ASC."""
    world.add_all(
        [
            StatusHistory(
                entity_type="ticket",
                entity_id=100,
                to_status="received",
                changed_by="system:ingest",
                changed_at=T0,
            ),
            TicketHubIssueHistory(
                ticket_id=100,
                hub_issue_id=10,
                effective_from=T0 + timedelta(minutes=2),
                change_reason="initial",
            ),
            StatusHistory(
                entity_type="ticket",
                entity_id=100,
                from_status="received",
                to_status="linked",
                changed_by="agent:dedup",
                changed_at=T0 + timedelta(minutes=2, seconds=1),
            ),
            TicketHubIssueHistory(
                ticket_id=100,
                hub_issue_id=10,
                effective_from=T0 + timedelta(hours=1),
                effective_to=T0 + timedelta(hours=1),
                change_reason="closed by supervisor relink",
            ),
            TicketHubIssueHistory(
                ticket_id=100,
                hub_issue_id=20,
                effective_from=T0 + timedelta(hours=1, seconds=1),
                change_reason="supervisor relink",
                human_confirmed=True,
            ),
            StatusHistory(
                entity_type="ticket",
                entity_id=100,
                from_status="linked",
                to_status="waiting_reply",
                changed_by="cascade:status_cascade",
                changed_at=T0 + timedelta(hours=2),
            ),
        ]
    )
    world.commit()

    r = app_client.get("/api/tickets/100/history", headers=_bearer())
    items = r.json()["items"]
    assert len(items) == 6
    kinds = [i["kind"] for i in items]
    # Chronological order:
    # status(T0) → link(T0+2m) → status(T0+2m1s) → link-close(T0+1h)
    # → link(T0+1h1s) → status(T0+2h)
    assert kinds == [
        "status",
        "hub_issue_link",
        "status",
        "hub_issue_link",
        "hub_issue_link",
        "status",
    ]


def test_history_isolated_per_ticket(app_client, world: Session) -> None:
    """Other ticket's events must not leak into the target ticket's history."""
    world.add(
        Ticket(
            id=101,
            short_code="TKT-OTHER",
            source_code="ksm",
            source_ticket_id="ksm-other",
            type="Raw",
            status="received",
        )
    )
    world.flush()
    world.add_all(
        [
            StatusHistory(
                entity_type="ticket",
                entity_id=100,
                to_status="received",
                changed_by="system:ingest",
                changed_at=T0,
            ),
            StatusHistory(
                entity_type="ticket",
                entity_id=101,
                to_status="received",
                changed_by="system:ingest",
                changed_at=T0,
            ),
            # hub_issue status_history with same id 100 — must NOT be returned
            StatusHistory(
                entity_type="hub_issue",
                entity_id=100,
                to_status="created",
                changed_by="system:ingest",
                changed_at=T0,
            ),
        ]
    )
    world.commit()

    r = app_client.get("/api/tickets/100/history", headers=_bearer())
    items = r.json()["items"]
    assert len(items) == 1  # only the ticket-100 row


def test_status_first_when_same_timestamp(app_client, world: Session) -> None:
    """At identical occurred_at, status precedes hub_issue_link in the response.

    This matches our "status is the cause; relink is the effect" convention.
    """
    same_ts = T0 + timedelta(minutes=1)
    world.add_all(
        [
            TicketHubIssueHistory(
                ticket_id=100,
                hub_issue_id=10,
                effective_from=same_ts,
                change_reason="x",
            ),
            StatusHistory(
                entity_type="ticket",
                entity_id=100,
                to_status="linked",
                changed_by="agent:dedup",
                changed_at=same_ts,
            ),
        ]
    )
    world.commit()

    r = app_client.get("/api/tickets/100/history", headers=_bearer())
    items = r.json()["items"]
    assert items[0]["kind"] == "status"
    assert items[1]["kind"] == "hub_issue_link"
