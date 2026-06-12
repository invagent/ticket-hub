"""Tests for dedup proposal supervisor flow (D4 第①段):
GET /dedup-proposals + POST /execute-dedup + POST /dismiss-dedup
+ pending hub_issues queue / repush-linear."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import (
    AgentDecision,
    HubIssue,
    Source,
    StatusHistory,
    Ticket,
    TicketHubIssueHistory,
    User,
)


def _bearer(user_id: int, *, name: str = "carol", role: str = "supervisor") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(user_id), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def dedup_world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(User(id=2, feishu_uid="ou_carol", name="carol", role="supervisor"))
    db_session.add(
        HubIssue(
            id=70, short_code="HUB-000070", type="Bug_fix", title="全票池同步停滞", status="created"
        )
    )
    db_session.flush()
    db_session.add_all(
        [
            # 原始工单（已毕业 hub_issue 70）
            Ticket(
                id=200,
                short_code="TKT-000200",
                source_code="ksm",
                source_ticket_id="dd-orig",
                type="Raw",
                status="received",
                title="全票池没同步",
                hub_issue_id=70,
            ),
            # 新进的重复工单
            Ticket(
                id=201,
                short_code="TKT-000201",
                source_code="zhichi",
                source_ticket_id="dd-dup",
                type="Raw",
                status="received",
                title="进项发票没有同步进来",
            ),
        ]
    )
    db_session.add(Source(code="zhichi", name="智齿"))
    db_session.add(
        AgentDecision(
            id=600,
            decision_type="dedup_link",
            subject_type="ticket",
            subject_id=201,
            proposal={
                "decision": "duplicate",
                "duplicate_of_ticket_id": 200,
                "confidence": 0.9,
                "reason": "同一系统级故障",
                "candidates": [{"ticket_id": 200, "short_code": "TKT-000200", "similarity": 0.93}],
            },
        )
    )
    db_session.commit()
    return db_session


def test_dedup_proposals_lists_pending(app_client: TestClient, dedup_world: Session) -> None:
    resp = app_client.get("/api/supervisor/dedup-proposals", headers=_bearer(2))
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    p = items[0]
    assert p["decision_id"] == 600
    assert p["ticket_short_code"] == "TKT-000201"
    assert p["duplicate_of"]["short_code"] == "TKT-000200"
    assert p["duplicate_of"]["hub_issue_id"] == 70
    assert p["similarity"] == 0.93


def test_dedup_proposals_requires_supervisor(app_client: TestClient, dedup_world: Session) -> None:
    r = app_client.get(
        "/api/supervisor/dedup-proposals", headers=_bearer(1, name="bob", role="assignee")
    )
    assert r.status_code == 403


def test_execute_dedup_links_to_hub(app_client: TestClient, dedup_world: Session) -> None:
    resp = app_client.post(
        "/api/supervisor/execute-dedup", json={"decision_id": 600}, headers=_bearer(2)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["hub_issue_id"] == 70
    assert body["hub_issue_short_code"] == "HUB-000070"

    dup = dedup_world.get(Ticket, 201)
    dedup_world.refresh(dup)
    assert dup.hub_issue_id == 70
    hub = dedup_world.get(HubIssue, 70)
    dedup_world.refresh(hub)
    assert hub.occurrence_count == 2
    link = dedup_world.query(TicketHubIssueHistory).filter_by(ticket_id=201).one()
    assert link.human_confirmed is True
    # 队列里消失
    assert (
        app_client.get("/api/supervisor/dedup-proposals", headers=_bearer(2)).json()["items"] == []
    )
    # 再次执行 → 409
    again = app_client.post(
        "/api/supervisor/execute-dedup", json={"decision_id": 600}, headers=_bearer(2)
    )
    assert again.status_code == 409


def test_execute_dedup_target_without_hub_409(app_client: TestClient, dedup_world: Session) -> None:
    t = dedup_world.get(Ticket, 200)
    t.hub_issue_id = None
    dedup_world.commit()
    resp = app_client.post(
        "/api/supervisor/execute-dedup", json={"decision_id": 600}, headers=_bearer(2)
    )
    assert resp.status_code == 409
    assert "create-hub-issue" in resp.json()["detail"]


def test_dismiss_dedup(app_client: TestClient, dedup_world: Session) -> None:
    resp = app_client.post(
        "/api/supervisor/dismiss-dedup",
        json={"decision_id": 600, "reason": "数据要逐户核对，不算重复"},
        headers=_bearer(2),
    )
    assert resp.status_code == 200, resp.text
    d = dedup_world.get(AgentDecision, 600)
    dedup_world.refresh(d)
    assert d.status == "reverted"
    assert (
        app_client.get("/api/supervisor/dedup-proposals", headers=_bearer(2)).json()["items"] == []
    )


# ---- pending hub_issues + repush --------------------------------------------


@pytest.fixture
def pending_world(db_session: Session) -> Session:
    db_session.add(User(id=2, feishu_uid="ou_carol", name="carol", role="supervisor"))
    db_session.add(
        HubIssue(
            id=80, short_code="HUB-000080", type="Bug_fix", title="卡住的推送", status="pending"
        )
    )
    db_session.flush()
    db_session.add(
        StatusHistory(
            entity_type="hub_issue",
            entity_id=80,
            from_status="created",
            to_status="pending",
            changed_by="agent:linear_push",
            reason="处理人 王五（wangwu@kingdee.com）在 Linear 工作区查无此人",
        )
    )
    db_session.commit()
    return db_session


def test_pending_hub_issues_lists_with_reason(
    app_client: TestClient, pending_world: Session
) -> None:
    resp = app_client.get("/api/supervisor/pending-hub-issues", headers=_bearer(2))
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["short_code"] == "HUB-000080"
    assert "查无此人" in items[0]["pending_reason"]


def test_repush_still_blocked_returns_reason(
    app_client: TestClient, pending_world: Session
) -> None:
    """LINEAR_PUSH_ENABLED=false（conftest）→ 重推不成功，返回 pending 原因。"""
    resp = app_client.post(
        "/api/supervisor/repush-linear", json={"hub_issue_id": 80}, headers=_bearer(2)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pushed"] is False
    assert body["pending_reason"] is not None


def test_repush_already_pushed_409(app_client: TestClient, pending_world: Session) -> None:
    hub = pending_world.get(HubIssue, 80)
    hub.linear_uuid = "u"
    hub.linear_identifier = "CNPRD-1"
    pending_world.commit()
    resp = app_client.post(
        "/api/supervisor/repush-linear", json={"hub_issue_id": 80}, headers=_bearer(2)
    )
    assert resp.status_code == 409
    assert "CNPRD-1" in resp.json()["detail"]


def test_repush_missing_hub_404(app_client: TestClient, pending_world: Session) -> None:
    resp = app_client.post(
        "/api/supervisor/repush-linear", json={"hub_issue_id": 9999}, headers=_bearer(2)
    )
    assert resp.status_code == 404
