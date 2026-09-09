"""ADR-0017 D4：产品内提单门户 —— 签名换 token、CRU、行级隔离、补料回炉、统计、aud 隔离。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.api.deps.portal_auth import compute_signature
from app.models import HubIssue, Source, Tenant, TenantUser, Ticket, User

SECRET = "s3cr3t-tenant-key"


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="embedded", name="产品内提单"))
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(User(id=1, feishu_uid="ou_admin", name="admin", role="admin"))
    db_session.add(Tenant(id=1, code="acme", name="Acme 公司", hmac_secret=SECRET, is_active=True))
    db_session.add(
        Tenant(id=2, code="dead", name="停用租户", hmac_secret="x" * 20, is_active=False)
    )
    db_session.commit()
    return db_session


def _token(
    app_client,
    external_uid: str = "u-001",
    *,
    name: str = "张三",
    tenant_code: str = "acme",
    secret: str = SECRET,
    ts: int | None = None,
) -> str:
    ts = ts if ts is not None else int(datetime.now(UTC).timestamp())
    r = app_client.post(
        "/api/portal/auth/token",
        json={
            "tenant_code": tenant_code,
            "external_uid": external_uid,
            "ts": ts,
            "sign": compute_signature(secret, tenant_code, external_uid, ts),
            "name": name,
            "email": f"{external_uid}@acme.test",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---- auth ---------------------------------------------------------------------


def test_token_issue_and_me(app_client, world: Session) -> None:
    tok = _token(app_client)
    r = app_client.get("/api/portal/me", headers=_h(tok))
    assert r.status_code == 200
    assert r.json() == {
        "tenant_code": "acme",
        "tenant_name": "Acme 公司",
        "external_uid": "u-001",
        "name": "张三",
    }
    tu = world.query(TenantUser).filter_by(external_uid="u-001").one()
    assert tu.customer_identity_id is not None  # 已落客户图谱
    assert tu.email == "u-001@acme.test"


def test_token_rejects_bad_signature_stale_ts_and_inactive_tenant(
    app_client, world: Session
) -> None:
    ts = int(datetime.now(UTC).timestamp())
    bad = app_client.post(
        "/api/portal/auth/token",
        json={"tenant_code": "acme", "external_uid": "u", "ts": ts, "sign": "0" * 64},
    )
    assert bad.status_code == 401
    stale = app_client.post(
        "/api/portal/auth/token",
        json={
            "tenant_code": "acme",
            "external_uid": "u",
            "ts": ts - 3600,
            "sign": compute_signature(SECRET, "acme", "u", ts - 3600),
        },
    )
    assert stale.status_code == 401
    inactive = app_client.post(
        "/api/portal/auth/token",
        json={
            "tenant_code": "dead",
            "external_uid": "u",
            "ts": ts,
            "sign": compute_signature("x" * 20, "dead", "u", ts),
        },
    )
    assert inactive.status_code == 401
    unknown = app_client.post(
        "/api/portal/auth/token",
        json={"tenant_code": "nope", "external_uid": "u", "ts": ts, "sign": "0" * 64},
    )
    assert unknown.status_code == 401


def test_employee_jwt_cannot_use_portal_and_vice_versa(app_client, world: Session) -> None:
    emp, _ = issue_jwt(sub="1", name="admin", role="admin")
    assert app_client.get("/api/portal/tickets", headers=_h(emp)).status_code == 401
    portal = _token(app_client)
    assert app_client.get("/api/tickets", headers=_h(portal)).status_code == 401


def test_portal_disabled_returns_404(
    app_client, world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    tok = _token(app_client)
    monkeypatch.setenv("PORTAL_ENABLED", "false")
    get_settings.cache_clear()
    try:
        assert app_client.get("/api/portal/tickets", headers=_h(tok)).status_code == 404
        ts = int(datetime.now(UTC).timestamp())
        r = app_client.post(
            "/api/portal/auth/token",
            json={
                "tenant_code": "acme",
                "external_uid": "u",
                "ts": ts,
                "sign": compute_signature(SECRET, "acme", "u", ts),
            },
        )
        assert r.status_code == 404
    finally:
        get_settings.cache_clear()


# ---- CRU ----------------------------------------------------------------------


def test_create_list_detail_own_only(app_client, world: Session) -> None:
    tok_a = _token(app_client, "u-a", name="甲")
    tok_b = _token(app_client, "u-b", name="乙")

    r = app_client.post(
        "/api/portal/tickets",
        json={"title": "开票失败", "body": "点击开票提示系统异常", "extra": {"page": "/invoice"}},
        headers=_h(tok_a),
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["stage"] == "received" and created["stage_label"] == "已接收"
    assert created["can_edit"] is True
    tid = created["id"]

    t = world.get(Ticket, tid)
    assert t is not None
    assert t.source_code == "embedded" and t.source_ticket_id.startswith("acme:")
    assert t.reporter_tenant == "Acme 公司"
    assert t.reporter["name"] == "甲"
    assert t.source_payload["embedded"]["page"] == "/invoice"
    assert t.customer_identity_id is not None
    assert t.assigned_user_id is None  # ADR-0017 D3：deprecated 字段不写

    # 甲看得到、乙看不到（404 不泄露存在性）
    assert [
        it["id"] for it in app_client.get("/api/portal/tickets", headers=_h(tok_a)).json()["items"]
    ] == [tid]
    assert app_client.get("/api/portal/tickets", headers=_h(tok_b)).json()["total"] == 0
    assert app_client.get(f"/api/portal/tickets/{tid}", headers=_h(tok_b)).status_code == 404

    d = app_client.get(f"/api/portal/tickets/{tid}", headers=_h(tok_a))
    assert d.status_code == 200
    assert d.json()["body"] == "点击开票提示系统异常"
    assert d.json()["timeline"][0]["stage"] == "received"
    assert d.json()["reply_content"] is None

    # 无 D
    assert app_client.delete(f"/api/portal/tickets/{tid}", headers=_h(tok_a)).status_code == 405


def test_update_allowed_only_in_editable_stages(app_client, world: Session) -> None:
    tok = _token(app_client, "u-c")
    tid = app_client.post(
        "/api/portal/tickets", json={"title": "t", "body": "b"}, headers=_h(tok)
    ).json()["id"]

    r = app_client.patch(f"/api/portal/tickets/{tid}", json={"title": "新标题"}, headers=_h(tok))
    assert r.status_code == 200 and r.json()["title"] == "新标题"

    # 进入研发中后不可改
    hub = HubIssue(
        short_code="HUB-000900",
        type="Bug_fix",
        title="t",
        status="in_progress",
        linear_status="Backlog",
    )
    world.add(hub)
    world.flush()
    t = world.get(Ticket, tid)
    assert t is not None
    t.hub_issue_id = hub.id
    world.commit()
    assert t.stage == "in_dev"
    r = app_client.patch(f"/api/portal/tickets/{tid}", json={"title": "再改"}, headers=_h(tok))
    assert r.status_code == 409
    d = app_client.get(f"/api/portal/tickets/{tid}", headers=_h(tok)).json()
    assert d["can_edit"] is False
    assert [e["stage"] for e in d["timeline"]] == ["received", "in_dev"]


def test_supplement_appends_and_reopens_supplementing(app_client, world: Session) -> None:
    tok = _token(app_client, "u-d")
    tid = app_client.post(
        "/api/portal/tickets", json={"title": "t", "body": "原文"}, headers=_h(tok)
    ).json()["id"]
    hub = HubIssue(
        short_code="HUB-000901",
        type="Operation",
        title="t",
        status="created",
        op_status="supplementing",
        op_handler="agent",
    )
    world.add(hub)
    world.flush()
    t = world.get(Ticket, tid)
    assert t is not None
    t.hub_issue_id = hub.id
    world.commit()
    assert t.stage == "supplementing"

    r = app_client.post(
        f"/api/portal/tickets/{tid}/supplement", json={"content": "补充截图说明"}, headers=_h(tok)
    )
    assert r.status_code == 200, r.text
    assert r.json()["stage"] == "processing"  # 补完自动回处理中
    world.refresh(hub)
    world.refresh(t)
    assert hub.op_status == "processing"
    assert "[提单人补充 @" in (t.body or "") and "补充截图说明" in (t.body or "")

    # 终态不可补充
    hub.op_status = "closed"
    world.commit()
    r = app_client.post(
        f"/api/portal/tickets/{tid}/supplement", json={"content": "x"}, headers=_h(tok)
    )
    assert r.status_code == 409


def test_stats_and_stage_filter(app_client, world: Session) -> None:
    tok = _token(app_client, "u-e")
    ids = [
        app_client.post(
            "/api/portal/tickets", json={"title": f"t{i}", "body": "b"}, headers=_h(tok)
        ).json()["id"]
        for i in range(3)
    ]
    hub = HubIssue(
        short_code="HUB-000902", type="Operation", title="t", status="created", op_status="closed"
    )
    world.add(hub)
    world.flush()
    t = world.get(Ticket, ids[0])
    assert t is not None
    t.hub_issue_id = hub.id
    world.commit()

    s = app_client.get("/api/portal/stats", headers=_h(tok)).json()
    assert s == {"total": 3, "open": 2, "closed": 1, "by_stage": {"received": 2, "closed": 1}}
    r = app_client.get("/api/portal/tickets", params={"stage": "closed"}, headers=_h(tok)).json()
    assert [it["id"] for it in r["items"]] == [ids[0]]
    r = app_client.get("/api/portal/tickets", params={"q": "t2"}, headers=_h(tok)).json()
    assert r["total"] == 1
