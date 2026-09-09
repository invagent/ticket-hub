"""ADR-0017 D4：/api/admin/tenants（require_admin）。"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import ProductLine, Tenant, User


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add_all(
        [
            User(id=1, feishu_uid="ou_admin", name="admin", role="admin"),
            User(id=2, feishu_uid="ou_sup", name="sup", role="supervisor"),
            ProductLine(code="cloud-fapiao", name="金蝶发票云"),
        ]
    )
    db_session.commit()
    return db_session


def _b(uid: int, role: str) -> dict[str, str]:
    token, _ = issue_jwt(sub=str(uid), name="x", role=role)
    return {"Authorization": f"Bearer {token}"}


def test_crud_and_secret_only_once(app_client, world: Session) -> None:
    r = app_client.post(
        "/api/admin/tenants",
        json={"code": "acme", "name": "Acme", "default_product_line_code": "cloud-fapiao"},
        headers=_b(1, "admin"),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body["hmac_secret"]) == 64
    tid = body["id"]

    lst = app_client.get("/api/admin/tenants", headers=_b(1, "admin")).json()
    assert lst[0]["code"] == "acme" and "hmac_secret" not in lst[0]
    assert lst[0]["user_count"] == 0 and lst[0]["ticket_count"] == 0

    dup = app_client.post(
        "/api/admin/tenants", json={"code": "acme", "name": "dup"}, headers=_b(1, "admin")
    )
    assert dup.status_code == 409

    p = app_client.patch(
        f"/api/admin/tenants/{tid}",
        json={"is_active": False, "name": "Acme2"},
        headers=_b(1, "admin"),
    )
    assert p.status_code == 200 and p.json()["is_active"] is False and p.json()["name"] == "Acme2"

    rot = app_client.post(f"/api/admin/tenants/{tid}/rotate-secret", headers=_b(1, "admin"))
    assert rot.status_code == 200 and rot.json()["hmac_secret"] != body["hmac_secret"]
    assert world.get(Tenant, tid).hmac_secret == rot.json()["hmac_secret"]  # type: ignore[union-attr]

    assert app_client.get(f"/api/admin/tenants/{tid}/users", headers=_b(1, "admin")).json() == []


def test_requires_admin(app_client, world: Session) -> None:
    assert app_client.get("/api/admin/tenants", headers=_b(2, "supervisor")).status_code == 403
    assert (
        app_client.post(
            "/api/admin/tenants", json={"code": "x1", "name": "x"}, headers=_b(2, "supervisor")
        ).status_code
        == 403
    )


def test_code_pattern_and_pl_check(app_client, world: Session) -> None:
    assert (
        app_client.post(
            "/api/admin/tenants", json={"code": "Bad Code", "name": "x"}, headers=_b(1, "admin")
        ).status_code
        == 422
    )
    r = app_client.post(
        "/api/admin/tenants",
        json={"code": "ok-1", "name": "x", "default_product_line_code": "nope"},
        headers=_b(1, "admin"),
    )
    assert r.status_code == 404
