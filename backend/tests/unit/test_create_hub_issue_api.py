"""Tests for POST /api/supervisor/create-hub-issue."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import HubIssue, Source, Ticket, User


def _bearer(user_id: int, *, name: str = "carol", role: str = "supervisor") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(user_id), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def hub_world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(User(id=2, feishu_uid="ou_carol", name="carol", role="supervisor"))
    db_session.add(
        Ticket(
            id=300,
            short_code="TKT-000300",
            source_code="ksm",
            source_ticket_id="chi-1",
            type="Raw",
            status="received",
            title="开票一直失败",
            body="报错截图见附件",
            predicted_type="Bug_fix",
        )
    )
    db_session.commit()
    return db_session


def test_requires_supervisor(app_client: TestClient, hub_world: Session) -> None:
    resp = app_client.post(
        "/api/supervisor/create-hub-issue",
        json={"ticket_id": 300},
        headers=_bearer(1, name="bob", role="assignee"),
    )
    assert resp.status_code == 403


def test_create_e2e(app_client: TestClient, hub_world: Session) -> None:
    resp = app_client.post(
        "/api/supervisor/create-hub-issue",
        json={"ticket_id": 300},
        headers=_bearer(2),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] is True
    assert body["type"] == "Bug_fix"
    assert body["hub_issue_short_code"].startswith("HUB-")

    t = hub_world.get(Ticket, 300)
    assert t is not None
    hub_world.refresh(t)
    assert t.hub_issue_id == body["hub_issue_id"]
    hub = hub_world.get(HubIssue, body["hub_issue_id"])
    assert hub is not None and hub.title == "开票一直失败"
    # LINEAR_PUSH_ENABLED 默认 false → 不会真推（linear_uuid 仍空）
    assert hub.linear_uuid is None


def test_create_with_type_override(app_client: TestClient, hub_world: Session) -> None:
    resp = app_client.post(
        "/api/supervisor/create-hub-issue",
        json={"ticket_id": 300, "type": "Demand"},
        headers=_bearer(2),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["type"] == "Demand"


def test_create_invalid_type_rejected(app_client: TestClient, hub_world: Session) -> None:
    resp = app_client.post(
        "/api/supervisor/create-hub-issue",
        json={"ticket_id": 300, "type": "NotAType"},
        headers=_bearer(2),
    )
    assert resp.status_code == 422


def test_create_twice_returns_existing(app_client: TestClient, hub_world: Session) -> None:
    first = app_client.post(
        "/api/supervisor/create-hub-issue", json={"ticket_id": 300}, headers=_bearer(2)
    ).json()
    again = app_client.post(
        "/api/supervisor/create-hub-issue", json={"ticket_id": 300}, headers=_bearer(2)
    )
    assert again.status_code == 200
    assert again.json()["created"] is False
    assert again.json()["hub_issue_id"] == first["hub_issue_id"]


def test_create_unclassified_returns_409(app_client: TestClient, hub_world: Session) -> None:
    hub_world.add(
        Ticket(
            id=301,
            short_code="TKT-000301",
            source_code="ksm",
            source_ticket_id="chi-2",
            type="Raw",
            status="received",
            title="未分类工单",
            predicted_type=None,
        )
    )
    hub_world.commit()
    resp = app_client.post(
        "/api/supervisor/create-hub-issue", json={"ticket_id": 301}, headers=_bearer(2)
    )
    assert resp.status_code == 409
    assert "no valid type" in resp.json()["detail"]
