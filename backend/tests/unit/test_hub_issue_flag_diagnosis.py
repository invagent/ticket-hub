"""Tests for POST /api/hub-issues/{id}/flag-diagnosis（运营单 AI 自动答复有问题 →
送反思诊断）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import AgentDecision, HubIssue, Source, StatusHistory, Ticket, User


def _bearer(user_id: int, *, name: str = "carol", role: str = "supervisor") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(user_id), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def flag_world(db_session: Session) -> Session:
    """一条 KSM 运营单，op_status=answered，AI 自动答复过（reply_authored_by=
    agent:ai_cs），处理人是 member id=5。"""
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(User(id=2, feishu_uid="ou_carol", name="carol", role="supervisor"))
    db_session.add(User(id=5, feishu_uid="ou_dave", name="dave", role="member"))
    db_session.add(User(id=6, feishu_uid="ou_eve", name="eve", role="member"))
    db_session.add(
        HubIssue(
            id=90,
            short_code="HUB-000090",
            type="Operation",
            title="开票失败",
            status="created",
            op_status="answered",
            reply_authored_by="agent:ai_cs",
            reply_content="请检查网络后重试",
        )
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
            title="开票失败",
            hub_issue_id=90,
            handler_user_id=5,
        )
    )
    db_session.add(
        AgentDecision(
            decision_type="auto_reply",
            subject_type="hub_issue",
            subject_id=90,
            proposal={
                "branch": "D",
                "question": "发票云-开票：开票失败",
                "answer": "请检查网络后重试",
                "supply_note": "",
                "cited_knowledge": [{"type": "faq", "title": "开票失败排查", "score": 0.9}],
                "skills_used": ["customer-service"],
            },
        )
    )
    db_session.commit()
    return db_session


def test_flag_diagnosis_requires_handler_or_supervisor(
    app_client: TestClient, flag_world: Session
) -> None:
    # 非处理人 member → 403
    r = app_client.post(
        "/api/hub-issues/90/flag-diagnosis",
        json={"ticket_id": 300},
        headers=_bearer(6, name="eve", role="member"),
    )
    assert r.status_code == 403

    # 处理人本人 → 200
    r = app_client.post(
        "/api/hub-issues/90/flag-diagnosis",
        json={"ticket_id": 300},
        headers=_bearer(5, name="dave", role="member"),
    )
    assert r.status_code == 200, r.text


def test_flag_diagnosis_supervisor_can_flag(app_client: TestClient, flag_world: Session) -> None:
    r = app_client.post(
        "/api/hub-issues/90/flag-diagnosis", json={"ticket_id": 300}, headers=_bearer(2)
    )
    assert r.status_code == 200, r.text


def test_flag_diagnosis_rejects_non_operation_type(
    app_client: TestClient, flag_world: Session
) -> None:
    hub = flag_world.get(HubIssue, 90)
    hub.type = "Bug_fix"
    # Bug_fix 要求 Operation 专属字段清空（ck_hub_issues_operation_fields）
    hub.reply_content = None
    hub.reply_authored_by = None
    hub.op_status = None
    flag_world.commit()
    r = app_client.post(
        "/api/hub-issues/90/flag-diagnosis", json={"ticket_id": 300}, headers=_bearer(2)
    )
    assert r.status_code == 409
    assert "仅运营" in r.text


def test_flag_diagnosis_rejects_processing_status(
    app_client: TestClient, flag_world: Session
) -> None:
    hub = flag_world.get(HubIssue, 90)
    hub.op_status = "processing"
    flag_world.commit()
    r = app_client.post(
        "/api/hub-issues/90/flag-diagnosis", json={"ticket_id": 300}, headers=_bearer(2)
    )
    assert r.status_code == 409


def test_flag_diagnosis_rejects_closed_status(app_client: TestClient, flag_world: Session) -> None:
    """关键回归：已关闭的工单不可标记诊断（用户明确要求已关闭不需要支持）。"""
    hub = flag_world.get(HubIssue, 90)
    hub.op_status = "closed"
    flag_world.commit()
    r = app_client.post(
        "/api/hub-issues/90/flag-diagnosis", json={"ticket_id": 300}, headers=_bearer(2)
    )
    assert r.status_code == 409


def test_flag_diagnosis_rejects_human_reply(app_client: TestClient, flag_world: Session) -> None:
    """答复是人工发的/编辑过的（非 agent:ai_cs）→ 无需诊断。"""
    hub = flag_world.get(HubIssue, 90)
    hub.reply_authored_by = "user:carol"
    flag_world.commit()
    r = app_client.post(
        "/api/hub-issues/90/flag-diagnosis", json={"ticket_id": 300}, headers=_bearer(2)
    )
    assert r.status_code == 409
    assert "非 AI 自动答复" in r.text


def test_flag_diagnosis_happy_path_persists_golden_triple(
    app_client: TestClient, flag_world: Session
) -> None:
    r = app_client.post(
        "/api/hub-issues/90/flag-diagnosis",
        json={"ticket_id": 300, "note": "引用的知识条目已过期"},
        headers=_bearer(5, name="dave", role="member"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ticket_id"] == 300
    assert body["hub_issue_id"] == 90
    assert body["flagged_at"]

    ticket = flag_world.get(Ticket, 300)
    flag_world.refresh(ticket)
    assert ticket.diagnosis_flagged_at is not None
    ai = (ticket.source_payload or {}).get("ai_cs") or {}
    assert ai["original_question"] == "发票云-开票：开票失败"
    assert ai["ai_answer"] == "请检查网络后重试"
    assert ai["dissatisfaction"] == "引用的知识条目已过期"
    assert ai["cited_knowledge"] == [{"type": "faq", "title": "开票失败排查", "score": 0.9}]
    assert ai["skills_used"] == ["customer-service"]

    rows = [
        h
        for h in flag_world.query(StatusHistory)
        .filter(StatusHistory.entity_type == "ticket", StatusHistory.entity_id == 300)
        .all()
        if (h.metadata_ or {}).get("kind") == "diagnosis_flagged"
    ]
    assert len(rows) == 1
    assert rows[0].changed_by == "user:dave"


def test_flag_diagnosis_note_optional(app_client: TestClient, flag_world: Session) -> None:
    r = app_client.post(
        "/api/hub-issues/90/flag-diagnosis", json={"ticket_id": 300}, headers=_bearer(2)
    )
    assert r.status_code == 200, r.text
    ticket = flag_world.get(Ticket, 300)
    flag_world.refresh(ticket)
    ai = (ticket.source_payload or {}).get("ai_cs") or {}
    assert ai["dissatisfaction"] == ""


def test_flag_diagnosis_missing_auto_reply_decision_409(
    app_client: TestClient, flag_world: Session
) -> None:
    flag_world.query(AgentDecision).filter(AgentDecision.subject_id == 90).delete()
    flag_world.commit()
    r = app_client.post(
        "/api/hub-issues/90/flag-diagnosis", json={"ticket_id": 300}, headers=_bearer(2)
    )
    assert r.status_code == 409
    assert "找不到 AI 自动答复记录" in r.text


def test_flag_diagnosis_ticket_not_linked_to_hub_404(
    app_client: TestClient, flag_world: Session
) -> None:
    flag_world.add(
        Ticket(
            id=301,
            short_code="TKT-000301",
            source_code="ksm",
            source_ticket_id="rp-2",
            type="Raw",
            status="received",
            title="别的工单",
        )
    )
    flag_world.commit()
    r = app_client.post(
        "/api/hub-issues/90/flag-diagnosis", json={"ticket_id": 301}, headers=_bearer(2)
    )
    assert r.status_code == 404


def test_flag_diagnosis_already_diagnosed_409(app_client: TestClient, flag_world: Session) -> None:
    ticket = flag_world.get(Ticket, 300)
    ticket.source_payload = {"ai_cs": {"diagnosis": {"cause": "skill", "causes": ["skill"]}}}
    flag_world.commit()
    r = app_client.post(
        "/api/hub-issues/90/flag-diagnosis", json={"ticket_id": 300}, headers=_bearer(2)
    )
    assert r.status_code == 409


def test_flag_diagnosis_idempotent_refresh(app_client: TestClient, flag_world: Session) -> None:
    """未诊断状态下重复标记 → 都 200，幂等刷新黄金三元组。"""
    r1 = app_client.post(
        "/api/hub-issues/90/flag-diagnosis",
        json={"ticket_id": 300, "note": "第一次备注"},
        headers=_bearer(2),
    )
    assert r1.status_code == 200, r1.text
    r2 = app_client.post(
        "/api/hub-issues/90/flag-diagnosis",
        json={"ticket_id": 300, "note": "第二次备注"},
        headers=_bearer(2),
    )
    assert r2.status_code == 200, r2.text
    ticket = flag_world.get(Ticket, 300)
    flag_world.refresh(ticket)
    ai = (ticket.source_payload or {}).get("ai_cs") or {}
    assert ai["dissatisfaction"] == "第二次备注"
