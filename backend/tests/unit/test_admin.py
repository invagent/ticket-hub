"""Admin endpoint smoke tests (sources / product-lines).

Users CRUD moved to test_admin_users.py (D2-E).
"""

from app.models import ProductLine, Source


def test_list_sources_empty(app_client) -> None:
    resp = app_client.get("/api/admin/sources")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_sources_with_rows(app_client, db_session) -> None:
    db_session.add_all(
        [
            Source(code="ksm", name="KSM"),
            Source(code="zhichi", name="智齿"),
        ]
    )
    db_session.commit()
    resp = app_client.get("/api/admin/sources")
    assert resp.status_code == 200
    codes = [r["code"] for r in resp.json()]
    assert codes == ["ksm", "zhichi"]


def test_list_product_lines(app_client, db_session) -> None:
    db_session.add(ProductLine(code="cloud-erp", name="Cloud ERP"))
    db_session.commit()
    resp = app_client.get("/api/admin/product-lines")
    assert resp.status_code == 200
    assert resp.json()[0]["code"] == "cloud-erp"


def test_health(app_client) -> None:
    resp = app_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_trace_id_round_trips(app_client) -> None:
    resp = app_client.get("/health", headers={"X-Trace-Id": "abc123def4567890"})
    assert resp.headers["X-Trace-Id"] == "abc123def4567890"


# ---- D2-C: PATCH /api/admin/product-lines/{code} -----------------------


def _admin_bearer(uid: int = 1) -> dict[str, str]:
    from app.api.auth import issue_jwt
    token, _ = issue_jwt(sub=str(uid), name="admin", role="admin")
    return {"Authorization": f"Bearer {token}"}


def test_patch_product_line_sla_overrides(app_client, db_session) -> None:
    from app.models import ProductLine, User

    db_session.add(User(id=1, feishu_uid="ou_admin", name="admin", role="admin"))
    db_session.add(ProductLine(code="cloud-erp", name="Cloud ERP"))
    db_session.commit()

    r = app_client.patch(
        "/api/admin/product-lines/cloud-erp",
        headers=_admin_bearer(),
        json={"sla_reply_hours": 2, "sla_resolve_hours": 4},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sla_reply_hours"] == 2
    assert body["sla_resolve_hours"] == 4

    # GET reflects the change
    r2 = app_client.get("/api/admin/product-lines")
    pl = next(p for p in r2.json() if p["code"] == "cloud-erp")
    assert pl["sla_reply_hours"] == 2


def test_patch_product_line_clear_with_null(app_client, db_session) -> None:
    from app.models import ProductLine, User

    db_session.add(User(id=1, feishu_uid="ou_admin", name="admin", role="admin"))
    db_session.add(ProductLine(code="cloud-erp", name="Cloud ERP", sla_reply_hours=4))
    db_session.commit()

    r = app_client.patch(
        "/api/admin/product-lines/cloud-erp",
        headers=_admin_bearer(),
        json={"sla_reply_hours": None},
    )
    assert r.status_code == 200
    assert r.json()["sla_reply_hours"] is None


def test_patch_product_line_404(app_client, db_session) -> None:
    from app.models import User

    db_session.add(User(id=1, feishu_uid="ou_admin", name="admin", role="admin"))
    db_session.commit()
    r = app_client.patch(
        "/api/admin/product-lines/nonexistent",
        headers=_admin_bearer(),
        json={"sla_reply_hours": 4},
    )
    assert r.status_code == 404


def test_patch_product_line_requires_admin(app_client, db_session) -> None:
    from app.models import ProductLine, User

    db_session.add(User(id=2, feishu_uid="ou_member", name="m", role="member"))
    db_session.add(ProductLine(code="cloud-erp", name="Cloud ERP"))
    db_session.commit()
    from app.api.auth import issue_jwt
    token, _ = issue_jwt(sub="2", name="m", role="member")
    r = app_client.patch(
        "/api/admin/product-lines/cloud-erp",
        headers={"Authorization": f"Bearer {token}"},
        json={"sla_reply_hours": 4},
    )
    assert r.status_code == 403


def test_patch_product_line_validates_range(app_client, db_session) -> None:
    """ge=1, le=168 (a week max)."""
    from app.models import ProductLine, User

    db_session.add(User(id=1, feishu_uid="ou_admin", name="admin", role="admin"))
    db_session.add(ProductLine(code="cloud-erp", name="Cloud ERP"))
    db_session.commit()

    r = app_client.patch(
        "/api/admin/product-lines/cloud-erp",
        headers=_admin_bearer(),
        json={"sla_reply_hours": 0},
    )
    assert r.status_code == 422

    r = app_client.patch(
        "/api/admin/product-lines/cloud-erp",
        headers=_admin_bearer(),
        json={"sla_reply_hours": 200},
    )
    assert r.status_code == 422
