"""POST /api/supervisor/drain-zhichi-writeback 端点测试。

鉴权门 + 默认 disabled 空转路径（不触网）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import Source, User


def _bearer(user_id: int, *, role: str = "supervisor") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(user_id), name="carol", role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="zhichi", name="智齿"))
    db_session.add(User(id=2, feishu_uid="ou_carol", name="carol", role="supervisor"))
    db_session.commit()
    return db_session


def test_requires_supervisor(app_client: TestClient, world: Session) -> None:
    r = app_client.post(
        "/api/supervisor/drain-zhichi-writeback", headers=_bearer(3, role="member")
    )
    assert r.status_code == 403


def test_disabled_default_returns_empty(app_client: TestClient, world: Session) -> None:
    r = app_client.post("/api/supervisor/drain-zhichi-writeback", headers=_bearer(2))
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["scanned"] == 0
