"""Unit tests for GET/PUT /api/admin/settings/default-pool-user."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import SystemSetting, User


def _auth_header(client: TestClient, role: str = "supervisor") -> dict[str, str]:
    from jose import jwt

    from app.config import get_settings

    token = jwt.encode(
        {"sub": "1", "name": "test", "role": role},
        get_settings().jwt_secret,
        algorithm=get_settings().jwt_algorithm,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(User(id=1, feishu_uid="ou_super", name="supervisor", role="supervisor"))
    db_session.add(User(id=2, feishu_uid="ou_pool", name="pool-user", role="assignee"))
    db_session.commit()
    return db_session


def test_get_returns_null_when_unset(app_client: TestClient, world: Session) -> None:
    resp = app_client.get(
        "/api/admin/settings/default-pool-user",
        headers=_auth_header(app_client),
    )
    assert resp.status_code == 200
    assert resp.json()["user_id"] is None


def test_put_sets_value(app_client: TestClient, world: Session) -> None:
    resp = app_client.put(
        "/api/admin/settings/default-pool-user",
        json={"user_id": 2},
        headers=_auth_header(app_client),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == 2
    assert data["user_name"] == "pool-user"


def test_get_returns_set_value(app_client: TestClient, world: Session) -> None:
    world.add(SystemSetting(key="default_pool_user_id", value="2", updated_by=1))
    world.commit()
    resp = app_client.get(
        "/api/admin/settings/default-pool-user",
        headers=_auth_header(app_client),
    )
    assert resp.status_code == 200
    assert resp.json()["user_id"] == 2
    assert resp.json()["user_name"] == "pool-user"


def test_put_invalid_user_returns_422(app_client: TestClient, world: Session) -> None:
    resp = app_client.put(
        "/api/admin/settings/default-pool-user",
        json={"user_id": 9999},
        headers=_auth_header(app_client),
    )
    assert resp.status_code == 422


def test_put_null_clears_value(app_client: TestClient, world: Session) -> None:
    world.add(SystemSetting(key="default_pool_user_id", value="2", updated_by=1))
    world.commit()
    resp = app_client.put(
        "/api/admin/settings/default-pool-user",
        json={"user_id": None},
        headers=_auth_header(app_client),
    )
    assert resp.status_code == 200
    assert resp.json()["user_id"] is None


def test_member_role_forbidden(app_client: TestClient, world: Session) -> None:
    resp = app_client.get(
        "/api/admin/settings/default-pool-user",
        headers=_auth_header(app_client, role="member"),
    )
    assert resp.status_code == 403
