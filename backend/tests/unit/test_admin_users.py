"""Admin /api/admin/users/* CRUD tests — D2-E.

Covers:
  - list / get-detail / patch / soft-delete
  - supervisor set / clear
  - partner add / remove (symmetric pair)
  - permission gate (admin only)
  - self-demotion / self-delete guards
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import AssignmentScopeModule, ProductLine, User, UserPartner, UserSupervisor


def _bearer(user_id: int, *, role: str = "admin") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(user_id), name="admin", role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(ProductLine(code="cloud-erp", name="Cloud ERP"))
    db_session.add_all(
        [
            User(id=1, feishu_uid="ou_admin", name="admin", role="admin"),
            User(id=2, feishu_uid="ou_alice", name="alice", role="assignee"),
            User(id=3, feishu_uid="ou_bob", name="bob", role="member"),
            User(id=4, feishu_uid="ou_charlie", name="charlie", role="assignee"),
        ]
    )
    db_session.commit()
    return db_session


# ---- list -----------------------------------------------------------------


def test_list_requires_admin(app_client, world) -> None:
    # No token → 401
    assert app_client.get("/api/admin/users").status_code == 401
    # Member token → 403
    r = app_client.get("/api/admin/users", headers=_bearer(3, role="member"))
    assert r.status_code == 403


def test_list_returns_active_only(app_client, world) -> None:
    world.add(User(id=99, feishu_uid="ou_deleted", name="deleted", deleted_at=datetime.utcnow()))
    world.commit()
    r = app_client.get("/api/admin/users", headers=_bearer(1))
    assert r.status_code == 200
    ids = [u["id"] for u in r.json()]
    assert ids == [1, 2, 3, 4]


# ---- detail aggregation ---------------------------------------------------


def test_detail_aggregates_scopes_supervisor_partners(app_client, world) -> None:
    world.add_all(
        [
            AssignmentScopeModule(user_id=2, product_line_code="cloud-erp", module="应付管理"),
            UserSupervisor(user_id=2, supervisor_id=1),
            UserPartner(user_id=2, partner_id=4),
            UserPartner(user_id=4, partner_id=2),
        ]
    )
    world.commit()
    r = app_client.get("/api/admin/users/2", headers=_bearer(1))
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["name"] == "alice"
    assert body["supervisor"]["supervisor_id"] == 1
    assert body["module_scopes"][0]["module"] == "应付管理"
    assert body["feature_scopes"] == []
    assert [p["id"] for p in body["partners"]] == [4]


def test_detail_404_on_missing(app_client, world) -> None:
    r = app_client.get("/api/admin/users/9999", headers=_bearer(1))
    assert r.status_code == 404


# ---- patch ----------------------------------------------------------------


def test_patch_role_and_email(app_client, world) -> None:
    r = app_client.patch(
        "/api/admin/users/3",
        headers=_bearer(1),
        json={"role": "assignee", "email": "bob@example.com"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "assignee"
    assert body["email"] == "bob@example.com"


def test_patch_self_demote_blocked(app_client, world) -> None:
    """Admin (id=1) trying to demote themselves to member → 400."""
    r = app_client.patch(
        "/api/admin/users/1",
        headers=_bearer(1),
        json={"role": "member"},
    )
    assert r.status_code == 400
    assert "demote" in r.json()["detail"]


def test_patch_self_keep_admin_ok(app_client, world) -> None:
    """Admin can update their own non-role fields."""
    r = app_client.patch(
        "/api/admin/users/1",
        headers=_bearer(1),
        json={"email": "admin@kingdee.com"},
    )
    assert r.status_code == 200
    assert r.json()["email"] == "admin@kingdee.com"


def test_patch_404_on_missing(app_client, world) -> None:
    r = app_client.patch("/api/admin/users/9999", headers=_bearer(1), json={"role": "assignee"})
    assert r.status_code == 404


# ---- soft-delete ----------------------------------------------------------


def test_delete_soft_marks_inactive(app_client, world) -> None:
    r = app_client.delete("/api/admin/users/3", headers=_bearer(1))
    assert r.status_code == 204
    # No longer in active list
    r2 = app_client.get("/api/admin/users", headers=_bearer(1))
    ids = [u["id"] for u in r2.json()]
    assert 3 not in ids


def test_delete_self_blocked(app_client, world) -> None:
    r = app_client.delete("/api/admin/users/1", headers=_bearer(1))
    assert r.status_code == 400
    assert "yourself" in r.json()["detail"]


def test_delete_404(app_client, world) -> None:
    r = app_client.delete("/api/admin/users/9999", headers=_bearer(1))
    assert r.status_code == 404


# ---- supervisor -----------------------------------------------------------


def test_supervisor_set_then_clear(app_client, world) -> None:
    r = app_client.post(
        "/api/admin/users/2/supervisor",
        headers=_bearer(1),
        json={"supervisor_id": 1, "deputy_supervisor_id": 4},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["supervisor_id"] == 1
    assert body["deputy_supervisor_id"] == 4

    r2 = app_client.delete("/api/admin/users/2/supervisor", headers=_bearer(1))
    assert r2.status_code == 204


def test_supervisor_self_rejected(app_client, world) -> None:
    r = app_client.post(
        "/api/admin/users/2/supervisor",
        headers=_bearer(1),
        json={"supervisor_id": 2},
    )
    assert r.status_code == 400


def test_supervisor_unknown_user(app_client, world) -> None:
    r = app_client.post(
        "/api/admin/users/2/supervisor",
        headers=_bearer(1),
        json={"supervisor_id": 9999},
    )
    assert r.status_code == 400


def test_supervisor_clear_404_when_none(app_client, world) -> None:
    r = app_client.delete("/api/admin/users/2/supervisor", headers=_bearer(1))
    assert r.status_code == 404


# ---- partners -------------------------------------------------------------


def test_partner_add_creates_symmetric_pair(app_client, world) -> None:
    r = app_client.post(
        "/api/admin/users/2/partners",
        headers=_bearer(1),
        json={"partner_id": 4},
    )
    assert r.status_code == 201
    body = r.json()
    assert [p["id"] for p in body] == [4]

    # Reverse direction also exists in DB (symmetric)
    rows = (
        world.query(UserPartner)
        .filter(
            ((UserPartner.user_id == 2) & (UserPartner.partner_id == 4))
            | ((UserPartner.user_id == 4) & (UserPartner.partner_id == 2))
        )
        .all()
    )
    assert len(rows) == 2


def test_partner_remove_clears_both_directions(app_client, world) -> None:
    world.add_all([UserPartner(user_id=2, partner_id=4), UserPartner(user_id=4, partner_id=2)])
    world.commit()
    r = app_client.delete("/api/admin/users/2/partners/4", headers=_bearer(1))
    assert r.status_code == 204
    assert world.query(UserPartner).count() == 0


def test_partner_remove_404_when_missing(app_client, world) -> None:
    r = app_client.delete("/api/admin/users/2/partners/4", headers=_bearer(1))
    assert r.status_code == 404


def test_partner_add_self_rejected(app_client, world) -> None:
    r = app_client.post(
        "/api/admin/users/2/partners",
        headers=_bearer(1),
        json={"partner_id": 2},
    )
    assert r.status_code == 400


def test_partner_add_duplicate_idempotent(app_client, world) -> None:
    """Re-adding the same partner pair returns 201 with the current list."""
    app_client.post("/api/admin/users/2/partners", headers=_bearer(1), json={"partner_id": 4})
    r = app_client.post("/api/admin/users/2/partners", headers=_bearer(1), json={"partner_id": 4})
    assert r.status_code == 201
    body = r.json()
    assert [p["id"] for p in body] == [4]
