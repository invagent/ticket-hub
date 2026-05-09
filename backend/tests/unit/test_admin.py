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
