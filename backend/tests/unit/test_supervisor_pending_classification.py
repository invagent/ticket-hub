"""Tests for GET /api/supervisor/pending-classification（待确认分类队列）."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import HubIssue, User


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
    # 不该出现在队列：Operation / created
    db_session.add(
        HubIssue(id=61, short_code="HUB-000061", type="Operation", title="配置咨询", status="created")
    )
    # 不该出现：pending_review 但已是 Operation（改判后遗留场景不在此队列）
    db_session.add(
        HubIssue(
            id=62, short_code="HUB-000062", type="Operation", title="x", status="pending_review"
        )
    )
    db_session.commit()
    return db_session


def test_pending_classification_requires_supervisor(
    app_client: TestClient, pc_world: Session
) -> None:
    r = app_client.get(
        "/api/supervisor/pending-classification",
        headers=_bearer(1, name="bob", role="member"),
    )
    assert r.status_code == 403


def test_pending_classification_lists_only_pending_review_devtype(
    app_client: TestClient, pc_world: Session
) -> None:
    r = app_client.get("/api/supervisor/pending-classification", headers=_bearer(2))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["short_code"] == "HUB-000060"
    assert items[0]["type"] == "Bug_fix"
