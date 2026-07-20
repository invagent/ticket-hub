"""Zhichi client tests with respx mocking."""

from __future__ import annotations

import httpx
import pytest
import respx

from adapters.zhichi import (
    ReplyTicketRequest,
    ZhichiAuthError,
    ZhichiBusinessError,
    ZhichiClient,
    ZhichiConfig,
)

BASE = "https://www.soboten.com"


def _cfg() -> ZhichiConfig:
    return ZhichiConfig(appid="appid-1", app_key="key-x")


def _client() -> ZhichiClient:
    return ZhichiClient(_cfg(), http_client=httpx.Client(timeout=5.0))


def _stub_token(rsps: respx.MockRouter, *, token: str = "z-tok-1") -> respx.Route:
    return rsps.get(f"{BASE}/api/get_token").mock(
        return_value=httpx.Response(
            200,
            json={"ret_code": "000000", "item": {"token": token, "expires_in": 86400}},
        )
    )


@respx.mock
def test_get_ticket_by_id_success() -> None:
    _stub_token(respx)
    respx.get(f"{BASE}/api/ws/5/ticket/get_ticket_by_id").mock(
        return_value=httpx.Response(
            200, json={"ret_code": "000000", "item": {"ticketid": "T-1", "title": "x"}}
        )
    )
    with _client() as c:
        item = c.get_ticket_by_id("T-1")
    assert item["ticketid"] == "T-1"


@respx.mock
def test_get_ticket_by_id_business_error_raises() -> None:
    _stub_token(respx)
    respx.get(f"{BASE}/api/ws/5/ticket/get_ticket_by_id").mock(
        return_value=httpx.Response(200, json={"ret_code": "200001", "ret_msg": "ticket not found"})
    )
    with _client() as c, pytest.raises(ZhichiBusinessError) as ei:
        c.get_ticket_by_id("T-bad")
    assert ei.value.ret_code == "200001"


@respx.mock
def test_http_401_triggers_token_refresh_and_retry() -> None:
    respx.get(f"{BASE}/api/get_token").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"ret_code": "000000", "item": {"token": "old-tok", "expires_in": 86400}},
            ),
            httpx.Response(
                200,
                json={"ret_code": "000000", "item": {"token": "new-tok", "expires_in": 86400}},
            ),
        ]
    )
    biz = respx.get(f"{BASE}/api/ws/5/ticket/get_ticket_by_id").mock(
        side_effect=[
            httpx.Response(401),
            httpx.Response(200, json={"ret_code": "000000", "item": {"id": "T"}}),
        ]
    )
    with _client() as c:
        item = c.get_ticket_by_id("T")
    assert item == {"id": "T"}
    assert biz.call_count == 2
    assert biz.calls[1].request.headers["token"] == "new-tok"


@respx.mock
def test_token_expiry_via_ret_code_triggers_retry() -> None:
    respx.get(f"{BASE}/api/get_token").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"ret_code": "000000", "item": {"token": "old", "expires_in": 86400}},
            ),
            httpx.Response(
                200,
                json={"ret_code": "000000", "item": {"token": "new", "expires_in": 86400}},
            ),
        ]
    )
    biz = respx.get(f"{BASE}/api/ws/5/ticket/get_ticket_by_id").mock(
        side_effect=[
            httpx.Response(200, json={"ret_code": "100001", "ret_msg": "token expired"}),
            httpx.Response(200, json={"ret_code": "000000", "item": {"id": "T"}}),
        ]
    )
    with _client() as c:
        c.get_ticket_by_id("T")
    assert biz.call_count == 2
    assert biz.calls[1].request.headers["token"] == "new"


@respx.mock
def test_persistent_401_after_refresh_raises_auth_error() -> None:
    respx.get(f"{BASE}/api/get_token").mock(
        return_value=httpx.Response(
            200, json={"ret_code": "000000", "item": {"token": "tok", "expires_in": 86400}}
        )
    )
    respx.get(f"{BASE}/api/ws/5/ticket/get_ticket_by_id").mock(return_value=httpx.Response(401))
    with _client() as c, pytest.raises(ZhichiAuthError):
        c.get_ticket_by_id("T")


@respx.mock
def test_list_agents_caches() -> None:
    _stub_token(respx)
    route = respx.get(f"{BASE}/api/ws/5/ticket/get_data_dict").mock(
        return_value=httpx.Response(
            200,
            json={
                "ret_code": "000000",
                "item": {
                    "agent_list": [
                        {"agentid": "A1", "agent_name": "alice"},
                        {"agentid": "A2", "agent_name": "bob"},
                    ]
                },
            },
        )
    )
    with _client() as c:
        a1 = c.list_agents()
        a2 = c.list_agents()  # cached, no new HTTP call
    assert len(a1) == 2
    assert {a.agent_name for a in a1} == {"alice", "bob"}
    assert route.call_count == 1
    assert a2 == a1


@respx.mock
def test_get_agent_by_name() -> None:
    _stub_token(respx)
    respx.get(f"{BASE}/api/ws/5/ticket/get_data_dict").mock(
        return_value=httpx.Response(
            200,
            json={
                "ret_code": "000000",
                "item": {"agent_list": [{"agentid": "A1", "agent_name": "alice"}]},
            },
        )
    )
    with _client() as c:
        assert c.get_agent_by_name("alice").agentid == "A1"  # type: ignore[union-attr]
        assert c.get_agent_by_name("ghost") is None


@respx.mock
def test_token_refresh_uses_md5_sign() -> None:
    """Sanity: token refresh sends the right `sign` query param."""
    route = _stub_token(respx)
    respx.get(f"{BASE}/api/ws/5/ticket/get_ticket_by_id").mock(
        return_value=httpx.Response(200, json={"ret_code": "000000", "item": {}})
    )
    with _client() as c:
        c.get_ticket_by_id("T")
    qs = dict(route.calls.last.request.url.params)
    assert qs["appid"] == "appid-1"
    # md5 hex is 32 lowercase chars
    assert len(qs["sign"]) == 32
    assert qs["sign"].lower() == qs["sign"]


@respx.mock
def test_reply_ticket_sends_payload() -> None:
    _stub_token(respx)
    route = respx.post(f"{BASE}/api/ws/5/ticket/save_ticket_reply").mock(
        return_value=httpx.Response(200, json={"ret_code": "000000"})
    )
    with _client() as c:
        c.reply_ticket(
            ReplyTicketRequest(
                ticket_id="T1",
                ticket_title="T",
                ticket_content="C",
                ticket_status="OPEN",
                ticket_level="3",
                reply_agentid="A1",
                reply_agent_name="alice",
                reply_content="hi",
            )
        )
    body = route.calls.last.request.content.decode()
    assert "ticketid" in body
    assert "T1" in body
    assert "alice" in body
    # get_ticket_datetime 必须是北京时间当前时刻（智齿 400016 修复）：晚于 UTC now，
    # 接近 UTC+8。解析出来应落在 [UTC now, UTC now + 9h] 区间内（容忍执行耗时）。
    import json as _json
    from datetime import datetime, timedelta, timezone

    sent = _json.loads(body)["get_ticket_datetime"]
    sent_dt = datetime.strptime(sent, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    utc_now = datetime.now(timezone.utc)
    # 北京时间字符串被当作 UTC 解析后，应比真实 UTC now 晚约 8h
    assert timedelta(hours=7) < (sent_dt - utc_now) < timedelta(hours=9)
