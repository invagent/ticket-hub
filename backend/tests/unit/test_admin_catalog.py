"""Tests for /api/admin/modules + /api/admin/features (D2-G)."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import Module, ProductLine, User


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add_all(
        [
            User(id=1, feishu_uid="ou_admin", name="admin", role="admin"),
            User(id=2, feishu_uid="ou_member", name="m", role="member"),
            ProductLine(code="cloud-fapiao", name="金蝶发票云"),
            ProductLine(code="cloud-erp-star", name="金蝶云星空"),
        ]
    )
    db_session.commit()
    return db_session


def _admin_bearer(uid: int = 1) -> dict[str, str]:
    token, _ = issue_jwt(sub=str(uid), name="admin", role="admin")
    return {"Authorization": f"Bearer {token}"}


def _member_bearer(uid: int = 2) -> dict[str, str]:
    token, _ = issue_jwt(sub=str(uid), name="m", role="member")
    return {"Authorization": f"Bearer {token}"}


# ---- modules -----------------------------------------------------------


def test_list_modules_empty(app_client, world: Session) -> None:
    r = app_client.get("/api/admin/modules", headers=_admin_bearer())
    assert r.status_code == 200
    assert r.json() == []


def test_create_module_then_list(app_client, world: Session) -> None:
    r = app_client.post(
        "/api/admin/modules",
        headers=_admin_bearer(),
        json={"product_line_code": "cloud-fapiao", "name": "数电开票"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "数电开票"
    assert body["is_active"] is True

    r2 = app_client.get(
        "/api/admin/modules?product_line_code=cloud-fapiao", headers=_admin_bearer()
    )
    assert r2.status_code == 200
    assert len(r2.json()) == 1


def test_create_module_unknown_product_line_404(app_client, world: Session) -> None:
    r = app_client.post(
        "/api/admin/modules",
        headers=_admin_bearer(),
        json={"product_line_code": "nope", "name": "x"},
    )
    assert r.status_code == 404


def test_create_module_duplicate_409(app_client, world: Session) -> None:
    payload = {"product_line_code": "cloud-fapiao", "name": "数电开票"}
    r1 = app_client.post("/api/admin/modules", headers=_admin_bearer(), json=payload)
    assert r1.status_code == 201
    r2 = app_client.post("/api/admin/modules", headers=_admin_bearer(), json=payload)
    assert r2.status_code == 409


def test_same_module_name_under_different_product_line_ok(app_client, world: Session) -> None:
    """Module is bound to product_line; same name under different lines OK."""
    r1 = app_client.post(
        "/api/admin/modules",
        headers=_admin_bearer(),
        json={"product_line_code": "cloud-fapiao", "name": "费用报销"},
    )
    r2 = app_client.post(
        "/api/admin/modules",
        headers=_admin_bearer(),
        json={"product_line_code": "cloud-erp-star", "name": "费用报销"},
    )
    assert r1.status_code == 201
    assert r2.status_code == 201


def test_delete_module(app_client, world: Session) -> None:
    r = app_client.post(
        "/api/admin/modules",
        headers=_admin_bearer(),
        json={"product_line_code": "cloud-fapiao", "name": "x"},
    )
    mid = r.json()["id"]
    r2 = app_client.delete(f"/api/admin/modules/{mid}", headers=_admin_bearer())
    assert r2.status_code == 204


def test_delete_module_404(app_client, world: Session) -> None:
    r = app_client.delete("/api/admin/modules/9999", headers=_admin_bearer())
    assert r.status_code == 404


def test_modules_require_admin(app_client, world: Session) -> None:
    r = app_client.get("/api/admin/modules", headers=_member_bearer())
    assert r.status_code == 403
    r2 = app_client.post(
        "/api/admin/modules",
        headers=_member_bearer(),
        json={"product_line_code": "cloud-fapiao", "name": "x"},
    )
    assert r2.status_code == 403


def test_list_modules_filter_inactive(app_client, db_session: Session) -> None:
    db_session.add(User(id=1, feishu_uid="ou_admin", name="admin", role="admin"))
    db_session.add(ProductLine(code="cloud-fapiao", name="金蝶发票云"))
    db_session.commit()
    db_session.add_all(
        [
            Module(product_line_code="cloud-fapiao", name="active-mod", is_active=True),
            Module(product_line_code="cloud-fapiao", name="inactive-mod", is_active=False),
        ]
    )
    db_session.commit()
    r = app_client.get("/api/admin/modules?product_line_code=cloud-fapiao", headers=_admin_bearer())
    names = [m["name"] for m in r.json()]
    assert "active-mod" in names
    assert "inactive-mod" not in names

    r2 = app_client.get(
        "/api/admin/modules?product_line_code=cloud-fapiao&active_only=false",
        headers=_admin_bearer(),
    )
    names2 = [m["name"] for m in r2.json()]
    assert "inactive-mod" in names2


# ---- features ----------------------------------------------------------


def test_create_feature_then_list(app_client, world: Session) -> None:
    r = app_client.post("/api/admin/features", headers=_admin_bearer(), json={"name": "数据导入"})
    assert r.status_code == 201
    assert r.json()["name"] == "数据导入"

    r2 = app_client.get("/api/admin/features", headers=_admin_bearer())
    assert len(r2.json()) == 1


def test_feature_duplicate_409(app_client, world: Session) -> None:
    app_client.post("/api/admin/features", headers=_admin_bearer(), json={"name": "x"})
    r = app_client.post("/api/admin/features", headers=_admin_bearer(), json={"name": "x"})
    assert r.status_code == 409


def test_features_require_admin(app_client, world: Session) -> None:
    r = app_client.get("/api/admin/features", headers=_member_bearer())
    assert r.status_code == 403


def test_delete_feature(app_client, world: Session) -> None:
    r = app_client.post("/api/admin/features", headers=_admin_bearer(), json={"name": "y"})
    fid = r.json()["id"]
    r2 = app_client.delete(f"/api/admin/features/{fid}", headers=_admin_bearer())
    assert r2.status_code == 204
