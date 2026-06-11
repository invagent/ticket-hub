"""Tests for /api/admin/scopes/* endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import (
    AssignmentScopeFeature,
    AssignmentScopeHistory,
    AssignmentScopeModule,
    ProductLine,
    User,
)


def _bearer(user_id: int, *, role: str = "admin") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(user_id), name="admin", role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_world(db_session: Session) -> Session:
    db_session.add(ProductLine(code="cloud-erp", name="Cloud ERP"))
    db_session.add_all(
        [
            User(id=1, feishu_uid="ou_alice", name="alice", role="assignee"),
            User(id=2, feishu_uid="ou_bob", name="bob", role="assignee"),
            User(id=3, feishu_uid="ou_carol", name="carol", role="supervisor"),
            User(id=99, feishu_uid="ou_dave", name="dave", role="admin"),
        ]
    )
    db_session.commit()
    return db_session


# ---- permission ----------------------------------------------------------


def test_list_modules_requires_admin(app_client: TestClient, admin_world: Session) -> None:
    # No token
    assert app_client.get("/api/admin/scopes/modules").status_code == 401
    # supervisor (carol) — not enough
    assert (
        app_client.get(
            "/api/admin/scopes/modules", headers=_bearer(3, role="supervisor")
        ).status_code
        == 403
    )
    # admin (dave) — OK
    assert app_client.get("/api/admin/scopes/modules", headers=_bearer(99)).status_code == 200


def test_supervisor_cannot_add_module(app_client: TestClient, admin_world: Session) -> None:
    resp = app_client.post(
        "/api/admin/scopes/modules",
        json={"user_id": 1, "product_line_code": "cloud-erp", "module": "应付"},
        headers=_bearer(3, role="supervisor"),
    )
    assert resp.status_code == 403


# ---- module CRUD ---------------------------------------------------------


def test_add_module_writes_row_and_history(
    app_client: TestClient, admin_world: Session, db_session: Session
) -> None:
    resp = app_client.post(
        "/api/admin/scopes/modules",
        json={"user_id": 1, "product_line_code": "cloud-erp", "module": "应付管理"},
        headers=_bearer(99),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["user_id"] == 1
    assert body["module"] == "应付管理"
    assert body["product_line_code"] == "cloud-erp"

    # row exists in scope table
    rows = db_session.query(AssignmentScopeModule).all()
    assert len(rows) == 1

    # history audit row written
    histories = db_session.query(AssignmentScopeHistory).all()
    assert len(histories) == 1
    h = histories[0]
    assert h.scope_type == "module"
    assert h.action == "add"
    assert h.user_id == 1
    assert h.changed_by == 99
    assert h.payload == {"product_line_code": "cloud-erp", "module": "应付管理"}


def test_add_duplicate_module_returns_409(app_client: TestClient, admin_world: Session) -> None:
    payload = {"user_id": 1, "product_line_code": "cloud-erp", "module": "应付"}
    r1 = app_client.post("/api/admin/scopes/modules", json=payload, headers=_bearer(99))
    assert r1.status_code == 201
    r2 = app_client.post("/api/admin/scopes/modules", json=payload, headers=_bearer(99))
    assert r2.status_code == 409
    assert "already exists" in r2.json()["detail"]


def test_list_modules_with_filters(
    app_client: TestClient, admin_world: Session, db_session: Session
) -> None:
    db_session.add_all(
        [
            AssignmentScopeModule(user_id=1, product_line_code="cloud-erp", module="应付"),
            AssignmentScopeModule(user_id=2, product_line_code="cloud-erp", module="应收"),
            AssignmentScopeModule(user_id=2, product_line_code="cloud-erp", module="应付"),
        ]
    )
    db_session.commit()

    # all
    r = app_client.get("/api/admin/scopes/modules", headers=_bearer(99))
    assert r.status_code == 200
    assert len(r.json()) == 3

    # filter by user
    r = app_client.get("/api/admin/scopes/modules?user_id=2", headers=_bearer(99))
    assert {row["module"] for row in r.json()} == {"应付", "应收"}

    # filter by module
    r = app_client.get("/api/admin/scopes/modules?module=应付", headers=_bearer(99))
    assert {row["user_id"] for row in r.json()} == {1, 2}


def test_delete_module_writes_remove_history(
    app_client: TestClient, admin_world: Session, db_session: Session
) -> None:
    db_session.add(
        AssignmentScopeModule(id=10, user_id=1, product_line_code="cloud-erp", module="应付")
    )
    db_session.commit()

    resp = app_client.delete("/api/admin/scopes/modules/10", headers=_bearer(99))
    assert resp.status_code == 204

    assert db_session.get(AssignmentScopeModule, 10) is None

    h = db_session.query(AssignmentScopeHistory).one()
    assert h.action == "remove"
    assert h.user_id == 1
    assert h.changed_by == 99
    assert h.payload["module"] == "应付"


def test_delete_module_unknown_returns_404(app_client: TestClient, admin_world: Session) -> None:
    resp = app_client.delete("/api/admin/scopes/modules/9999", headers=_bearer(99))
    assert resp.status_code == 404


# ---- feature CRUD -------------------------------------------------------


def test_add_feature_and_list(
    app_client: TestClient, admin_world: Session, db_session: Session
) -> None:
    resp = app_client.post(
        "/api/admin/scopes/features",
        json={"user_id": 1, "feature": "数据导入"},
        headers=_bearer(99),
    )
    assert resp.status_code == 201
    assert resp.json()["feature"] == "数据导入"

    h = db_session.query(AssignmentScopeHistory).one()
    assert h.scope_type == "feature"
    assert h.action == "add"

    listing = app_client.get("/api/admin/scopes/features?feature=数据导入", headers=_bearer(99))
    assert len(listing.json()) == 1


def test_add_duplicate_feature_returns_409(app_client: TestClient, admin_world: Session) -> None:
    body = {"user_id": 1, "feature": "F"}
    r1 = app_client.post("/api/admin/scopes/features", json=body, headers=_bearer(99))
    assert r1.status_code == 201
    r2 = app_client.post("/api/admin/scopes/features", json=body, headers=_bearer(99))
    assert r2.status_code == 409


def test_delete_feature_writes_remove_history(
    app_client: TestClient, admin_world: Session, db_session: Session
) -> None:
    db_session.add(AssignmentScopeFeature(id=20, user_id=1, feature="F"))
    db_session.commit()
    resp = app_client.delete("/api/admin/scopes/features/20", headers=_bearer(99))
    assert resp.status_code == 204
    assert db_session.get(AssignmentScopeFeature, 20) is None

    h = db_session.query(AssignmentScopeHistory).one()
    assert h.scope_type == "feature"
    assert h.action == "remove"
    assert h.payload["feature"] == "F"


# ---- history -----------------------------------------------------------


def test_history_endpoint_filters(app_client: TestClient, admin_world: Session) -> None:
    # Use the API itself to create scopes (so history is populated)
    app_client.post(
        "/api/admin/scopes/modules",
        json={"user_id": 1, "product_line_code": "cloud-erp", "module": "M1"},
        headers=_bearer(99),
    )
    app_client.post(
        "/api/admin/scopes/features",
        json={"user_id": 2, "feature": "F1"},
        headers=_bearer(99),
    )
    app_client.post(
        "/api/admin/scopes/features",
        json={"user_id": 2, "feature": "F2"},
        headers=_bearer(99),
    )

    # all
    r = app_client.get("/api/admin/scopes/history", headers=_bearer(99))
    assert r.status_code == 200
    assert len(r.json()) == 3

    # filter by user
    r = app_client.get("/api/admin/scopes/history?user_id=2", headers=_bearer(99))
    assert {h["user_id"] for h in r.json()} == {2}

    # filter by scope_type
    r = app_client.get("/api/admin/scopes/history?scope_type=feature", headers=_bearer(99))
    assert all(h["scope_type"] == "feature" for h in r.json())

    # bad scope_type
    r = app_client.get("/api/admin/scopes/history?scope_type=bogus", headers=_bearer(99))
    assert r.status_code == 422


def test_history_ordered_by_changed_at_desc(app_client: TestClient, admin_world: Session) -> None:
    app_client.post(
        "/api/admin/scopes/features",
        json={"user_id": 1, "feature": "first"},
        headers=_bearer(99),
    )
    app_client.post(
        "/api/admin/scopes/features",
        json={"user_id": 1, "feature": "second"},
        headers=_bearer(99),
    )
    r = app_client.get("/api/admin/scopes/history", headers=_bearer(99))
    payloads = [h["payload"] for h in r.json()]
    # newest first → "second" should appear before "first"
    assert payloads[0]["feature"] == "second"
    assert payloads[1]["feature"] == "first"
