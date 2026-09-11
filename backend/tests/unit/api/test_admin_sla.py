"""Tests for /api/admin/sla-levels/* endpoints — 服务等级 & SLA 配置 CRUD + 权限 + 流水号生成."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import SlaLevel, User


def _bearer(user_id: int, *, name: str = "admin", role: str = "admin") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(user_id), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def sla_world(db_session: Session) -> Session:
    db_session.add_all(
        [
            User(id=3, feishu_uid="ou_carol", name="carol", role="supervisor"),
            User(id=99, feishu_uid="ou_dave", name="Dave", role="admin"),
            SlaLevel(
                id="SEVERLEVEL0001",
                code="22",
                name="标准成功服务（2023版）",
                sort_order=1,
                issue_levels="P0、P1、P2、P3",
                issue_types="不限",
                sla_hours=40.0,
                source_system="KSM",
                source_system_field="serviceLevel",
                source_system_code="22",
                updated_by="系统初始化",
            ),
        ]
    )
    db_session.commit()
    return db_session


def test_sla_permissions(app_client: TestClient, sla_world: Session) -> None:
    # 401 without auth
    assert app_client.get("/api/admin/sla-levels").status_code == 401
    assert app_client.post("/api/admin/sla-levels", json={}).status_code == 401
    assert app_client.put("/api/admin/sla-levels/SEVERLEVEL0001", json={}).status_code == 401

    # 403 for non-admin
    assert app_client.get("/api/admin/sla-levels", headers=_bearer(3, role="supervisor")).status_code == 403


def test_list_sla_levels(app_client: TestClient, sla_world: Session) -> None:
    r = app_client.get("/api/admin/sla-levels", headers=_bearer(99))
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["id"] == "SEVERLEVEL0001"
    assert data[0]["name"] == "标准成功服务（2023版）"
    assert data[0]["sla_hours"] == 40.0
    assert data[0]["source_system"] == "KSM"
    assert data[0]["source_system_field"] == "serviceLevel"
    assert data[0]["source_system_code"] == "22"
    assert data[0]["updated_by"] == "系统初始化"


def test_create_sla_level(app_client: TestClient, sla_world: Session) -> None:
    body = {
        "name": "战略VIP服务",
        "issue_levels": "P0、P1",
        "issue_types": "需求、bug修复",
        "sla_hours": 12.5,
        "source_system": "智齿",
        "source_system_field": "ticket_level",
        "source_system_code": "vip",
        "sort_order": 2,
    }
    r = app_client.post("/api/admin/sla-levels", json=body, headers=_bearer(99, name="Dave"))
    assert r.status_code == 201
    created = r.json()
    assert created["id"] == "SEVERLEVEL0002"
    assert created["name"] == "战略VIP服务"
    assert created["code"] == "vip"
    assert created["source_system_code"] == "vip"
    assert created["sla_hours"] == 12.5
    assert created["updated_by"] == "Dave"
    assert created["updated_at"] is not None

    # Verify listing includes newly created
    r_list = app_client.get("/api/admin/sla-levels", headers=_bearer(99))
    assert len(r_list.json()) == 2


def test_create_sla_level_validation_sla_hours(app_client: TestClient, sla_world: Session) -> None:
    # sla_hours <= 0 is invalid
    body = {
        "name": "无效服务",
        "issue_levels": "P0",
        "issue_types": "不限",
        "sla_hours": 0,
        "source_system": "KSM",
        "source_system_field": "serviceLevel",
        "source_system_code": "0",
    }
    r = app_client.post("/api/admin/sla-levels", json=body, headers=_bearer(99))
    assert r.status_code == 422


def test_update_sla_level(app_client: TestClient, sla_world: Session) -> None:
    update_body = {
        "name": "标准成功服务（改版）",
        "issue_levels": "P0、P1、P2",
        "issue_types": "应用类",
        "sla_hours": 24.0,
        "source_system": "KSM",
        "source_system_field": "serviceLevel",
        "source_system_code": "22",
    }
    r = app_client.put(
        "/api/admin/sla-levels/SEVERLEVEL0001",
        json=update_body,
        headers=_bearer(99, name="Dave"),
    )
    assert r.status_code == 200
    updated = r.json()
    assert updated["id"] == "SEVERLEVEL0001"
    assert updated["name"] == "标准成功服务（改版）"
    assert updated["sla_hours"] == 24.0
    assert updated["updated_by"] == "Dave"


def test_update_non_existent(app_client: TestClient, sla_world: Session) -> None:
    r = app_client.put(
        "/api/admin/sla-levels/SEVERLEVEL9999",
        json={
            "name": "不存在",
            "issue_levels": "P0",
            "issue_types": "不限",
            "sla_hours": 10.0,
            "source_system": "KSM",
            "source_system_field": "serviceLevel",
            "source_system_code": "9999",
        },
        headers=_bearer(99),
    )
    assert r.status_code == 404
