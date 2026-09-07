"""Unit tests for GET /api/metrics/daily-dashboard."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import User


def _auth_header(role: str = "supervisor") -> dict[str, str]:
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


def test_requires_supervisor(app_client: TestClient, world: Session) -> None:
    resp = app_client.get(
        "/api/metrics/daily-dashboard?date=2026-09-01",
        headers=_auth_header(role="member"),
    )
    assert resp.status_code == 403


def test_ok_for_supervisor(app_client: TestClient, world: Session) -> None:
    resp = app_client.get(
        "/api/metrics/daily-dashboard?date=2026-09-01",
        headers=_auth_header(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-09-01"
    assert set(body["totals"]) == {
        "received",
        "completed",
        "returned_to_ksm",
        "ksm_rejected",
        "supplemented",
    }
    assert set(body["lifetime"]) == {
        "total",
        "in_progress",
        "completed",
        "returned_to_ksm_total",
    }
    assert body["by_assignee"] == []


def test_invalid_date_format_rejected(app_client: TestClient, world: Session) -> None:
    resp = app_client.get(
        "/api/metrics/daily-dashboard?date=2026-9-1",
        headers=_auth_header(),
    )
    assert resp.status_code == 422


def test_missing_date_rejected(app_client: TestClient, world: Session) -> None:
    resp = app_client.get(
        "/api/metrics/daily-dashboard",
        headers=_auth_header(),
    )
    assert resp.status_code == 422
