"""Tests for JWT issuance and Feishu SSO endpoints."""

from datetime import UTC, datetime

from jose import jwt

from app.api.auth import issue_jwt
from app.config import get_settings


def test_issue_jwt_round_trip() -> None:
    token, ttl = issue_jwt(sub="u-123", name="alice", role="supervisor")
    assert ttl > 0

    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    assert payload["sub"] == "u-123"
    assert payload["name"] == "alice"
    assert payload["role"] == "supervisor"
    assert payload["exp"] > datetime.now(UTC).timestamp()


def test_login_url_returns_authorize_url(app_client) -> None:
    resp = app_client.get("/api/auth/feishu/login")
    assert resp.status_code == 200
    body = resp.json()
    assert body["authorize_url"].startswith("https://open.feishu.cn/")
    assert "test-app" in body["authorize_url"]


def test_callback_invalid_code_redirects_to_login_with_error(app_client) -> None:
    """D2 fix: callback is GET (browser redirect from Feishu); on auth failure
    we 302 to frontend /login#sso_error=... rather than returning JSON 401.

    Real OAuth flow is exercised in tests/unit/services/test_feishu_sso.py via respx mocks.
    """
    # No mocks — real httpx will fail to reach Feishu in unit env. Accept either:
    #   - 302 (FeishuSSOError caught, redirected to /login#sso_error=)
    #   - 503 (feishu credentials not configured in test env)
    resp = app_client.get("/api/auth/feishu/callback?code=fake", follow_redirects=False)
    assert resp.status_code in (302, 503)
    if resp.status_code == 302:
        assert "sso_error=" in resp.headers["location"]


def test_callback_rejects_post(app_client) -> None:
    """Callback only accepts GET (browser redirect)."""
    resp = app_client.post("/api/auth/feishu/callback?code=anything")
    assert resp.status_code == 405
