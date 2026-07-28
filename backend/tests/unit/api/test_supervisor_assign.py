"""Tests for POST /api/supervisor/assign (manual指派端点)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import Source, Ticket, User


def _bearer(user_id: int, *, name: str = "test", role: str = "supervisor") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(user_id), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def assign_world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add_all(
        [
            User(id=1, feishu_uid="ou_carol", name="carol", role="supervisor"),
            User(id=2, feishu_uid="ou_bob", name="bob", role="member"),
            User(id=3, feishu_uid="ou_dev", name="dev", role="assignee"),
        ]
    )
    db_session.flush()
    db_session.add_all(
        [
            Ticket(
                id=100,
                short_code="A-1",
                source_code="ksm",
                source_ticket_id="ksm-1",
                type="Raw",
                status="received",
            ),
            Ticket(
                id=101,
                short_code="A-2",
                source_code="ksm",
                source_ticket_id="ksm-2",
                type="Raw",
                status="received",
            ),
            Ticket(
                id=102,
                short_code="A-3",
                source_code="ksm",
                source_ticket_id="ksm-3",
                type="Raw",
                status="received",
            ),
        ]
    )
    db_session.commit()
    return db_session


def test_assign_endpoint_success(app_client: TestClient, assign_world: Session) -> None:
    resp = app_client.post(
        "/api/supervisor/assign",
        json={"ticket_ids": [100], "assigned_user_id": 3},
        headers=_bearer(1),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["assigned_count"] == 1
    assert body["not_found_count"] == 0
    assert body["results"][0]["success"] is True
    assert body["results"][0]["ticket_id"] == 100

    assign_world.expire_all()
    ticket = assign_world.get(Ticket, 100)
    assert ticket is not None
    assert ticket.assigned_user_id == 3


def test_assign_endpoint_role_not_allowed_422(
    app_client: TestClient, assign_world: Session
) -> None:
    resp = app_client.post(
        "/api/supervisor/assign",
        json={"ticket_ids": [101], "assigned_user_id": 2},  # bob is 'member'
        headers=_bearer(1),
    )
    assert resp.status_code == 422


def test_assign_endpoint_requires_supervisor(app_client: TestClient, assign_world: Session) -> None:
    resp = app_client.post(
        "/api/supervisor/assign",
        json={"ticket_ids": [102], "assigned_user_id": 3},
        headers=_bearer(2, role="member"),
    )
    assert resp.status_code == 403
