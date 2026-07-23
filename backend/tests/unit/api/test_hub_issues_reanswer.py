"""Tests for POST /api/hub-issues/{id}/re-answer (Task 8, 人工重答 API)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from adapters.ai_cs.types import ReplayResult
from app.api.auth import issue_jwt
from app.models import HubIssue, Source, Ticket


def _bearer(user_id: int, *, name: str = "carol", role: str = "supervisor") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(user_id), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


class _FakeClient:
    def __init__(self, answer: str = "") -> None:
        self._answer = answer

    def replay(self, **kw: object) -> ReplayResult:
        return ReplayResult(answer=self._answer, cited_knowledge=[], skills_used=[], trace_id="t1")

    def close(self) -> None:
        pass


@pytest.fixture
def reanswer_world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(
        HubIssue(
            id=90,
            short_code="HUB-000090",
            type="Operation",
            title="开票失败",
            canonical_body="开票时提示网络错误",
            status="created",
            op_status="processing",
            op_handler="主管",  # 人工介入中
        )
    )
    db_session.add(
        HubIssue(
            id=91,
            short_code="HUB-000091",
            type="Operation",
            title="尚未处理",
            status="created",
            op_status="processing",
            op_handler="agent",  # 刚毕业，agent 还没跑过一轮——非人工介入
        )
    )
    db_session.add(
        HubIssue(id=92, short_code="HUB-000092", type="Bug_fix", title="bug", status="created")
    )
    db_session.add(
        HubIssue(
            id=93,
            short_code="HUB-000093",
            type="Operation",
            title="replay 故障",
            canonical_body="replay 系统故障后卡死",
            status="created",
            op_status="exception",
            op_handler="主管",  # 处理异常，主管修完系统故障后应能重答
        )
    )
    db_session.add(
        HubIssue(
            id=94,
            short_code="HUB-000094",
            type="Operation",
            title="异常+agent(防御)",
            status="created",
            op_status="exception",
            op_handler="agent",  # 理论不该出现：exception 但 handler 仍是 agent
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
            title="x",
            hub_issue_id=90,
        )
    )
    db_session.commit()
    return db_session


def test_re_answer_requires_knowledge_op_or_above(
    app_client: TestClient, reanswer_world: Session
) -> None:
    r = app_client.post(
        "/api/hub-issues/90/re-answer",
        headers=_bearer(1, name="bob", role="member"),
    )
    assert r.status_code == 403


def test_re_answer_success_marks_answered(app_client: TestClient, reanswer_world: Session) -> None:
    from app.services.agents.operation_answer import AnswerRoute

    fake = _FakeClient(answer="您好，请在【发票管理】重新发起开票。")
    with (
        patch("app.services.agents.operation_answer.build_client", return_value=fake),
        patch(
            "app.services.agents.operation_answer._route_answer",
            return_value=AnswerRoute(branch="D"),
        ),
    ):
        r = app_client.post("/api/hub-issues/90/re-answer", headers=_bearer(2))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answered"] is True
    assert body["op_status"] == "answered"

    hub = reanswer_world.get(HubIssue, 90)
    reanswer_world.refresh(hub)
    assert hub.op_status == "answered"
    assert hub.reply_content_version == 1


def test_re_answer_leaves_processing_when_transfer(
    app_client: TestClient, reanswer_world: Session
) -> None:
    from app.services.agents.operation_answer import AnswerRoute

    fake = _FakeClient(answer="无法回答")
    with (
        patch("app.services.agents.operation_answer.build_client", return_value=fake),
        patch(
            "app.services.agents.operation_answer._route_answer",
            return_value=AnswerRoute(branch="transfer"),
        ),
    ):
        r = app_client.post("/api/hub-issues/90/re-answer", headers=_bearer(2))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answered"] is False
    assert body["op_status"] == "processing"

    hub = reanswer_world.get(HubIssue, 90)
    reanswer_world.refresh(hub)
    assert hub.op_status == "processing"
    assert hub.op_handler != "agent"


def test_re_answer_not_human_intervening_409(
    app_client: TestClient, reanswer_world: Session
) -> None:
    """op_handler='agent' 代表刚毕业尚未处理过（非人工介入中）——重答被拒。"""
    r = app_client.post("/api/hub-issues/91/re-answer", headers=_bearer(2))
    assert r.status_code == 409
    assert "人工介入中" in r.json()["detail"]


def test_re_answer_non_operation_409(app_client: TestClient, reanswer_world: Session) -> None:
    r = app_client.post("/api/hub-issues/92/re-answer", headers=_bearer(2))
    assert r.status_code == 409
    assert "Operation-only" in r.json()["detail"]


def test_re_answer_exception_status_recoverable(
    app_client: TestClient, reanswer_world: Session
) -> None:
    """op_status=exception + handler=主管 → 重答可执行（不 409），成功后落 answered。

    exception 是 replay 系统故障时置的转人工态；主管修完系统故障后应能点重答把
    工单拉回处理流程，不该永久卡死（无 drain 扫描，也无法重答 → 死胡同）。
    """
    from app.services.agents.operation_answer import AnswerRoute

    fake = _FakeClient(answer="您好，系统已恢复，请重新尝试提交。")
    with (
        patch("app.services.agents.operation_answer.build_client", return_value=fake),
        patch(
            "app.services.agents.operation_answer._route_answer",
            return_value=AnswerRoute(branch="D"),
        ),
    ):
        r = app_client.post("/api/hub-issues/93/re-answer", headers=_bearer(2))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answered"] is True
    assert body["op_status"] == "answered"

    hub = reanswer_world.get(HubIssue, 93)
    reanswer_world.refresh(hub)
    assert hub.op_status == "answered"


def test_re_answer_exception_with_agent_handler_still_409(
    app_client: TestClient, reanswer_world: Session
) -> None:
    """exception + op_handler='agent'（防御性场景，理论不该出现）→ 仍 409。

    handler=='agent' 一律拒绝，不因 op_status 是 exception 而放行。
    """
    r = app_client.post("/api/hub-issues/94/re-answer", headers=_bearer(2))
    assert r.status_code == 409


def test_re_answer_missing_hub_409(app_client: TestClient, reanswer_world: Session) -> None:
    r = app_client.post("/api/hub-issues/9999/re-answer", headers=_bearer(2))
    assert r.status_code == 409


def test_re_answer_ignores_auto_reply_disabled_flag(
    app_client: TestClient, reanswer_world: Session
) -> None:
    """force=True 跳过 operation_auto_reply_enabled 总开关——人工重答不该被自动答复总闸拦住。"""
    from app.services.agents.operation_answer import AnswerRoute

    fake = _FakeClient(answer="您好，请在【发票管理】重新发起开票。")
    from app.config import get_settings

    real_settings = get_settings()
    with (
        patch("app.services.agents.operation_answer.get_settings") as mock_get_settings,
        patch("app.services.agents.operation_answer.build_client", return_value=fake),
        patch(
            "app.services.agents.operation_answer._route_answer",
            return_value=AnswerRoute(branch="D"),
        ),
    ):
        disabled = real_settings.model_copy(update={"operation_auto_reply_enabled": False})
        mock_get_settings.return_value = disabled
        r = app_client.post("/api/hub-issues/90/re-answer", headers=_bearer(2))
    assert r.status_code == 200, r.text
    assert r.json()["answered"] is True
