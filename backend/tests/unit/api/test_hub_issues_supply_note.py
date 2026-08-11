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
