"""Tests for /api/admin/dispatch/* endpoints — 运营分派规则 CRUD + 权限。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import DispatchAssignee, DispatchConfig, DispatchLog, DispatchRule, User


def _bearer(user_id: int, *, role: str = "admin") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(user_id), name="admin", role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_world(db_session: Session) -> Session:
    db_session.add_all(
        [
            User(id=3, feishu_uid="ou_carol", name="carol", role="supervisor"),
            User(id=50, feishu_uid="ou_50", name="运营A", role="assignee"),
            User(id=99, feishu_uid="ou_dave", name="dave", role="admin"),
        ]
    )
    db_session.commit()
    return db_session


RULE_BODY = {
    "name": "发票云运营",
    "match_sources": ["ksm"],
    "match_product_lines": ["cloud-fapiao"],
    "match_modules": ["数电开票"],
    "match_sla": [],
    "dispatch_mode": "count",
    "rule_type": "primary",
    "priority": 10,
}


# ---- permission -------------------------------------------------------------


def test_no_token_unauthorized(app_client: TestClient, admin_world: Session) -> None:
    assert app_client.get("/api/admin/dispatch/rules").status_code == 401


def test_supervisor_forbidden(app_client: TestClient, admin_world: Session) -> None:
    r = app_client.get("/api/admin/dispatch/rules", headers=_bearer(3, role="supervisor"))
    assert r.status_code == 403


def test_admin_allowed(app_client: TestClient, admin_world: Session) -> None:
    r = app_client.get("/api/admin/dispatch/rules", headers=_bearer(99))
    assert r.status_code == 200


# ---- rule CRUD ---------------------------------------------------------------


def test_rule_crud(app_client: TestClient, admin_world: Session) -> None:
    h = _bearer(99)
    r = app_client.post("/api/admin/dispatch/rules", json=RULE_BODY, headers=h)
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert r.json()["name"] == "发票云运营"

    listing = app_client.get("/api/admin/dispatch/rules", headers=h)
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    r2 = app_client.put(
        f"/api/admin/dispatch/rules/{rid}", json={**RULE_BODY, "priority": 5}, headers=h
    )
    assert r2.status_code == 200
    assert r2.json()["priority"] == 5

    assert app_client.delete(f"/api/admin/dispatch/rules/{rid}", headers=h).status_code == 204
    assert app_client.get("/api/admin/dispatch/rules", headers=h).json() == []


def test_update_unknown_rule_404(app_client: TestClient, admin_world: Session) -> None:
    r = app_client.put("/api/admin/dispatch/rules/9999", json=RULE_BODY, headers=_bearer(99))
    assert r.status_code == 404


def test_delete_unknown_rule_is_noop_204(app_client: TestClient, admin_world: Session) -> None:
    assert app_client.delete("/api/admin/dispatch/rules/9999", headers=_bearer(99)).status_code == 204


def test_invalid_dispatch_mode_rejected(app_client: TestClient, admin_world: Session) -> None:
    body = {**RULE_BODY, "dispatch_mode": "bogus"}
    r = app_client.post("/api/admin/dispatch/rules", json=body, headers=_bearer(99))
    assert r.status_code == 422


# ---- assignee CRUD ------------------------------------------------------------


def test_assignee_crud(app_client: TestClient, admin_world: Session, db_session: Session) -> None:
    h = _bearer(99)
    rid = app_client.post(
        "/api/admin/dispatch/rules",
        json={
            "name": "r",
            "match_sources": [],
            "match_product_lines": [],
            "match_modules": [],
            "match_sla": [],
            "dispatch_mode": "count",
            "rule_type": "primary",
            "priority": 100,
        },
        headers=h,
    ).json()["id"]

    ra = app_client.post(
        f"/api/admin/dispatch/rules/{rid}/assignees",
        json={"user_id": 50, "alloc_value": 1, "daily_cap": 20, "tier": "main"},
        headers=h,
    )
    assert ra.status_code == 200, ra.text
    aid = ra.json()["id"]
    assert ra.json()["rule_id"] == rid

    listing = app_client.get(f"/api/admin/dispatch/rules/{rid}/assignees", headers=h)
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    assert (
        app_client.delete(f"/api/admin/dispatch/rules/{rid}/assignees/{aid}", headers=h).status_code
        == 204
    )
    assert db_session.get(DispatchAssignee, aid) is None


def test_add_assignee_to_unknown_rule_404(app_client: TestClient, admin_world: Session) -> None:
    r = app_client.post(
        "/api/admin/dispatch/rules/9999/assignees",
        json={"user_id": 50, "alloc_value": 1, "tier": "main"},
        headers=_bearer(99),
    )
    assert r.status_code == 404


# ---- config -------------------------------------------------------------------


def test_config_upsert_and_get(app_client: TestClient, admin_world: Session) -> None:
    h = _bearer(99)
    rc = app_client.put(
        "/api/admin/dispatch/config",
        json={"key": "default_operation_assignee", "value": "50"},
        headers=h,
    )
    assert rc.status_code == 200
    assert rc.json() == {"default_operation_assignee": "50"}

    got = app_client.get("/api/admin/dispatch/config", headers=h)
    assert got.status_code == 200
    assert got.json()["default_operation_assignee"] == "50"

    # update existing key
    rc2 = app_client.put(
        "/api/admin/dispatch/config",
        json={"key": "default_operation_assignee", "value": "3"},
        headers=h,
    )
    assert rc2.status_code == 200
    assert app_client.get("/api/admin/dispatch/config", headers=h).json()["default_operation_assignee"] == "3"


# ---- logs ---------------------------------------------------------------------


def test_logs_list_and_filter(app_client: TestClient, admin_world: Session, db_session: Session) -> None:
    from app.models import HubIssue

    db_session.add(
        HubIssue(
            short_code="HUB-000001",
            type="Operation",
            title="t",
            canonical_body="b",
            status="created",
        )
    )
    db_session.commit()
    hub_issue_id = db_session.query(HubIssue).one().id

    rule = DispatchRule(
        name="r",
        match_sources=[],
        match_product_lines=[],
        match_modules=[],
        match_sla=[],
        dispatch_mode="count",
        rule_type="primary",
        priority=100,
    )
    db_session.add(rule)
    db_session.commit()

    db_session.add_all(
        [
            DispatchLog(
                hub_issue_id=hub_issue_id, rule_id=rule.id, assignee_user_id=50, tier_hit="main"
            ),
            DispatchLog(
                hub_issue_id=hub_issue_id, rule_id=None, assignee_user_id=3, tier_hit="default"
            ),
        ]
    )
    db_session.commit()

    h = _bearer(99)
    r = app_client.get("/api/admin/dispatch/logs", headers=h)
    assert r.status_code == 200
    assert len(r.json()) == 2

    r2 = app_client.get(f"/api/admin/dispatch/logs?rule_id={rule.id}", headers=h)
    assert r2.status_code == 200
    assert len(r2.json()) == 1
    assert r2.json()[0]["rule_id"] == rule.id


def test_config_table_isolated_from_rules(app_client: TestClient, admin_world: Session, db_session: Session) -> None:
    # sanity: DispatchConfig row is keyed by string key, not autoincrement id
    db_session.add(DispatchConfig(key="foo", value="bar"))
    db_session.commit()
    r = app_client.get("/api/admin/dispatch/config", headers=_bearer(99))
    assert r.json()["foo"] == "bar"
