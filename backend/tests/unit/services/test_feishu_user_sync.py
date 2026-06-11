"""Feishu user sync service tests — D2-E.

Mocks Feishu contact API (tenant_access_token + users/find_by_department +
users/{open_id}) via respx; exercises both upsert paths.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx
from sqlalchemy.orm import Session

from adapters.feishu import FeishuClient, FeishuConfig
from app.api.auth import issue_jwt
from app.models import User
from app.services.users.feishu_sync import FeishuUserSyncService

BASE = "https://open.feishu.cn"


def _bearer(uid: int, *, role: str = "admin") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(uid), name="admin", role=role)
    return {"Authorization": f"Bearer {token}"}


def _stub_token(rsps: respx.MockRouter) -> None:
    rsps.post(f"{BASE}/open-apis/auth/v3/tenant_access_token/internal").mock(
        return_value=httpx.Response(200, json={"tenant_access_token": "tok-x"})
    )


def _stub_dept_users(rsps: respx.MockRouter, users: list[dict]) -> None:
    rsps.get(f"{BASE}/open-apis/contact/v3/users/find_by_department").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "msg": "ok",
                "data": {"items": users, "has_more": False},
            },
        )
    )


def _client() -> FeishuClient:
    return FeishuClient(
        FeishuConfig(app_id="test-app", app_secret="test-secret"),
        http_client=httpx.Client(timeout=5.0),
    )


# ---- service-level tests --------------------------------------------------


@respx.mock
def test_sync_dept_creates_new_users(db_session: Session) -> None:
    _stub_token(respx)
    _stub_dept_users(
        respx,
        [
            {
                "open_id": "ou_a",
                "name": "Alice",
                "email": "alice@kingdee.com",
                "mobile": "+8613800138000",
                "employee_no": "K0030",
                "status": {"is_activated": True},
            },
            {
                "open_id": "ou_b",
                "name": "Bob",
                "email": "bob@kingdee.com",
                "status": {"is_activated": True},
            },
        ],
    )
    client = _client()
    try:
        svc = FeishuUserSyncService(db_session, client=client)
        report = svc.sync_from_department("0")
    finally:
        client.close()
    db_session.commit()

    assert report.new_count == 2
    assert report.updated_count == 0
    assert report.errors == []
    rows = db_session.query(User).order_by(User.id).all()
    assert {r.feishu_uid for r in rows} == {"ou_a", "ou_b"}
    alice = next(r for r in rows if r.feishu_uid == "ou_a")
    assert alice.mobile == "13800138000"  # +86 stripped
    assert alice.employee_no == "K0030"


@respx.mock
def test_sync_dept_updates_existing_without_touching_role(db_session: Session) -> None:
    db_session.add(
        User(feishu_uid="ou_a", name="Old Name", role="admin", email="old@example.com")
    )
    db_session.commit()
    _stub_token(respx)
    _stub_dept_users(
        respx,
        [
            {
                "open_id": "ou_a",
                "name": "Alice (renamed)",
                "email": "new@kingdee.com",
                "status": {"is_activated": True},
            }
        ],
    )
    client = _client()
    try:
        report = FeishuUserSyncService(db_session, client=client).sync_from_department("0")
    finally:
        client.close()
    db_session.commit()

    assert report.new_count == 0
    assert report.updated_count == 1
    row = db_session.query(User).filter_by(feishu_uid="ou_a").one()
    assert row.name == "Alice (renamed)"
    assert row.email == "new@kingdee.com"
    assert row.role == "admin"  # NOT clobbered


@respx.mock
def test_sync_dept_revives_soft_deleted(db_session: Session) -> None:
    db_session.add(
        User(
            feishu_uid="ou_a",
            name="returnee",
            role="member",
            is_active=False,
            deleted_at=datetime.now(UTC),
        )
    )
    db_session.commit()
    _stub_token(respx)
    _stub_dept_users(
        respx,
        [{"open_id": "ou_a", "name": "returnee", "status": {"is_activated": True}}],
    )
    client = _client()
    try:
        report = FeishuUserSyncService(db_session, client=client).sync_from_department("0")
    finally:
        client.close()
    db_session.commit()

    assert report.revived_count == 1
    row = db_session.query(User).filter_by(feishu_uid="ou_a").one()
    assert row.deleted_at is None
    assert row.is_active is True


@respx.mock
def test_sync_dept_skips_inactive_new_user(db_session: Session) -> None:
    _stub_token(respx)
    _stub_dept_users(
        respx,
        [{"open_id": "ou_x", "name": "deactivated", "status": {"is_activated": False}}],
    )
    client = _client()
    try:
        report = FeishuUserSyncService(db_session, client=client).sync_from_department("0")
    finally:
        client.close()
    db_session.commit()

    assert report.skipped_inactive == 1
    assert report.new_count == 0
    assert db_session.query(User).count() == 0


@respx.mock
def test_sync_open_ids(db_session: Session) -> None:
    _stub_token(respx)
    respx.get(f"{BASE}/open-apis/contact/v3/users/ou_a").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "user": {
                        "open_id": "ou_a",
                        "name": "Alice",
                        "email": "a@k.com",
                        "status": {"is_activated": True},
                    }
                },
            },
        )
    )
    respx.get(f"{BASE}/open-apis/contact/v3/users/ou_missing").mock(
        return_value=httpx.Response(200, json={"code": 230002, "msg": "not found"})
    )
    client = _client()
    try:
        report = FeishuUserSyncService(db_session, client=client).sync_from_open_ids(
            ["ou_a", "ou_missing"]
        )
    finally:
        client.close()
    db_session.commit()
    assert report.new_count == 1
    assert len(report.errors) == 1
    assert report.errors[0]["open_id"] == "ou_missing"


# ---- API endpoint -----------------------------------------------------


@respx.mock
def test_sync_endpoint_admin_only(app_client) -> None:
    """Without admin token → 401/403."""
    r = app_client.post("/api/admin/users/sync-from-feishu", json={"department_id": "0"})
    assert r.status_code == 401


@respx.mock
def test_sync_endpoint_validates_payload(app_client, db_session) -> None:
    db_session.add(User(id=1, feishu_uid="ou_admin", name="admin", role="admin"))
    db_session.commit()
    # Both fields → 400
    r = app_client.post(
        "/api/admin/users/sync-from-feishu",
        headers=_bearer(1),
        json={"department_id": "0", "open_ids": ["ou_x"]},
    )
    assert r.status_code == 400
    # Neither field → 400
    r2 = app_client.post(
        "/api/admin/users/sync-from-feishu",
        headers=_bearer(1),
        json={},
    )
    assert r2.status_code == 400


@respx.mock
def test_sync_endpoint_happy_path(app_client, db_session) -> None:
    db_session.add(User(id=1, feishu_uid="ou_admin", name="admin", role="admin"))
    db_session.commit()
    _stub_token(respx)
    _stub_dept_users(
        respx,
        [
            {"open_id": "ou_n1", "name": "newbie1", "status": {"is_activated": True}},
            {"open_id": "ou_n2", "name": "newbie2", "status": {"is_activated": True}},
        ],
    )
    r = app_client.post(
        "/api/admin/users/sync-from-feishu",
        headers=_bearer(1),
        json={"department_id": "0"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["new_count"] == 2
    assert body["errors"] == []
    assert len(body["new_user_ids"]) == 2


# ---- org-tree browse endpoints --------------------------------------


@respx.mock
def test_browse_departments_returns_children(app_client, db_session) -> None:
    db_session.add(User(id=1, feishu_uid="ou_admin", name="admin", role="admin"))
    db_session.commit()
    _stub_token(respx)
    respx.get(f"{BASE}/open-apis/contact/v3/departments/0/children").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "items": [
                        {
                            "open_department_id": "od_1",
                            "department_id": "1",
                            "name": "技术中心",
                            "parent_department_id": "0",
                            "member_count": 50,
                        },
                        {
                            "open_department_id": "od_2",
                            "department_id": "2",
                            "name": "市场部",
                            "parent_department_id": "0",
                            "member_count": 30,
                        },
                    ],
                    "has_more": False,
                },
            },
        )
    )
    r = app_client.get(
        "/api/admin/users/feishu/departments?parent_id=0",
        headers=_bearer(1),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 2
    assert body[0]["name"] == "技术中心"
    assert body[0]["member_count"] == 50


@respx.mock
def test_browse_dept_users_marks_already_synced(app_client, db_session) -> None:
    db_session.add_all(
        [
            User(id=1, feishu_uid="ou_admin", name="admin", role="admin"),
            User(id=5, feishu_uid="ou_existing", name="existing user", role="member"),
        ]
    )
    db_session.commit()
    _stub_token(respx)
    _stub_dept_users(
        respx,
        [
            {"open_id": "ou_existing", "name": "existing user", "status": {"is_activated": True}},
            {"open_id": "ou_new_user", "name": "new person", "status": {"is_activated": True}},
        ],
    )
    r = app_client.get(
        "/api/admin/users/feishu/departments/od_x/users",
        headers=_bearer(1),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    by_id = {u["open_id"]: u for u in body}
    assert by_id["ou_existing"]["already_synced"] is True
    assert by_id["ou_existing"]["local_user_id"] == 5
    assert by_id["ou_new_user"]["already_synced"] is False
    assert by_id["ou_new_user"]["local_user_id"] is None


def test_browse_endpoints_require_admin(app_client) -> None:
    assert app_client.get("/api/admin/users/feishu/departments").status_code == 401
    assert (
        app_client.get("/api/admin/users/feishu/departments/od_1/users").status_code == 401
    )
