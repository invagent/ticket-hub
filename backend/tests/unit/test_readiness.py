"""Tests for /health/ready (K8s readiness probe)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api import health as health_module
from app.api.health import CheckResult

# ---- happy path: SQLite SELECT 1 succeeds --------------------------------


def test_ready_returns_200_with_pg_ok(app_client: TestClient) -> None:
    resp = app_client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["version"]
    assert len(body["checks"]) == 1
    pg = body["checks"][0]
    assert pg["name"] == "postgres"
    assert pg["ok"] is True
    assert pg["required"] is True
    assert pg["error"] is None
    assert pg["latency_ms"] >= 0


def test_ready_check_shape_matches_pydantic_model(app_client: TestClient) -> None:
    """Defensive: exact field set so frontend / k8s probe parsers don't break."""
    resp = app_client.get("/health/ready")
    body = resp.json()
    assert set(body.keys()) == {"status", "version", "checks"}
    assert set(body["checks"][0].keys()) == {"name", "ok", "latency_ms", "error", "required"}


# ---- failure: required check fails → 503 --------------------------------


def test_ready_returns_503_when_required_check_fails(app_client: TestClient, monkeypatch) -> None:
    """Simulate PG down by patching _check_pg."""

    def fake_check_pg(_db) -> CheckResult:  # type: ignore[no-untyped-def]
        return CheckResult(
            name="postgres",
            ok=False,
            latency_ms=12.3,
            error="OperationalError: connection refused",
        )

    monkeypatch.setattr(health_module, "_check_pg", fake_check_pg)

    resp = app_client.get("/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unhealthy"
    pg = body["checks"][0]
    assert pg["ok"] is False
    assert "connection refused" in pg["error"]
    assert pg["latency_ms"] == 12.3


# ---- degraded: optional check fails but required ok → 200 + 'degraded' ---


def test_ready_returns_200_degraded_when_only_optional_check_fails(
    app_client: TestClient, monkeypatch
) -> None:
    """When we add Redis (optional) in D3, this is the path it'll take when down."""

    def fake_run_checks(db) -> list[CheckResult]:  # type: ignore[no-untyped-def]
        return [
            CheckResult(name="postgres", ok=True, latency_ms=1.0, required=True),
            CheckResult(
                name="redis",
                ok=False,
                latency_ms=5000.0,
                error="ConnectionError: timeout",
                required=False,
            ),
        ]

    monkeypatch.setattr(health_module, "_run_checks", fake_run_checks)

    resp = app_client.get("/health/ready")
    assert resp.status_code == 200  # optional failure does not deny traffic
    body = resp.json()
    assert body["status"] == "degraded"
    names = {c["name"] for c in body["checks"]}
    assert names == {"postgres", "redis"}


# ---- liveness still works (separate path) -------------------------------


def test_liveness_endpoint_unchanged(app_client: TestClient) -> None:
    resp = app_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body
