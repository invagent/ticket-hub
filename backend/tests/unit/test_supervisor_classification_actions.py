"""Tests for 分类三动作端点 confirm / reclassify / dismiss."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import HubIssue, Ticket, User


def _bearer(uid: int, *, name: str = "carol", role: str = "supervisor") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(uid), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def act_world(db_session: Session) -> Session:
    db_session.add(User(id=2, feishu_uid="ou_c", name="carol", role="supervisor"))
    for hid, sc in [(70, "HUB-000070"), (71, "HUB-000071"), (72, "HUB-000072")]:
        db_session.add(
            HubIssue(
                id=hid,
                short_code=sc,
                type="Bug_fix",
                title="t",
                canonical_body="b",
                status="pending_review",
            )
        )
    db_session.flush()
    # hub 71 挂一条 ticket（predicted_type=Bug_fix）——验证改判会同步 ticket.predicted_type
    db_session.add(
        Ticket(
            id=710,
            short_code="TKT-000710",
            source_code="ksm",
            source_ticket_id="k-710",
            type="Raw",
            status="received",
            title="t",
            hub_issue_id=71,
            predicted_type="Bug_fix",
        )
    )
    db_session.commit()
    return db_session


def _action_rows(db: Session, ticket_id: int, action: str) -> list:
    from app.models import StatusHistory

    return [
        h
        for h in db.query(StatusHistory)
        .filter(StatusHistory.entity_type == "ticket", StatusHistory.entity_id == ticket_id)
        .all()
        if (h.metadata_ or {}).get("action") == action
    ]


def test_confirm_records_ticket_action(app_client: TestClient, act_world: Session) -> None:
    # hub 71 挂 ticket 710
    with patch("app.api.supervisor.push_hub_issue_to_linear"):
        r = app_client.post(
            "/api/supervisor/confirm-classification",
            json={"hub_issue_id": 71},
            headers=_bearer(2),
        )
    assert r.status_code == 200, r.text
    rows = _action_rows(act_world, 710, "confirm_classification")
    assert len(rows) == 1
    assert rows[0].changed_by == "user:carol"


def test_reclassify_records_ticket_action(app_client: TestClient, act_world: Session) -> None:
    r = app_client.post(
        "/api/supervisor/reclassify",
        json={"hub_issue_id": 71, "new_type": "Operation", "reason": "配置问题"},
        headers=_bearer(2),
    )
    assert r.status_code == 200, r.text
    rows = _action_rows(act_world, 710, "reclassify")
    assert len(rows) == 1
    assert rows[0].reason == "改判为 Operation"


def test_dismiss_records_ticket_action(app_client: TestClient, act_world: Session) -> None:
    r = app_client.post(
        "/api/supervisor/dismiss-classification",
        json={"hub_issue_id": 71, "reason": "误报"},
        headers=_bearer(2),
    )
    assert r.status_code == 200, r.text
    rows = _action_rows(act_world, 710, "dismiss_classification")
    assert len(rows) == 1


def test_confirm_pushes_linear(
    app_client: TestClient, act_world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """闸门③关（gate_linear_push_enabled=False）时保留旧行为：直推 Linear。"""
    monkeypatch.setenv("GATE_LINEAR_PUSH_ENABLED", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        with patch("app.api.supervisor.push_hub_issue_to_linear") as push:
            r = app_client.post(
                "/api/supervisor/confirm-classification",
                json={"hub_issue_id": 70},
                headers=_bearer(2),
            )
        assert r.status_code == 200, r.text
        hub = act_world.get(HubIssue, 70)
        act_world.refresh(hub)
        assert hub.status == "created"
        push.assert_called_once_with(70)
    finally:
        get_settings.cache_clear()


def test_reclassify_to_operation_enters_answer_chain(
    app_client: TestClient, act_world: Session
) -> None:
    r = app_client.post(
        "/api/supervisor/reclassify",
        json={"hub_issue_id": 71, "new_type": "Operation", "reason": "配置问题"},
        headers=_bearer(2),
    )
    assert r.status_code == 200, r.text
    hub = act_world.get(HubIssue, 71)
    act_world.refresh(hub)
    assert hub.type == "Operation"
    assert hub.status == "created"
    assert hub.op_status == "processing"
    assert hub.op_handler == "agent"  # 下轮 drain 会扫到
    # 关联 ticket 的 predicted_type 也同步改判（否则工单列表 AI 分类列仍显示旧类型）
    tk = act_world.get(Ticket, 710)
    act_world.refresh(tk)
    assert tk.predicted_type == "Operation"


def test_reclassify_to_operation_runs_dispatch(app_client: TestClient, act_world: Session) -> None:
    """改判进 Operation 要走分派引擎(与自动/手动毕业一致),写 op_handler_user_id;
    否则该 hub 永远拿不到预分配运营,转人工只能走兜底。"""
    from app.models import DispatchAssignee, DispatchRule, User

    act_world.add(User(id=7, feishu_uid="ou_op7", name="op7", role="assignee"))
    rule = DispatchRule(
        name="all",
        match_sources=[],
        match_product_lines=[],
        match_modules=[],
        match_sla=[],
        dispatch_mode="count",
        rule_type="primary",
        priority=100,
        is_active=True,
    )
    act_world.add(rule)
    act_world.flush()
    act_world.add(
        DispatchAssignee(rule_id=rule.id, user_id=7, daily_cap=20, tier="main", is_active=True)
    )
    act_world.commit()

    r = app_client.post(
        "/api/supervisor/reclassify",
        json={"hub_issue_id": 71, "new_type": "Operation", "reason": "配置问题"},
        headers=_bearer(2),
    )
    assert r.status_code == 200, r.text
    hub = act_world.get(HubIssue, 71)
    act_world.refresh(hub)
    assert hub.op_handler_user_id == 7  # 分派引擎预分配了运营处理人


def test_dismiss_closes(app_client: TestClient, act_world: Session) -> None:
    r = app_client.post(
        "/api/supervisor/dismiss-classification",
        json={"hub_issue_id": 72, "reason": "误报"},
        headers=_bearer(2),
    )
    assert r.status_code == 200, r.text
    hub = act_world.get(HubIssue, 72)
    act_world.refresh(hub)
    assert hub.status == "closed"


def test_actions_require_supervisor(app_client: TestClient, act_world: Session) -> None:
    r = app_client.post(
        "/api/supervisor/confirm-classification",
        json={"hub_issue_id": 70},
        headers=_bearer(1, name="bob", role="member"),
    )
    assert r.status_code == 403


def test_action_rejects_non_pending_review(app_client: TestClient, act_world: Session) -> None:
    """已 created 的 hub 不可再走确认（防重复推）。"""
    hub = act_world.get(HubIssue, 70)
    hub.status = "created"
    act_world.commit()
    r = app_client.post(
        "/api/supervisor/confirm-classification",
        json={"hub_issue_id": 70},
        headers=_bearer(2),
    )
    assert r.status_code == 409


# ---- Task 5: confirm-classification / reclassify 按类型分流 -----------------


@pytest.fixture
def type_world(db_session: Session) -> Session:
    """闸门①下全类型都会停 pending_review——confirm/reclassify 需按 hub.type 分流。"""
    db_session.add(User(id=2, feishu_uid="ou_c", name="carol", role="supervisor"))
    db_session.add(
        HubIssue(
            id=80,
            short_code="HUB-000080",
            type="Operation",
            title="配置咨询",
            canonical_body="b",
            status="pending_review",
        )
    )
    db_session.add(
        HubIssue(
            id=81,
            short_code="HUB-000081",
            type="Bug_fix",
            title="报错",
            canonical_body="b",
            status="pending_review",
        )
    )
    db_session.add(
        HubIssue(
            id=82,
            short_code="HUB-000082",
            type="Internal_task",
            title="内部任务",
            canonical_body="b",
            status="pending_review",
        )
    )
    db_session.add(
        HubIssue(
            id=83,
            short_code="HUB-000083",
            type="Demand",
            title="需求",
            canonical_body="b",
            status="pending_review",
        )
    )
    db_session.commit()
    return db_session


def test_confirm_operation_enters_answer_chain(app_client: TestClient, type_world: Session) -> None:
    r = app_client.post(
        "/api/supervisor/confirm-classification",
        json={"hub_issue_id": 80},
        headers=_bearer(2),
    )
    assert r.status_code == 200, r.text
    hub = type_world.get(HubIssue, 80)
    type_world.refresh(hub)
    assert hub.status == "created"
    assert hub.op_status == "processing"
    assert hub.op_handler == "agent"


def test_confirm_bugfix_gate_on_goes_pending_linear_review(
    app_client: TestClient, type_world: Session
) -> None:
    with patch("app.api.supervisor.push_hub_issue_to_linear") as push:
        r = app_client.post(
            "/api/supervisor/confirm-classification",
            json={"hub_issue_id": 81},
            headers=_bearer(2),
        )
    assert r.status_code == 200, r.text
    hub = type_world.get(HubIssue, 81)
    type_world.refresh(hub)
    assert hub.status == "pending_linear_review"
    assert hub.linear_uuid is None
    push.assert_not_called()


def test_confirm_demand_gate_on_goes_pending_linear_review(
    app_client: TestClient, type_world: Session
) -> None:
    with patch("app.api.supervisor.push_hub_issue_to_linear") as push:
        r = app_client.post(
            "/api/supervisor/confirm-classification",
            json={"hub_issue_id": 83},
            headers=_bearer(2),
        )
    assert r.status_code == 200, r.text
    hub = type_world.get(HubIssue, 83)
    type_world.refresh(hub)
    assert hub.status == "pending_linear_review"
    push.assert_not_called()


def test_confirm_bugfix_gate_off_pushes_and_created(
    app_client: TestClient, type_world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GATE_LINEAR_PUSH_ENABLED", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        with patch("app.api.supervisor.push_hub_issue_to_linear") as push:
            r = app_client.post(
                "/api/supervisor/confirm-classification",
                json={"hub_issue_id": 81},
                headers=_bearer(2),
            )
        assert r.status_code == 200, r.text
        hub = type_world.get(HubIssue, 81)
        type_world.refresh(hub)
        assert hub.status == "created"
        push.assert_called_once_with(81)
    finally:
        get_settings.cache_clear()


def test_confirm_internal_task_created_no_external_action(
    app_client: TestClient, type_world: Session
) -> None:
    with patch("app.api.supervisor.push_hub_issue_to_linear") as push:
        r = app_client.post(
            "/api/supervisor/confirm-classification",
            json={"hub_issue_id": 82},
            headers=_bearer(2),
        )
    assert r.status_code == 200, r.text
    hub = type_world.get(HubIssue, 82)
    type_world.refresh(hub)
    assert hub.status == "created"
    push.assert_not_called()


def test_reclassify_to_bugfix_gate_on_goes_pending_linear_review(
    app_client: TestClient, type_world: Session
) -> None:
    """改判成 Bug_fix/Demand 时也要经过闸门③（不是回到 pending_review 而是直接
    pending_linear_review，因为主管这次改判本身已经是"确认分类"的动作）。"""
    with patch("app.api.supervisor.push_hub_issue_to_linear") as push:
        r = app_client.post(
            "/api/supervisor/reclassify",
            json={"hub_issue_id": 82, "new_type": "Bug_fix", "reason": "其实是研发类"},
            headers=_bearer(2),
        )
    assert r.status_code == 200, r.text
    hub = type_world.get(HubIssue, 82)
    type_world.refresh(hub)
    assert hub.type == "Bug_fix"
    assert hub.status == "pending_linear_review"
    push.assert_not_called()


def test_reclassify_to_demand_gate_off_pushes(
    app_client: TestClient, type_world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GATE_LINEAR_PUSH_ENABLED", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        with patch("app.api.supervisor.push_hub_issue_to_linear") as push:
            r = app_client.post(
                "/api/supervisor/reclassify",
                json={"hub_issue_id": 82, "new_type": "Demand", "reason": "其实是需求"},
                headers=_bearer(2),
            )
        assert r.status_code == 200, r.text
        hub = type_world.get(HubIssue, 82)
        type_world.refresh(hub)
        assert hub.type == "Demand"
        assert hub.status == "created"
        push.assert_called_once_with(82)
    finally:
        get_settings.cache_clear()
