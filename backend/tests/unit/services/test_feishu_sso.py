"""FeishuSSOService unit tests with respx mock."""

from __future__ import annotations

import httpx
import pytest
import respx
from sqlalchemy.orm import Session

from app.models import User
from app.services.auth.feishu_sso import (
    FeishuSSOConfig,
    FeishuSSOError,
    FeishuSSOService,
)

BASE = "https://open.feishu.cn"


def _cfg() -> FeishuSSOConfig:
    return FeishuSSOConfig(
        app_id="cli_test",
        app_secret="secret",
        redirect_uri="http://localhost:8080/api/auth/feishu/callback",
    )


def _svc() -> FeishuSSOService:
    return FeishuSSOService(_cfg(), http_client=httpx.Client(timeout=5.0))


def _stub_happy_path(rsps: respx.MockRouter, profile: dict | None = None) -> None:
    rsps.post(f"{BASE}/open-apis/auth/v3/app_access_token/internal").mock(
        return_value=httpx.Response(200, json={"app_access_token": "app-tok"})
    )
    rsps.post(f"{BASE}/open-apis/authen/v1/oidc/access_token").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": {"access_token": "user-tok", "expires_in": 7200},
            },
        )
    )
    rsps.get(f"{BASE}/open-apis/authen/v1/user_info").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "data": profile
                or {
                    "open_id": "ou_alice_001",
                    "name": "alice",
                    "email": "alice@kingdee.com",
                    "mobile": "+8613800138000",
                    "employee_no": "K0001",
                },
            },
        )
    )


@respx.mock
def test_first_login_creates_user(db_session: Session) -> None:
    _stub_happy_path(respx)
    svc = _svc()
    try:
        authed = svc.login(db_session, code="abc")
    finally:
        svc.close()
    db_session.commit()

    assert authed.feishu_uid == "ou_alice_001"
    assert authed.name == "alice"
    assert authed.email == "alice@kingdee.com"
    assert authed.role == "member"
    assert authed.user_id > 0

    user = db_session.query(User).filter(User.feishu_uid == "ou_alice_001").one()
    assert user.name == "alice"
    assert user.employee_no == "K0001"


@respx.mock
def test_second_login_updates_profile(db_session: Session) -> None:
    _stub_happy_path(respx)
    with _svc() as svc1:
        svc1.login(db_session, code="abc")
    db_session.commit()

    # Now simulate a second login with updated email
    respx.reset()
    _stub_happy_path(
        respx,
        profile={
            "open_id": "ou_alice_001",
            "name": "alice (updated)",
            "email": "alice-new@kingdee.com",
        },
    )
    svc2 = _svc()
    try:
        svc2.login(db_session, code="def")
    finally:
        svc2.close()
    db_session.commit()

    user = db_session.query(User).filter(User.feishu_uid == "ou_alice_001").one()
    assert user.name == "alice (updated)"
    assert user.email == "alice-new@kingdee.com"


@respx.mock
def test_soft_deleted_user_revived_on_login(db_session: Session) -> None:
    from datetime import UTC, datetime

    db_session.add(
        User(
            feishu_uid="ou_returnee_001",
            name="returnee",
            role="assignee",
            deleted_at=datetime.now(UTC),
        )
    )
    db_session.commit()

    _stub_happy_path(
        respx,
        profile={"open_id": "ou_returnee_001", "name": "returnee", "email": "x@y.com"},
    )
    svc = _svc()
    try:
        svc.login(db_session, code="abc")
    finally:
        svc.close()
    db_session.commit()

    user = db_session.query(User).filter(User.feishu_uid == "ou_returnee_001").one()
    assert user.deleted_at is None
    assert user.role == "assignee"  # role unchanged


@respx.mock
def test_empty_code_rejected(db_session: Session) -> None:
    svc = _svc()
    with pytest.raises(FeishuSSOError, match="empty authorization code"):
        svc.login(db_session, code="")
    svc.close()


@respx.mock
def test_missing_open_id_rejected(db_session: Session) -> None:
    """user_info that returns neither open_id nor user_id should fail."""
    _stub_happy_path(respx, profile={"name": "no-id"})
    svc = _svc()
    with pytest.raises(FeishuSSOError, match="open_id"):
        svc.login(db_session, code="abc")
    svc.close()


@respx.mock
def test_oidc_failure_propagates(db_session: Session) -> None:
    respx.post(f"{BASE}/open-apis/auth/v3/app_access_token/internal").mock(
        return_value=httpx.Response(200, json={"app_access_token": "ok"})
    )
    respx.post(f"{BASE}/open-apis/authen/v1/oidc/access_token").mock(
        return_value=httpx.Response(200, json={"code": 99991664, "msg": "invalid code"})
    )
    svc = _svc()
    with pytest.raises(FeishuSSOError, match="oidc"):
        svc.login(db_session, code="bad")
    svc.close()


@respx.mock
def test_app_token_failure_propagates(db_session: Session) -> None:
    respx.post(f"{BASE}/open-apis/auth/v3/app_access_token/internal").mock(
        return_value=httpx.Response(200, json={})
    )
    svc = _svc()
    with pytest.raises(FeishuSSOError, match="app_access_token"):
        svc.login(db_session, code="abc")
    svc.close()


@respx.mock
def test_callback_endpoint_e2e(app_client) -> None:  # type: ignore[no-untyped-def]
    """End-to-end through the FastAPI /api/auth/feishu/callback endpoint.

    D2 fix: callback is GET; on success it 302-redirects to the frontend SPA
    with a fragment containing the JWT. The token sits in URL hash so server
    logs / referer headers never see it.
    """
    _stub_happy_path(respx)
    resp = app_client.get("/api/auth/feishu/callback?code=abc", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["location"]
    # Fragment carries token + user info
    assert "#token=" in location
    assert "feishu_uid=ou_alice_001" in location
    assert "name=alice" in location
    assert "role=member" in location
    # Token sits AFTER the # — never goes server-side
    assert location.split("#", 1)[0].endswith("/")


@respx.mock
def test_callback_endpoint_invalid_code_redirects_to_login(app_client) -> None:  # type: ignore[no-untyped-def]
    """Bad code → 302 to frontend /login#sso_error=... (was 401 JSON pre-D2)."""
    respx.post(f"{BASE}/open-apis/auth/v3/app_access_token/internal").mock(
        return_value=httpx.Response(200, json={"app_access_token": "ok"})
    )
    respx.post(f"{BASE}/open-apis/authen/v1/oidc/access_token").mock(
        return_value=httpx.Response(200, json={"code": 1, "msg": "bad code"})
    )
    resp = app_client.get("/api/auth/feishu/callback?code=bad", follow_redirects=False)
    assert resp.status_code == 302
    assert "sso_error=" in resp.headers["location"]
    assert "/login#" in resp.headers["location"]
