"""Admin /api/admin/holidays/* API 测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import User


def _bearer(uid: int, *, role: str = "admin") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(uid), name="boss", role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(User(id=1, feishu_uid="ou_admin", name="boss", role="admin"))
    db_session.commit()
    return db_session


def test_requires_admin(app_client: TestClient, world: Session) -> None:
    r = app_client.get("/api/admin/holidays", headers=_bearer(2, role="member"))
    assert r.status_code == 403


def test_upsert_list_delete(app_client: TestClient, world: Session) -> None:
    up = app_client.post(
        "/api/admin/holidays",
        json={
            "items": [
                {"holiday_date": "2026-10-01", "day_type": "holiday", "name": "国庆"},
                {"holiday_date": "2026-09-27", "day_type": "workday", "name": "调休"},
            ]
        },
        headers=_bearer(1),
    )
    assert up.status_code == 200 and up.json()["upserted"] == 2

    lst = app_client.get("/api/admin/holidays?year=2026", headers=_bearer(1)).json()
    dates = {h["holiday_date"] for h in lst}
    assert "2026-10-01" in dates and "2026-09-27" in dates

    # upsert 覆盖
    app_client.post(
        "/api/admin/holidays",
        json={"items": [{"holiday_date": "2026-10-01", "day_type": "workday"}]},
        headers=_bearer(1),
    )
    lst2 = app_client.get("/api/admin/holidays", headers=_bearer(1)).json()
    oct1 = next(h for h in lst2 if h["holiday_date"] == "2026-10-01")
    assert oct1["day_type"] == "workday"

    # delete
    d = app_client.delete("/api/admin/holidays/2026-09-27", headers=_bearer(1))
    assert d.status_code == 204
    assert (
        app_client.delete("/api/admin/holidays/2026-09-27", headers=_bearer(1)).status_code == 404
    )


def test_invalid_day_type_422(app_client: TestClient, world: Session) -> None:
    r = app_client.post(
        "/api/admin/holidays",
        json={"items": [{"holiday_date": "2026-10-01", "day_type": "nope"}]},
        headers=_bearer(1),
    )
    assert r.status_code == 422
