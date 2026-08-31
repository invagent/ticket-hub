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
    # 改判 reason 写中文类型（时间轴人性化，不再显示英文枚举 Operation）
    assert rows[0].reason == "改判为 运营"


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
    """模块负责人确定 → 直推 Linear（status=created）。"""
    monkeypatch.setattr(
        "app.api.supervisor.resolve_module_owner",
        lambda *a, **k: act_world.get(User, 2),
    )
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


def test_confirm_bugfix_owner_resolved_pushes_and_created(
    app_client: TestClient, type_world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.api.supervisor.resolve_module_owner",
        lambda *a, **k: type_world.get(User, 2),
    )
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


def test_reclassify_to_demand_owner_resolved_pushes(
    app_client: TestClient, type_world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.api.supervisor.resolve_module_owner",
        lambda *a, **k: type_world.get(User, 2),
    )
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


def test_confirm_by_handler_allowed(app_client: TestClient, act_world: Session) -> None:
    """确认推送放宽到处理人本人：hub 的 op_handler_user_id==当前用户 → 放行。"""
    from app.models import HubIssue, User

    act_world.add(User(id=7, feishu_uid="ou_h7", name="handler7", role="assignee"))
    act_world.add(
        HubIssue(
            id=75,
            short_code="HUB-000075",
            type="Bug_fix",
            title="t",
            canonical_body="b",
            status="pending_review",
            op_handler_user_id=7,
        )
    )
    act_world.commit()
    with patch("app.api.supervisor.push_hub_issue_to_linear"):
        r = app_client.post(
            "/api/supervisor/confirm-classification",
            json={"hub_issue_id": 75},
            headers=_bearer(7, name="handler7", role="assignee"),
        )
    assert r.status_code == 200, r.text


def test_confirm_by_stranger_403(app_client: TestClient, act_world: Session) -> None:
    """路人（非处理人非主管）确认推送 → 403。"""
    from app.models import HubIssue, User

    act_world.add(User(id=8, feishu_uid="ou_s8", name="stranger8", role="member"))
    act_world.add(
        HubIssue(
            id=76,
            short_code="HUB-000076",
            type="Bug_fix",
            title="t",
            canonical_body="b",
            status="pending_review",
            op_handler_user_id=7,
        )
    )
    act_world.commit()
    r = app_client.post(
        "/api/supervisor/confirm-classification",
        json={"hub_issue_id": 76},
        headers=_bearer(8, name="stranger8", role="member"),
    )
    assert r.status_code == 403, r.text


# ---- 处理中 Operation 转研发类并直推 Linear（2026-08-20）----------------------


@pytest.fixture
def op_world(db_session: Session) -> Session:
    """一个处理中(op_status=processing)的已确认 Operation hub + 关联 ticket。"""
    db_session.add(User(id=2, feishu_uid="ou_c", name="carol", role="supervisor"))
    db_session.add(
        HubIssue(
            id=80,
            short_code="HUB-000080",
            type="Operation",
            title="t",
            canonical_body="b",
            status="created",
            op_status="processing",
            op_handler="agent",
        )
    )
    db_session.flush()
    db_session.add(
        Ticket(
            id=800,
            short_code="TKT-000800",
            source_code="ksm",
            source_ticket_id="k-800",
            type="Raw",
            status="received",
            title="t",
            hub_issue_id=80,
            predicted_type="Operation",
        )
    )
    db_session.commit()
    return db_session


def test_processing_operation_reclassify_to_dev_pushes_linear(
    app_client: TestClient, op_world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """处理中 Operation 改判 Demand + 模块负责人确定：直推 Linear、op 字段清空、type 翻转。"""
    monkeypatch.setattr(
        "app.api.supervisor.resolve_module_owner",
        lambda *a, **k: op_world.get(User, 2),
    )
    with patch("app.api.supervisor.push_hub_issue_to_linear") as push:
        r = app_client.post(
            "/api/supervisor/reclassify",
            json={"hub_issue_id": 80, "new_type": "Demand", "reason": "客户沟通后确认是需求"},
            headers=_bearer(2),
        )
    assert r.status_code == 200, r.text
    hub = op_world.get(HubIssue, 80)
    op_world.refresh(hub)
    assert hub.type == "Demand"
    assert hub.status == "created"  # 责任人确定 → 直推，不是 pending_linear_review
    # Operation 专属字段清空（满足 ck_hub_issues_operation_fields + 契约）
    assert hub.op_status is None
    assert hub.op_handler is None
    assert hub.op_handler_user_id is None
    assert hub.reply_content is None
    push.assert_called_once_with(80)
    # 关联 ticket 的 predicted_type 同步
    tk = op_world.get(Ticket, 800)
    op_world.refresh(tk)
    assert tk.predicted_type == "Demand"


def test_answered_operation_cannot_reclassify(app_client: TestClient, op_world: Session) -> None:
    """已答复(answered)Operation = 处理完成，不可转研发（409）。"""
    hub = op_world.get(HubIssue, 80)
    hub.op_status = "answered"
    op_world.commit()
    r = app_client.post(
        "/api/supervisor/reclassify",
        json={"hub_issue_id": 80, "new_type": "Bug_fix", "reason": "x"},
        headers=_bearer(2),
    )
    assert r.status_code == 409, r.text
    assert "不可改判" in r.text


def test_reviewing_operation_can_reclassify_to_dev(
    app_client: TestClient, op_world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """草稿待审(op_status=reviewing)的 Operation——AI 判错类型——可改判转研发，草稿
    连同 op 专属字段一并清空（草稿本就没发出去，直接清掉即可）。"""
    hub = op_world.get(HubIssue, 80)
    hub.op_status = "reviewing"
    hub.reply_content = "AI 生成的答复草稿"
    hub.reply_authored_by = "agent:ai_cs:draft"
    op_world.commit()
    monkeypatch.setattr(
        "app.api.supervisor.resolve_module_owner",
        lambda *a, **k: op_world.get(User, 2),
    )
    with patch("app.api.supervisor.push_hub_issue_to_linear") as push:
        r = app_client.post(
            "/api/supervisor/reclassify",
            json={"hub_issue_id": 80, "new_type": "Bug_fix", "reason": "AI 误判为运营类"},
            headers=_bearer(2),
        )
    assert r.status_code == 200, r.text
    op_world.expire_all()
    hub = op_world.get(HubIssue, 80)
    assert hub.type == "Bug_fix"
    assert hub.op_status is None
    assert hub.op_handler is None
    assert hub.reply_content is None
    assert hub.reply_authored_by is None
    push.assert_called_once_with(80)


def test_processing_operation_reclassify_by_own_handler(
    app_client: TestClient, op_world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """处理人本人（member）也能转研发：授权对齐 PATCH /attributes 的口径。"""
    op_world.add(User(id=9, feishu_uid="ou_m", name="mia", role="member"))
    hub = op_world.get(HubIssue, 80)
    hub.op_handler_user_id = 9
    op_world.commit()
    monkeypatch.setattr(
        "app.api.supervisor.resolve_module_owner",
        lambda *a, **k: op_world.get(User, 2),
    )
    with patch("app.api.supervisor.push_hub_issue_to_linear") as push:
        r = app_client.post(
            "/api/supervisor/reclassify",
            json={"hub_issue_id": 80, "new_type": "Bug_fix", "reason": "跟客户确认是 bug"},
            headers=_bearer(9, role="member"),
        )
    assert r.status_code == 200, r.text
    op_world.refresh(hub)
    assert hub.type == "Bug_fix"
    assert hub.status == "created"  # 责任人确定 → 直推
    assert hub.op_handler_user_id is None  # Operation→研发清运营字段
    push.assert_called_once_with(80)


def test_reclassify_forbidden_for_non_handler_member(
    app_client: TestClient, op_world: Session
) -> None:
    """非处理人的 member 无权转研发（403）。"""
    op_world.add(User(id=10, feishu_uid="ou_n", name="nina", role="member"))
    op_world.commit()
    r = app_client.post(
        "/api/supervisor/reclassify",
        json={"hub_issue_id": 80, "new_type": "Bug_fix", "reason": "x"},
        headers=_bearer(10, role="member"),
    )
    assert r.status_code == 403, r.text
