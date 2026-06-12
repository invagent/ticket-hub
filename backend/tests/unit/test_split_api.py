"""Tests for /api/supervisor/execute-split + /revert-split endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import AgentDecision, Source, Ticket, User


def _bearer(user_id: int, *, name: str = "carol", role: str = "supervisor") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(user_id), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def split_world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(User(id=2, feishu_uid="ou_carol", name="carol", role="supervisor"))
    db_session.add(
        Ticket(
            id=100,
            short_code="TKT-000100",
            source_code="ksm",
            source_ticket_id="api-split-1",
            type="Raw",
            status="received",
            title="1、步骤咨询 2、状态不同步",
        )
    )
    db_session.flush()
    db_session.add(
        AgentDecision(
            id=500,
            decision_type="split_ticket",
            subject_type="ticket",
            subject_id=100,
            proposal={
                "decision": "split",
                "confidence": 0.7,
                "sub_issues": [
                    {"title": "步骤咨询", "summary": "a"},
                    {"title": "状态不同步", "summary": "b"},
                ],
            },
        )
    )
    db_session.commit()
    return db_session


def test_execute_split_requires_supervisor(app_client: TestClient, split_world: Session) -> None:
    resp = app_client.post(
        "/api/supervisor/execute-split",
        json={"decision_id": 500},
        headers=_bearer(1, name="bob", role="assignee"),
    )
    assert resp.status_code == 403


def test_execute_split_e2e(app_client: TestClient, split_world: Session) -> None:
    resp = app_client.post(
        "/api/supervisor/execute-split",
        json={"decision_id": 500},
        headers=_bearer(2),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["parent_ticket_id"] == 100
    assert len(body["child_ticket_ids"]) == 2

    parent = split_world.get(Ticket, 100)
    assert parent is not None
    split_world.refresh(parent)
    assert parent.type == "Parent"
    children = split_world.query(Ticket).filter_by(parent_ticket_id=100).all()
    assert {c.internal_split_id for c in children} == {"TKT-000100-C1", "TKT-000100-C2"}


def test_execute_split_conflict_returns_409(app_client: TestClient, split_world: Session) -> None:
    ok = app_client.post(
        "/api/supervisor/execute-split", json={"decision_id": 500}, headers=_bearer(2)
    )
    assert ok.status_code == 200
    dup = app_client.post(
        "/api/supervisor/execute-split", json={"decision_id": 500}, headers=_bearer(2)
    )
    assert dup.status_code == 409
    assert "expected Raw" in dup.json()["detail"]


def test_revert_split_e2e(app_client: TestClient, split_world: Session) -> None:
    app_client.post("/api/supervisor/execute-split", json={"decision_id": 500}, headers=_bearer(2))
    resp = app_client.post(
        "/api/supervisor/revert-split",
        json={"decision_id": 500, "reason": "拆错了"},
        headers=_bearer(2),
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["deleted_child_ids"]) == 2

    parent = split_world.get(Ticket, 100)
    assert parent is not None
    split_world.refresh(parent)
    assert parent.type == "Raw"
    d = split_world.get(AgentDecision, 500)
    assert d is not None
    split_world.refresh(d)
    assert d.status == "reverted"


def test_revert_unmaterialized_returns_409(app_client: TestClient, split_world: Session) -> None:
    resp = app_client.post(
        "/api/supervisor/revert-split",
        json={"decision_id": 500},
        headers=_bearer(2),
    )
    assert resp.status_code == 409
    assert "never materialized" in resp.json()["detail"]


# ---- proposals list + dismiss (supervisor work-bench queue) -----------------


def test_split_proposals_lists_pending(app_client: TestClient, split_world: Session) -> None:
    resp = app_client.get("/api/supervisor/split-proposals", headers=_bearer(2))
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    p = items[0]
    assert p["decision_id"] == 500
    assert p["ticket_short_code"] == "TKT-000100"
    assert p["confidence"] == 0.7
    assert [s["title"] for s in p["sub_issues"]] == ["步骤咨询", "状态不同步"]


def test_split_proposals_requires_supervisor(app_client: TestClient, split_world: Session) -> None:
    resp = app_client.get(
        "/api/supervisor/split-proposals", headers=_bearer(1, name="bob", role="assignee")
    )
    assert resp.status_code == 403


def test_split_proposals_excludes_materialized(
    app_client: TestClient, split_world: Session
) -> None:
    app_client.post("/api/supervisor/execute-split", json={"decision_id": 500}, headers=_bearer(2))
    resp = app_client.get("/api/supervisor/split-proposals", headers=_bearer(2))
    assert resp.json()["items"] == []


def test_dismiss_split_removes_from_queue(app_client: TestClient, split_world: Session) -> None:
    resp = app_client.post(
        "/api/supervisor/dismiss-split",
        json={"decision_id": 500, "reason": "其实是同一个问题"},
        headers=_bearer(2),
    )
    assert resp.status_code == 200, resp.text

    assert (
        app_client.get("/api/supervisor/split-proposals", headers=_bearer(2)).json()["items"] == []
    )
    d = split_world.get(AgentDecision, 500)
    assert d is not None
    split_world.refresh(d)
    assert d.status == "reverted"
    assert d.revert_reason == "其实是同一个问题"
    # 工单本身未被改动
    t = split_world.get(Ticket, 100)
    assert t is not None and t.type == "Raw"


def test_dismiss_materialized_returns_409(app_client: TestClient, split_world: Session) -> None:
    app_client.post("/api/supervisor/execute-split", json={"decision_id": 500}, headers=_bearer(2))
    resp = app_client.post(
        "/api/supervisor/dismiss-split", json={"decision_id": 500}, headers=_bearer(2)
    )
    assert resp.status_code == 409
    assert "use revert-split" in resp.json()["detail"]


def test_dismiss_then_execute_returns_409(app_client: TestClient, split_world: Session) -> None:
    app_client.post("/api/supervisor/dismiss-split", json={"decision_id": 500}, headers=_bearer(2))
    resp = app_client.post(
        "/api/supervisor/execute-split", json={"decision_id": 500}, headers=_bearer(2)
    )
    assert resp.status_code == 409
