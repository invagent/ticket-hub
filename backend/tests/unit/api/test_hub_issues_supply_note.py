"""Tests for HubIssueDetail.supply_note (补料清单暴露, 2026-08-11).

supply_note 只在 op_status=supplementing 时从最新 auto_reply agent_decision 的
proposal.supply_note 回填；其它状态返回 None。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import AgentDecision, HubIssue


def _bearer(user_id: int = 1, *, name: str = "carol", role: str = "supervisor") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(user_id), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def supply_world(db_session: Session) -> Session:
    # 95: 补料态,带 auto_reply decision(含 supply_note)
    db_session.add(
        HubIssue(
            id=95,
            short_code="HUB-000095",
            type="Operation",
            title="需补料",
            canonical_body="信息不足",
            status="created",
            op_status="supplementing",
        )
    )
    # 96: 已答复(非补料态),即便有 decision 也不回填
    db_session.add(
        HubIssue(
            id=96,
            short_code="HUB-000096",
            type="Operation",
            title="已答复",
            status="created",
            op_status="answered",
        )
    )
    db_session.flush()
    db_session.add(
        AgentDecision(
            decision_type="auto_reply",
            subject_type="hub_issue",
            subject_id=95,
            proposal={"branch": "C", "supply_note": "请提供:1) 报错截图 2) 操作步骤 3) 单据编号"},
        )
    )
    db_session.add(
        AgentDecision(
            decision_type="auto_reply",
            subject_type="hub_issue",
            subject_id=96,
            proposal={"branch": "C", "supply_note": "不应返回"},
        )
    )
    db_session.commit()
    return db_session


def test_supply_note_exposed_when_supplementing(
    app_client: TestClient, supply_world: Session
) -> None:
    r = app_client.get("/api/hub-issues/95", headers=_bearer())
    assert r.status_code == 200, r.text
    assert r.json()["supply_note"] == "请提供:1) 报错截图 2) 操作步骤 3) 单据编号"


def test_supply_note_none_when_not_supplementing(
    app_client: TestClient, supply_world: Session
) -> None:
    r = app_client.get("/api/hub-issues/96", headers=_bearer())
    assert r.status_code == 200, r.text
    assert r.json()["supply_note"] is None


def test_supply_note_none_when_no_decision(app_client: TestClient, db_session: Session) -> None:
    db_session.add(
        HubIssue(
            id=97,
            short_code="HUB-000097",
            type="Operation",
            title="补料但无decision",
            status="created",
            op_status="supplementing",
        )
    )
    db_session.commit()
    r = app_client.get("/api/hub-issues/97", headers=_bearer())
    assert r.status_code == 200, r.text
    assert r.json()["supply_note"] is None


def test_detail_exposes_reply_is_draft(app_client: TestClient, db_session: Session) -> None:
    """详情响应暴露 reply_is_draft，前端据此区分「AI 草稿待处理」与「已发答复」。"""
    db_session.add(
        HubIssue(
            id=98,
            short_code="HUB-000098",
            type="Operation",
            title="需补料草稿",
            canonical_body="信息不足",
            status="created",
            op_status="processing",
            reply_content="请提供完整报错截图",
            reply_is_draft=True,
        )
    )
    db_session.commit()
    r = app_client.get("/api/hub-issues/98", headers=_bearer())
    assert r.status_code == 200, r.text
    assert r.json()["reply_is_draft"] is True
    assert r.json()["reply_content"] == "请提供完整报错截图"


def test_last_transfer_attempt_exposed_when_processing_and_latest_is_transfer(
    app_client: TestClient, db_session: Session
) -> None:
    """processing 态 + 最新 auto_reply 判 transfer → 回填已尝试问答（只读展示）。"""
    db_session.add(
        HubIssue(
            id=100,
            short_code="HUB-000100",
            type="Operation",
            title="AI答不上来",
            status="created",
            op_status="processing",
        )
    )
    db_session.flush()
    db_session.add(
        AgentDecision(
            decision_type="auto_reply",
            subject_type="hub_issue",
            subject_id=100,
            proposal={
                "branch": "transfer",
                "question": "发票云-开票：客户反馈的问题",
                "answer": "抱歉，未在知识库中找到相关答案",
                "supply_note": "",
            },
        )
    )
    db_session.commit()
    r = app_client.get("/api/hub-issues/100", headers=_bearer())
    assert r.status_code == 200, r.text
    attempt = r.json()["last_transfer_attempt"]
    assert attempt == {
        "question": "发票云-开票：客户反馈的问题",
        "answer": "抱歉，未在知识库中找到相关答案",
    }


def test_last_transfer_attempt_none_when_latest_decision_not_transfer(
    app_client: TestClient, db_session: Session
) -> None:
    """processing 态但最新 decision 是 C（补料）分支 → 不回填 transfer 提示。"""
    db_session.add(
        HubIssue(
            id=101,
            short_code="HUB-000101",
            type="Operation",
            title="需补料",
            status="created",
            op_status="processing",
        )
    )
    db_session.flush()
    db_session.add(
        AgentDecision(
            decision_type="auto_reply",
            subject_type="hub_issue",
            subject_id=101,
            proposal={"branch": "C", "supply_note": "请提供截图"},
        )
    )
    db_session.commit()
    r = app_client.get("/api/hub-issues/101", headers=_bearer())
    assert r.status_code == 200, r.text
    assert r.json()["last_transfer_attempt"] is None


def test_last_transfer_attempt_none_when_not_processing(
    app_client: TestClient, db_session: Session
) -> None:
    """即便有 transfer decision，非 processing 态（如已答复）也不回填。"""
    db_session.add(
        HubIssue(
            id=102,
            short_code="HUB-000102",
            type="Operation",
            title="已答复",
            status="created",
            op_status="answered",
        )
    )
    db_session.flush()
    db_session.add(
        AgentDecision(
            decision_type="auto_reply",
            subject_type="hub_issue",
            subject_id=102,
            proposal={"branch": "transfer", "question": "q", "answer": "a", "supply_note": ""},
        )
    )
    db_session.commit()
    r = app_client.get("/api/hub-issues/102", headers=_bearer())
    assert r.status_code == 200, r.text
    assert r.json()["last_transfer_attempt"] is None


def test_request_supply_rejected_on_closed(app_client: TestClient, db_session: Session) -> None:
    """已关闭（closed）Operation hub 不允许再补料 → 409。"""
    db_session.add(
        HubIssue(
            id=99,
            short_code="HUB-000099",
            type="Operation",
            title="已关闭",
            status="created",
            op_status="closed",
        )
    )
    db_session.commit()
    r = app_client.post(
        "/api/hub-issues/99/request-supply",
        json={"note": "请补充"},
        headers=_bearer(),
    )
    assert r.status_code == 409, r.text
