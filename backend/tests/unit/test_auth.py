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


def test_callback_d0_pending_impl(app_client) -> None:
    resp = app_client.post("/api/auth/feishu/callback?code=fake")
    assert resp.status_code == 501
