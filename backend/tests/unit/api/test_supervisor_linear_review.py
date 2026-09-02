"""闸门③：待推 Linear 队列 + confirm-linear-push 端点。"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import HubIssue, Module, ProductLine, StatusHistory, Ticket, User


def _bearer(uid: int, *, name: str = "carol", role: str = "supervisor") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(uid), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def review_world(db_session: Session) -> Session:
    db_session.add(User(id=2, feishu_uid="ou_c", name="carol", role="supervisor"))
    # 模块负责人：在 Linear 有映射
    db_session.add(
        User(
            id=40,
            feishu_uid="ou_owner",
            name="module-owner",
            role="assignee",
            linear_user_id="lu-owner",
            linear_team_id="team-owner",
        )
    )
    # 手选 assignee：也在 Linear 有映射
    db_session.add(
        User(
            id=41,
            feishu_uid="ou_manual",
            name="manual-pick",
            role="assignee",
            linear_user_id="lu-manual",
            linear_team_id="team-manual",
        )
    )
    db_session.flush()
    db_session.add(ProductLine(code="发票云", name="发票云"))
    db_session.add(
        Module(
            product_line_code="发票云",
            name="开票",
            dev_owners="module-owner",
        )
    )
    db_session.add(
        HubIssue(
            id=90,
            short_code="HUB-000090",
            type="Bug_fix",
            title="开票失败",
            canonical_body="b",
            status="pending_linear_review",
            product_line_code="发票云",
            module="开票",
        )
    )
    db_session.add(
        HubIssue(
            id=91,
            short_code="HUB-000091",
            type="Demand",
            title="需求",
            canonical_body="b",
            status="pending_linear_review",
            product_line_code="发票云",
            module="开票",
        )
    )
    # 非 pending_linear_review，不应出现在队列里
    db_session.add(
        HubIssue(
            id=92,
            short_code="HUB-000092",
            type="Bug_fix",
            title="已推送过",
            canonical_body="b",
            status="created",
        )
    )
    db_session.commit()
    return db_session


def test_list_pending_linear_review(app_client: TestClient, review_world: Session) -> None:
    r = app_client.get("/api/supervisor/pending-linear-review", headers=_bearer(2))
    assert r.status_code == 200, r.text
    data = r.json()
    ids = {item["hub_issue_id"] for item in data["items"]}
    assert ids == {90, 91}
    item90 = next(i for i in data["items"] if i["hub_issue_id"] == 90)
    assert item90["default_assignee_user_id"] == 40
    assert item90["default_assignee_name"] == "module-owner"
    assert item90["default_assignee_in_linear"] is True
    assert item90["product_line_code"] == "发票云"
    assert item90["module"] == "开票"


def test_list_pending_linear_review_preview_does_not_consume_rotation(
    app_client: TestClient, review_world: Session
) -> None:
    """展示预览用 peek，不消耗轮询名额——反复查看队列不应改变游标。"""
    for _ in range(3):
        r = app_client.get("/api/supervisor/pending-linear-review", headers=_bearer(2))
        assert r.status_code == 200, r.text
        item90 = next(i for i in r.json()["items"] if i["hub_issue_id"] == 90)
        assert item90["default_assignee_user_id"] == 40
    mod = review_world.query(Module).filter_by(product_line_code="发票云", name="开票").one()
    assert mod.dev_owner_rotation_cursor == 0


def test_list_pending_linear_review_filters_to_own_handler(
    app_client: TestClient, review_world: Session
) -> None:
    """非主管只看到处理人=自己的闸门；别人的不返回（行级可见性）。"""
    # member id=1 不是任何 pending hub 的处理人 → 空列表
    r = app_client.get(
        "/api/supervisor/pending-linear-review",
        headers=_bearer(1, name="bob", role="member"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["items"] == []

    # 给 hub 90 挂一条 handler=1 的 ticket → member id=1 能看到 hub 90（而非 91）
    review_world.add(
        Ticket(
            id=901,
            short_code="TKT-000901",
            source_code="ksm",
            source_ticket_id="k-901",
            type="Raw",
            status="received",
            title="t",
            hub_issue_id=90,
            handler_user_id=1,
        )
    )
    review_world.commit()
    r = app_client.get(
        "/api/supervisor/pending-linear-review",
        headers=_bearer(1, name="bob", role="member"),
    )
    assert r.status_code == 200, r.text
    ids = {item["hub_issue_id"] for item in r.json()["items"]}
    assert ids == {90}


def test_confirm_linear_push_defaults_to_module_owner(
    app_client: TestClient, review_world: Session
) -> None:
    with patch("app.api.supervisor.push_hub_issue_to_linear") as push:
        r = app_client.post(
            "/api/supervisor/confirm-linear-push",
            json={"hub_issue_id": 90},
            headers=_bearer(2),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hub_issue_id"] == 90
    assert body["status"] == "created"
    hub = review_world.get(HubIssue, 90)
    review_world.refresh(hub)
    assert hub.status == "created"
    assert hub.owner_user_id == 40  # 回落模块负责人（轮询选中）
    push.assert_called_once_with(90, assignee_override_user_id=40)
    sh = (
        review_world.query(StatusHistory)
        .filter_by(entity_type="hub_issue", entity_id=90, to_status="created")
        .one()
    )
    assert sh.changed_by == "user:carol"


def test_confirm_linear_push_with_explicit_assignee(
    app_client: TestClient, review_world: Session
) -> None:
    with patch("app.api.supervisor.push_hub_issue_to_linear") as push:
        r = app_client.post(
            "/api/supervisor/confirm-linear-push",
            json={"hub_issue_id": 91, "assignee_user_id": 41},
            headers=_bearer(2),
        )
    assert r.status_code == 200, r.text
    hub = review_world.get(HubIssue, 91)
    review_world.refresh(hub)
    assert hub.status == "created"
    assert hub.owner_user_id == 41  # 手选值，不调用 consume_module_owner
    push.assert_called_once_with(91, assignee_override_user_id=41)


def test_confirm_linear_push_explicit_assignee_skips_consume(
    app_client: TestClient, review_world: Session
) -> None:
    """手选 assignee_user_id 时不应调用 consume_module_owner（不消耗轮询游标）。"""
    with (
        patch("app.api.supervisor.push_hub_issue_to_linear"),
        patch("app.api.supervisor.consume_module_owner") as consume,
    ):
        r = app_client.post(
            "/api/supervisor/confirm-linear-push",
            json={"hub_issue_id": 91, "assignee_user_id": 41},
            headers=_bearer(2),
        )
    assert r.status_code == 200, r.text
    consume.assert_not_called()


def test_confirm_linear_push_rejects_non_pending(
    app_client: TestClient, review_world: Session
) -> None:
    r = app_client.post(
        "/api/supervisor/confirm-linear-push",
        json={"hub_issue_id": 92},
        headers=_bearer(2),
    )
    assert r.status_code == 409


def test_confirm_linear_push_by_own_handler(
    app_client: TestClient, review_world: Session
) -> None:
    """处理人本人可确认推送；非处理人的 member 仍 403。"""
    # 非处理人 member → 403
    r = app_client.post(
        "/api/supervisor/confirm-linear-push",
        json={"hub_issue_id": 90},
        headers=_bearer(1, name="bob", role="member"),
    )
    assert r.status_code == 403

    # 处理人本人（关联 ticket.handler_user_id=3）→ 200
    review_world.add(User(id=3, feishu_uid="ou_h", name="handler", role="member"))
    review_world.add(
        Ticket(
            id=902,
            short_code="TKT-000902",
            source_code="ksm",
            source_ticket_id="k-902",
            type="Raw",
            status="received",
            title="t",
            hub_issue_id=90,
            handler_user_id=3,
        )
    )
    review_world.commit()
    with patch("app.api.supervisor.push_hub_issue_to_linear") as push:
        r = app_client.post(
            "/api/supervisor/confirm-linear-push",
            json={"hub_issue_id": 90},
            headers=_bearer(3, name="handler", role="member"),
        )
    assert r.status_code == 200, r.text
    push.assert_called_once_with(90, assignee_override_user_id=40)
