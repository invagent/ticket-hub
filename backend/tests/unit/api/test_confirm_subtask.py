"""Tests for confirm_subtask_endpoint (Demand/Bug_fix Linear push validation)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import HubIssue, Ticket, User


def _bearer(uid: int, *, name: str = "carol", role: str = "supervisor") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(uid), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def subtask_world(db_session: Session) -> Session:
    db_session.add(User(id=2, feishu_uid="ou_c", name="carol", role="supervisor"))
    db_session.add(
        User(
            id=10,
            feishu_uid="ou_dev10",
            name="dev-ten",
            role="assignee",
            linear_user_id="lu-10",
            linear_team_id="team-10",
        )
    )
    t = Ticket(
        id=100,
        short_code="TKT-100",
        source_code="ksm",
        source_ticket_id="ksm-100",
        type="Raw",
        status="processing",
        title="测试工单",
        handler_user_id=2,
    )
    db_session.add(t)
    db_session.commit()
    yield db_session


def test_confirm_subtask_missing_solution_rejected(app_client: TestClient, subtask_world: Session) -> None:
    """Bug_fix 任务指派说明为空时，确认推送被拦截并返回 400。"""
    hub = HubIssue(
        ticket_id=100,
        short_code="HUB-SUB-1",
        type="Bug_fix",
        title="子任务1",
        canonical_body="子任务问题说明",
        reply_content="",  # 空指派说明
        assigned_user_id=10,
        status="draft",
    )
    subtask_world.add(hub)
    subtask_world.commit()

    resp = app_client.post(
        f"/api/hub-issues/{hub.id}/confirm-subtask",
        json={},
        headers=_bearer(2),
    )
    assert resp.status_code == 400
    assert "需求类或 Bug 类任务在推送前必须先录入指派说明" in resp.json()["detail"]


def test_confirm_subtask_missing_assignee_rejected(app_client: TestClient, subtask_world: Session) -> None:
    """Bug_fix 任务责任人（处理人）为空时，确认推送被拦截并返回 400。"""
    hub = HubIssue(
        ticket_id=100,
        short_code="HUB-SUB-2",
        type="Demand",
        title="需求任务2",
        canonical_body="需求说明",
        reply_content="指派研发排查处理",
        assigned_user_id=None,  # 未分配处理人
        status="draft",
    )
    subtask_world.add(hub)
    subtask_world.commit()

    resp = app_client.post(
        f"/api/hub-issues/{hub.id}/confirm-subtask",
        json={},
        headers=_bearer(2),
    )
    assert resp.status_code == 400
    assert "任务处理人为空，请先分配处理人后再进行确认推送" in resp.json()["detail"]


def test_confirm_subtask_success_pushes_linear(app_client: TestClient, subtask_world: Session) -> None:
    """指派说明与处理人齐备时，成功调用 push_hub_issue_to_linear 并更新为 processing。"""
    hub = HubIssue(
        ticket_id=100,
        short_code="HUB-SUB-3",
        type="Bug_fix",
        title="Bug任务3",
        canonical_body="详细问题",
        reply_content="已定位缺陷，需后端修复",
        assigned_user_id=10,
        status="draft",
    )
    subtask_world.add(hub)
    subtask_world.commit()

    mock_res = MagicMock()
    with patch("app.services.hub_issues.linear_push.push_hub_issue_to_linear", return_value=mock_res) as mock_push:
        resp = app_client.post(
            f"/api/hub-issues/{hub.id}/confirm-subtask",
            json={},
            headers=_bearer(2),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "processing"
        assert data["assigned_user_id"] == 10
        mock_push.assert_called_once()
        # 验证传入的 assignee_override_user_id 是任务处理人
        assert mock_push.call_args[1]["assignee_override_user_id"] == 10

    subtask_world.refresh(hub)
    assert hub.status == "processing"
    assert hub.assigned_user_id == 10
