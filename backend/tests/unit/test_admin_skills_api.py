"""Admin /api/admin/skills/* API 测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import User


def _bearer(uid: int, *, name: str = "boss", role: str = "admin") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(uid), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(User(id=1, feishu_uid="ou_admin", name="boss", role="admin"))
    db_session.commit()
    return db_session


def test_requires_admin(app_client: TestClient, world: Session) -> None:
    r = app_client.get("/api/admin/skills", headers=_bearer(2, name="m", role="member"))
    assert r.status_code == 403


def test_import_then_list_get_edit_history_rollback(app_client: TestClient, world: Session) -> None:
    # import
    imp = app_client.post("/api/admin/skills/import-from-files", headers=_bearer(1))
    assert imp.status_code == 200 and imp.json()["added"] >= 5

    # list
    lst = app_client.get("/api/admin/skills", headers=_bearer(1)).json()
    names = {s["name"] for s in lst}
    assert "classify_v2" in names and "dedup_v1" in names

    # get
    detail = app_client.get("/api/admin/skills/dedup_v1", headers=_bearer(1)).json()
    assert detail["version"] == 1 and detail["content_md"]

    # edit → v2
    e = app_client.put(
        "/api/admin/skills/dedup_v1",
        json={"content_md": "新版去重提示词", "reason": "调优"},
        headers=_bearer(1),
    )
    assert e.status_code == 200 and e.json()["version"] == 2

    # history has v2, v1
    hist = app_client.get("/api/admin/skills/dedup_v1/history", headers=_bearer(1)).json()
    assert [h["version"] for h in hist] == [2, 1]

    # rollback to v1 → v3
    rb = app_client.post(
        "/api/admin/skills/dedup_v1/rollback", json={"version": 1}, headers=_bearer(1)
    )
    assert rb.status_code == 200 and rb.json()["version"] == 3


def test_get_missing_404(app_client: TestClient, world: Session) -> None:
    app_client.post("/api/admin/skills/import-from-files", headers=_bearer(1))
    r = app_client.get("/api/admin/skills/nope_xyz", headers=_bearer(1))
    assert r.status_code == 404


def test_edit_missing_409(app_client: TestClient, world: Session) -> None:
    r = app_client.put("/api/admin/skills/nope_xyz", json={"content_md": "x"}, headers=_bearer(1))
    assert r.status_code == 409


def test_edit_empty_422(app_client: TestClient, world: Session) -> None:
    app_client.post("/api/admin/skills/import-from-files", headers=_bearer(1))
    r = app_client.put("/api/admin/skills/dedup_v1", json={"content_md": ""}, headers=_bearer(1))
    assert r.status_code == 422
