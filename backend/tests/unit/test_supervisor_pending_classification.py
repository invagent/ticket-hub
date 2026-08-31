"""Tests for GET /api/supervisor/pending-classification（待确认分类队列）."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import HubIssue, Ticket, User


def _bearer(uid: int, *, name: str = "carol", role: str = "supervisor") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(uid), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def pc_world(db_session: Session) -> Session:
    db_session.add(User(id=2, feishu_uid="ou_c", name="carol", role="supervisor"))
    db_session.add(
        HubIssue(
            id=60,
            short_code="HUB-000060",
            type="Bug_fix",
            title="app_token异常",
            canonical_body="初始化失败",
            status="pending_review",
        )
    )
    # 闸门①下 Operation 毕业也会停 pending_review——队列现在覆盖全类型，
    # 不再只挑 Bug_fix/Demand。
    db_session.add(
        HubIssue(
            id=61,
            short_code="HUB-000061",
            type="Operation",
            title="配置咨询",
            status="pending_review",
        )
    )
    # 不该出现：非 pending_review（已 created）
    db_session.add(
        HubIssue(id=62, short_code="HUB-000062", type="Operation", title="x", status="created")
    )
    # 覆盖 Internal_task 也进队列
    db_session.add(
        HubIssue(
            id=63,
            short_code="HUB-000063",
            type="Internal_task",
            title="内部任务",
            status="pending_review",
        )
    )
    db_session.commit()
    return db_session


def test_pending_classification_filters_to_own_handler(
    app_client: TestClient, pc_world: Session
) -> None:
    """非主管只看到处理人=自己的待确认分类；别人的不返回。"""
    # member 非处理人 → 空列表
    r = app_client.get(
        "/api/supervisor/pending-classification",
        headers=_bearer(1, name="bob", role="member"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["items"] == []

    # 给 hub 60 挂一条 handler=1 的 ticket → member id=1 看到 hub 60（而非 61/63）
    pc_world.add(
        Ticket(
            id=601,
            short_code="TKT-000601",
            source_code="ksm",
            source_ticket_id="k-601",
            type="Raw",
            status="received",
            title="t",
            hub_issue_id=60,
            handler_user_id=1,
        )
    )
    pc_world.commit()
    r = app_client.get(
        "/api/supervisor/pending-classification",
        headers=_bearer(1, name="bob", role="member"),
    )
    assert r.status_code == 200, r.text
    codes = {i["short_code"] for i in r.json()["items"]}
    assert codes == {"HUB-000060"}


def test_pending_classification_lists_all_types_pending_review(
    app_client: TestClient, pc_world: Session
) -> None:
    """闸门①覆盖全类型：队列不应再只挑 Bug_fix/Demand，Operation/Internal_task
    的 pending_review hub 也要出现；非 pending_review（created）不出现。"""
    r = app_client.get("/api/supervisor/pending-classification", headers=_bearer(2))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    codes = {i["short_code"] for i in items}
    assert codes == {"HUB-000060", "HUB-000061", "HUB-000063"}
