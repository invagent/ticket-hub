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


# ---- D2-G2: POST/DELETE /api/admin/product-lines ----


def test_post_product_line_creates(app_client, db_session) -> None:
    from app.models import User

    db_session.add(User(id=1, feishu_uid="ou_admin", name="admin", role="admin"))
    db_session.commit()

    r = app_client.post(
        "/api/admin/product-lines",
        headers=_admin_bearer(),
        json={"code": "cloud-new", "name": "New Cloud", "sla_reply_hours": 8},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["code"] == "cloud-new"
    assert body["sla_reply_hours"] == 8

    r2 = app_client.get("/api/admin/product-lines")
    assert any(p["code"] == "cloud-new" for p in r2.json())


def test_post_product_line_duplicate_409(app_client, db_session) -> None:
    from app.models import ProductLine, User

    db_session.add(User(id=1, feishu_uid="ou_admin", name="admin", role="admin"))
    db_session.add(ProductLine(code="cloud-erp", name="Cloud ERP"))
    db_session.commit()

    r = app_client.post(
        "/api/admin/product-lines",
        headers=_admin_bearer(),
        json={"code": "cloud-erp", "name": "Cloud ERP"},
    )
    assert r.status_code == 409


def test_post_product_line_requires_admin(app_client, db_session) -> None:
    from app.models import User

    db_session.add(User(id=2, feishu_uid="ou_member", name="m", role="member"))
    db_session.commit()
    from app.api.auth import issue_jwt
    token, _ = issue_jwt(sub="2", name="m", role="member")
    r = app_client.post(
        "/api/admin/product-lines",
        headers={"Authorization": f"Bearer {token}"},
        json={"code": "x", "name": "y"},
    )
    assert r.status_code == 403


def test_delete_product_line_no_modules_ok(app_client, db_session) -> None:
    from app.models import ProductLine, User

    db_session.add(User(id=1, feishu_uid="ou_admin", name="admin", role="admin"))
    db_session.add(ProductLine(code="empty-pl", name="Empty"))
    db_session.commit()

    r = app_client.delete("/api/admin/product-lines/empty-pl", headers=_admin_bearer())
    assert r.status_code == 204


def test_delete_product_line_with_modules_409(app_client, db_session) -> None:
    from app.models import Module, ProductLine, User

    db_session.add(User(id=1, feishu_uid="ou_admin", name="admin", role="admin"))
    db_session.add(ProductLine(code="busy-pl", name="Busy"))
    db_session.flush()
    db_session.add(Module(product_line_code="busy-pl", name="some-mod", is_active=True))
    db_session.commit()

    r = app_client.delete("/api/admin/product-lines/busy-pl", headers=_admin_bearer())
    assert r.status_code == 409
    assert "modules" in r.json()["detail"].lower()


def test_delete_product_line_404(app_client, db_session) -> None:
    from app.models import User

    db_session.add(User(id=1, feishu_uid="ou_admin", name="admin", role="admin"))
    db_session.commit()
    r = app_client.delete("/api/admin/product-lines/nope", headers=_admin_bearer())
    assert r.status_code == 404
