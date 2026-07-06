"""投诉队列 supervisor API 测试（ADR-0016 P2d）：
GET /complaint-tickets + POST /close-complaint + 转型毕业出队。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import Source, StatusHistory, Ticket, User


def _bearer(user_id: int, *, name: str = "carol", role: str = "supervisor") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(user_id), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def complaint_world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(User(id=2, feishu_uid="ou_carol", name="carol", role="supervisor"))
    db_session.flush()
    db_session.add_all(
        [
            # 待人工的投诉
            Ticket(
                id=300,
                short_code="TKT-000300",
                source_code="ksm",
                source_ticket_id="cp-1",
                type="Raw",
                status="received",
                title="响应太慢要投诉",
                predicted_type="Complaint",
                predicted_confidence=0.9,
            ),
            # 已关闭的投诉——不该出现在队列
            Ticket(
                id=301,
                short_code="TKT-000301",
                source_code="ksm",
                source_ticket_id="cp-2",
                type="Raw",
                status="closed",
                title="旧投诉",
                predicted_type="Complaint",
            ),
            # 已转毕业的投诉（hub_issue_id 落值）——不该出现
            Ticket(
                id=302,
                short_code="TKT-000302",
                source_code="ksm",
                source_ticket_id="cp-3",
                type="Raw",
                status="linked",
                title="投诉裹着bug",
                predicted_type="Complaint",
                hub_issue_id=1,
            ),
            # 普通工单——不该出现
            Ticket(
                id=303,
                short_code="TKT-000303",
                source_code="ksm",
                source_ticket_id="cp-4",
                type="Raw",
                status="received",
                title="报错",
                predicted_type="Bug_fix",
            ),
        ]
    )
    db_session.commit()
    return db_session


def test_complaint_queue_lists_open_only(app_client: TestClient, complaint_world: Session) -> None:
    resp = app_client.get("/api/supervisor/complaint-tickets", headers=_bearer(2))
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert [i["ticket_id"] for i in items] == [300]
    assert items[0]["short_code"] == "TKT-000300"
    assert items[0]["confidence"] == 0.9


def test_complaint_queue_requires_supervisor(
    app_client: TestClient, complaint_world: Session
) -> None:
    r = app_client.get(
        "/api/supervisor/complaint-tickets", headers=_bearer(1, name="bob", role="assignee")
    )
    assert r.status_code == 403


def test_close_complaint(app_client: TestClient, complaint_world: Session) -> None:
    resp = app_client.post(
        "/api/supervisor/close-complaint",
        json={"ticket_id": 300, "reason": "纯情绪，已电话安抚"},
        headers=_bearer(2),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ticket_id": 300, "status": "closed"}
    t = complaint_world.get(Ticket, 300)
    complaint_world.refresh(t)
    assert t.status == "closed"
    hist = (
        complaint_world.query(StatusHistory)
        .filter_by(entity_type="ticket", entity_id=300, to_status="closed")
        .one()
    )
    assert hist.changed_by == "user:carol"
    assert hist.reason == "纯情绪，已电话安抚"
    # 关闭后离开队列
    items = app_client.get("/api/supervisor/complaint-tickets", headers=_bearer(2)).json()["items"]
    assert items == []


def test_close_complaint_rejects_non_complaint(
    app_client: TestClient, complaint_world: Session
) -> None:
    r = app_client.post(
        "/api/supervisor/close-complaint", json={"ticket_id": 303}, headers=_bearer(2)
    )
    assert r.status_code == 409
    assert "非投诉" in r.json()["detail"]


def test_close_complaint_rejects_terminal(app_client: TestClient, complaint_world: Session) -> None:
    r = app_client.post(
        "/api/supervisor/close-complaint", json={"ticket_id": 301}, headers=_bearer(2)
    )
    assert r.status_code == 409
    assert "终态" in r.json()["detail"]


def test_close_complaint_404(app_client: TestClient, complaint_world: Session) -> None:
    r = app_client.post(
        "/api/supervisor/close-complaint", json={"ticket_id": 9999}, headers=_bearer(2)
    )
    assert r.status_code == 404


def test_convert_complaint_graduates_and_leaves_queue(
    app_client: TestClient, complaint_world: Session
) -> None:
    """投诉裹着真问题 → create-hub-issue 带 type 覆盖 → hub_issue_id 落值出队。"""
    resp = app_client.post(
        "/api/supervisor/create-hub-issue",
        json={"ticket_id": 300, "type": "Bug_fix"},
        headers=_bearer(2),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["type"] == "Bug_fix"
    items = app_client.get("/api/supervisor/complaint-tickets", headers=_bearer(2)).json()["items"]
    assert items == []


def test_convert_complaint_without_type_rejected(
    app_client: TestClient, complaint_world: Session
) -> None:
    """不带 type 覆盖的投诉毕业被 creator 守卫拒绝（投诉停 ticket 层）。"""
    resp = app_client.post(
        "/api/supervisor/create-hub-issue",
        json={"ticket_id": 300},
        headers=_bearer(2),
    )
    assert resp.status_code == 409
    assert "投诉" in resp.json()["detail"]
