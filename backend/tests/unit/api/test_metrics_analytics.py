"""Unit tests for GET /api/metrics/ticket-analytics."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import User


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
    db_session.commit()
    return db_session


def test_ticket_analytics_requires_supervisor(app_client: TestClient, world: Session) -> None:
    resp = app_client.get(
        "/api/metrics/ticket-analytics",
        headers=_auth_header(app_client, role="member"),
    )
    assert resp.status_code == 403


def test_ticket_analytics_ok_for_supervisor(app_client: TestClient, world: Session) -> None:
    resp = app_client.get(
        "/api/metrics/ticket-analytics",
        headers=_auth_header(app_client),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "kpi" in body and "total" in body["kpi"]
    assert "by_module" in body and "trend" in body
