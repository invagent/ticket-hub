"""Tests for GET /api/supervisor/reviewing-answers (D_review 主管审核队列)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import AgentDecision, HubIssue, User


def _bearer(uid: int, *, name: str = "carol", role: str = "supervisor") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(uid), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def rvw_world(db_session: Session) -> Session:
    db_session.add(User(id=2, feishu_uid="ou_c", name="carol", role="supervisor"))
    db_session.add(
        HubIssue(
            id=70,
            short_code="HUB-000070",
            type="Operation",
            title="开票失败",
            status="created",
            op_status="reviewing",
            op_handler="主管",
            reply_content="草稿答复",
            reply_is_draft=True,
        )
    )
    db_session.flush()
    db_session.add(
        AgentDecision(
            decision_type="auto_reply",
            subject_type="hub_issue",
            subject_id=70,
            proposal={
                "branch": "D_review",
                "accuracy": 60,
                "reason": "依据不足",
                "answer": "草稿答复",
            },
        )
    )
    db_session.commit()
    return db_session


def test_reviewing_requires_supervisor(app_client: TestClient, rvw_world: Session) -> None:
    r = app_client.get(
        "/api/supervisor/reviewing-answers",
        headers=_bearer(1, name="bob", role="member"),
    )
    assert r.status_code == 403


def test_reviewing_lists_with_accuracy(app_client: TestClient, rvw_world: Session) -> None:
    r = app_client.get("/api/supervisor/reviewing-answers", headers=_bearer(2))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    it = items[0]
    assert it["short_code"] == "HUB-000070"
    assert it["draft_reply"] == "草稿答复"
    assert it["accuracy"] == 60
    assert "依据不足" in (it["accuracy_reason"] or "")
