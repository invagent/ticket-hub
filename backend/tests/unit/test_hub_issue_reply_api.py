"""Tests for POST /api/hub-issues/{id}/reply (D4 第②段)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import HubIssue, Source, SyncOutbox, Ticket, User


def _bearer(user_id: int, *, name: str = "carol", role: str = "supervisor") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(user_id), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def reply_world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(User(id=2, feishu_uid="ou_carol", name="carol", role="supervisor"))
    db_session.add(
        HubIssue(
            id=90, short_code="HUB-000090", type="Operation", title="怎么开红字", status="created"
        )
    )
    db_session.add(
        HubIssue(id=91, short_code="HUB-000091", type="Bug_fix", title="bug", status="created")
    )
    db_session.flush()
    db_session.add(
        Ticket(
            id=300,
            short_code="TKT-000300",
            source_code="ksm",
            source_ticket_id="rp-1",
            type="Raw",
            status="received",
            title="x",
            hub_issue_id=90,
        )
    )
    db_session.commit()
    return db_session


def test_reply_requires_supervisor(app_client: TestClient, reply_world: Session) -> None:
    r = app_client.post(
        "/api/hub-issues/90/reply",
        json={"content": "回复"},
        headers=_bearer(1, name="bob", role="member"),
    )
    assert r.status_code == 403


def test_reply_e2e(app_client: TestClient, reply_world: Session) -> None:
    r = app_client.post(
        "/api/hub-issues/90/reply",
        json={"content": "请在发票云-红字确认单中操作"},
        headers=_bearer(2),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 1
    assert body["cascaded_ticket_count"] == 1
    assert body["outbox_count"] == 1

    hub = reply_world.get(HubIssue, 90)
    reply_world.refresh(hub)
    assert hub.reply_content == "请在发票云-红字确认单中操作"
    t = reply_world.get(Ticket, 300)
    reply_world.refresh(t)
    assert t.cached_reply_version == 1
    assert reply_world.query(SyncOutbox).filter_by(kind="reply").count() == 1

    # detail 返回回复内容
    detail = app_client.get("/api/hub-issues/90", headers=_bearer(2)).json()
    assert detail["reply_content"] == "请在发票云-红字确认单中操作"
    assert detail["reply_content_version"] == 1


def test_reply_on_bugfix_409(app_client: TestClient, reply_world: Session) -> None:
    r = app_client.post("/api/hub-issues/91/reply", json={"content": "x"}, headers=_bearer(2))
    assert r.status_code == 409
    assert "Operation-only" in r.json()["detail"]


def test_reply_missing_hub_409(app_client: TestClient, reply_world: Session) -> None:
    r = app_client.post("/api/hub-issues/9999/reply", json={"content": "x"}, headers=_bearer(2))
    assert r.status_code == 409


def test_reply_empty_422(app_client: TestClient, reply_world: Session) -> None:
    r = app_client.post("/api/hub-issues/90/reply", json={"content": ""}, headers=_bearer(2))
    assert r.status_code == 422


# ---- request-supply (补料) ---------------------------------------------------


def test_request_supply_requires_supervisor(app_client: TestClient, reply_world: Session) -> None:
    r = app_client.post(
        "/api/hub-issues/90/request-supply",
        json={"note": "请补充截图"},
        headers=_bearer(1, name="bob", role="member"),
    )
    assert r.status_code == 403


def test_request_supply_e2e(app_client: TestClient, reply_world: Session) -> None:
    r = app_client.post(
        "/api/hub-issues/90/request-supply",
        json={"note": "请提供完整报错截图与操作步骤"},
        headers=_bearer(2),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ticket_count"] == 1 and body["outbox_count"] == 1
    rows = reply_world.query(SyncOutbox).filter_by(kind="supply").all()
    assert len(rows) == 1 and rows[0].target_source_code == "ksm"
    assert rows[0].payload["supply_note"] == "请提供完整报错截图与操作步骤"


def test_request_supply_missing_hub_409(app_client: TestClient, reply_world: Session) -> None:
    r = app_client.post(
        "/api/hub-issues/9999/request-supply", json={"note": "x"}, headers=_bearer(2)
    )
    assert r.status_code == 409


def test_request_supply_empty_422(app_client: TestClient, reply_world: Session) -> None:
    r = app_client.post("/api/hub-issues/90/request-supply", json={"note": ""}, headers=_bearer(2))
    assert r.status_code == 422
