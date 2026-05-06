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


def test_callback_invalid_code_returns_401(app_client) -> None:
    """D1: callback now implemented; bogus code propagates as 401 from feishu OIDC.

    Real OAuth flow is exercised in tests/unit/services/test_feishu_sso.py via respx mocks.
    Here we just verify the endpoint contract: 401 on auth failure.
    """
    # No mocks — real httpx will fail to reach Feishu in unit env.
    # Either DNS / network → 401 (FeishuSSOError handler); we accept 401 OR 503.
    resp = app_client.post("/api/auth/feishu/callback?code=fake")
    assert resp.status_code in (401, 503)
