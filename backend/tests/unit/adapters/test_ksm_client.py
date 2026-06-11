"""KSM client tests with respx mocking."""

from __future__ import annotations

import httpx
import pytest
import respx

from adapters.ksm import (
    HandleOrderRequest,
    KSMAuthError,
    KSMBusinessError,
    KSMClient,
    KSMConfig,
    LockOrderRequest,
    ReturnOrderRequest,
    SplitOrderRequest,
    SupplyOrderRequest,
)

BASE = "https://ierpuat.kingdee.com"


def _cfg() -> KSMConfig:
    return KSMConfig(
        base_url=BASE,
        app_id="app-id",
        app_secret="app-secret",
        tenant_id="t1",
        account_id="acc1",
        user="ops-bot",
    )


def _client() -> KSMClient:
    return KSMClient(_cfg(), http_client=httpx.Client(timeout=5.0))


def _stub_token(rsps: respx.MockRouter, *, expire_time_ms: int | None = None) -> None:
    rsps.post(f"{BASE}/ierp/api/getAppToken.do").mock(
        return_value=httpx.Response(200, json={"data": {"app_token": "app-tok"}})
    )
    login_data: dict[str, object] = {"access_token": "access-tok-1"}
    if expire_time_ms is not None:
        login_data["expire_time"] = expire_time_ms
    rsps.post(f"{BASE}/ierp/api/login.do").mock(
        return_value=httpx.Response(200, json={"data": login_data})
    )


@respx.mock
def test_lock_order_success() -> None:
    _stub_token(respx)
    route = respx.post(f"{BASE}/ierp/kapi/v2/kded/kded_wos/lockKsmOrder").mock(
        return_value=httpx.Response(200, json={"status": True, "result": "ok"})
    )

    with _client() as c:
        result = c.lock_order(
            LockOrderRequest(
                bill_id="bill-1",
                account="acc",
                account_number="ACC-001",
                account_name="alice",
            )
        )

    assert result["status"] is True
    body = route.calls.last.request.read().decode()
    assert "bill-1" in body
    assert "ACC-001" in body
    # access_token must be in the query string
    assert "access_token=access-tok-1" in str(route.calls.last.request.url)


@respx.mock
def test_handle_order_with_is_deal_overrides_bill_type() -> None:
    _stub_token(respx)
    route = respx.post(f"{BASE}/ierp/kapi/v2/kded/kded_wos/handleKsmOrder").mock(
        return_value=httpx.Response(200, json={"status": True})
    )

    with _client() as c:
        c.handle_order(
            HandleOrderRequest(
                bill_id="bill-2",
                account="a",
                account_number="N",
                account_name="bob",
                email="x@y.com",
                mobile="13800138000",
                product_id="P1",
                version_id="V1",
                module_id="M1",
                back_type="BT",
                node_id="N1",
                is_deal=True,
                bill_type="ignored",  # is_deal=True overrides
                deal_method="ignored",
            )
        )

    body = route.calls.last.request.content.decode()
    assert '"isDeal":"2"' in body
    assert '"billType":"服务咨询"' in body
    assert '"dealMethod":"指导解决"' in body


@respx.mock
def test_handle_order_customer_email_overrides_email() -> None:
    _stub_token(respx)
    route = respx.post(f"{BASE}/ierp/kapi/v2/kded/kded_wos/handleKsmOrder").mock(
        return_value=httpx.Response(200, json={"status": True})
    )

    with _client() as c:
        c.handle_order(
            HandleOrderRequest(
                bill_id="b",
                account="a",
                account_number="N",
                account_name="bob",
                email="agent@kingdee.com",
                customer_email="customer@example.com",
                mobile="13900139000",
                customer_mobile="13800138000",
                product_id="P",
                version_id="V",
                module_id="M",
                back_type="BT",
                node_id="N1",
            )
        )
    body = route.calls.last.request.content.decode()
    # customer_* should win
    assert "customer@example.com" in body
    assert "agent@kingdee.com" not in body
    assert "13800138000" in body
    assert "13900139000" not in body


@respx.mock
def test_handle_order_files_attached_when_provided() -> None:
    _stub_token(respx)
    route = respx.post(f"{BASE}/ierp/kapi/v2/kded/kded_wos/handleKsmOrder").mock(
        return_value=httpx.Response(200, json={"status": True})
    )

    with _client() as c:
        c.handle_order(
            HandleOrderRequest(
                bill_id="b",
                account="a",
                account_number="N",
                account_name="bob",
                email="x@y.com",
                mobile="13800138000",
                product_id="P",
                version_id="V",
                module_id="M",
                back_type="BT",
                node_id="N1",
                files=[{"name": "log.txt", "url": "https://x/y"}],
            )
        )
    body = route.calls.last.request.content.decode()
    assert '"files"' in body
    assert "log.txt" in body


@respx.mock
def test_business_failure_raises_ksm_business_error() -> None:
    _stub_token(respx)
    respx.post(f"{BASE}/ierp/kapi/v2/kded/kded_wos/lockKsmOrder").mock(
        return_value=httpx.Response(
            200, json={"status": False, "message": "duplicate lock", "errorCode": "200"}
        )
    )
    with _client() as c, pytest.raises(KSMBusinessError) as ei:
        c.lock_order(
            LockOrderRequest(bill_id="b", account="a", account_number="N", account_name="bob")
        )
    assert ei.value.op == "lockKsmOrder"
    assert "duplicate lock" in str(ei.value)


@respx.mock
def test_split_supply_return_orders_send_correct_paths() -> None:
    _stub_token(respx)
    split = respx.post(f"{BASE}/ierp/kapi/v2/kded/kded_wos/splitKsmOrder").mock(
        return_value=httpx.Response(200, json={"status": True})
    )
    supply = respx.post(f"{BASE}/ierp/kapi/v2/kded/kded_wos/supplyKsmOrder").mock(
        return_value=httpx.Response(200, json={"status": True})
    )
    ret = respx.post(f"{BASE}/ierp/kapi/v2/kded/kded_wos/returnKsmOrder").mock(
        return_value=httpx.Response(200, json={"status": True})
    )

    with _client() as c:
        c.split_order(
            SplitOrderRequest(
                bill_id="b1",
                split_count=3,
                account="a",
                account_number="N",
                account_name="bob",
            )
        )
        c.supply_order(
            SupplyOrderRequest(
                bill_id="b2",
                node_id="N2",
                account="a",
                account_number="N",
                account_name="bob",
            )
        )
        c.return_order(
            ReturnOrderRequest(
                bill_id="b3",
                deal_opinion="not enough info",
                opercache_id="OC",
                current_node_id="CN",
                account="a",
                account_number="N",
                account_name="bob",
            )
        )

    assert split.called
    assert supply.called
    assert ret.called


@respx.mock
def test_unauthorized_response_triggers_one_refresh() -> None:
    """If KSM returns errorCode=401 in body, we force-refresh + retry once."""
    # First, stub token endpoints (will be hit twice: initial + force refresh)
    respx.post(f"{BASE}/ierp/api/getAppToken.do").mock(
        side_effect=[
            httpx.Response(200, json={"data": {"app_token": "app1"}}),
            httpx.Response(200, json={"data": {"app_token": "app2"}}),
        ]
    )
    respx.post(f"{BASE}/ierp/api/login.do").mock(
        side_effect=[
            httpx.Response(200, json={"data": {"access_token": "tok-old"}}),
            httpx.Response(200, json={"data": {"access_token": "tok-new"}}),
        ]
    )
    # Business endpoint: first 401-in-body, then success
    biz = respx.post(f"{BASE}/ierp/kapi/v2/kded/kded_wos/lockKsmOrder").mock(
        side_effect=[
            httpx.Response(200, json={"errorCode": "401", "error_desc": "未经授权"}),
            httpx.Response(200, json={"status": True}),
        ]
    )

    with _client() as c:
        c.lock_order(
            LockOrderRequest(bill_id="b", account="a", account_number="N", account_name="bob")
        )

    # Two calls to the business endpoint
    assert biz.call_count == 2
    # Second call must have used the new token
    assert "access_token=tok-new" in str(biz.calls[1].request.url)


@respx.mock
def test_get_order_detail_success() -> None:
    _stub_token(respx)
    respx.post(f"{BASE}/ierp/kapi/app/open/subscribeCallback").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": True,
                "data": {"billId": "B1", "title": "查询订单"},
            },
        )
    )
    with _client() as c:
        detail = c.get_order_detail(bill_id="B1", notice_num="N", subscribe_num="S")
    assert detail["billId"] == "B1"
    assert detail["title"] == "查询订单"


@respx.mock
def test_get_order_detail_no_data_raises() -> None:
    _stub_token(respx)
    respx.post(f"{BASE}/ierp/kapi/app/open/subscribeCallback").mock(
        return_value=httpx.Response(200, json={"status": True})
    )
    with _client() as c, pytest.raises(KSMBusinessError, match="no data"):
        c.get_order_detail(bill_id="B1", notice_num="N", subscribe_num="S")


@respx.mock
def test_app_token_empty_raises_auth_error() -> None:
    respx.post(f"{BASE}/ierp/api/getAppToken.do").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    with _client() as c, pytest.raises(KSMAuthError, match="getAppToken"):
        c.lock_order(
            LockOrderRequest(bill_id="b", account="a", account_number="N", account_name="bob")
        )


@respx.mock
def test_login_empty_token_raises_auth_error() -> None:
    respx.post(f"{BASE}/ierp/api/getAppToken.do").mock(
        return_value=httpx.Response(200, json={"data": {"app_token": "ok"}})
    )
    respx.post(f"{BASE}/ierp/api/login.do").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    with _client() as c, pytest.raises(KSMAuthError, match="login"):
        c.lock_order(
            LockOrderRequest(bill_id="b", account="a", account_number="N", account_name="bob")
        )


@respx.mock
def test_download_attachment_uses_browser_user_agent() -> None:
    route = respx.get("https://files.example.com/x.png").mock(
        return_value=httpx.Response(200, content=b"\x89PNG\r\n")
    )
    with _client() as c:
        body = c.download_attachment("https://files.example.com/x.png")
    assert body == b"\x89PNG\r\n"
    assert route.calls.last.request.headers["User-Agent"].startswith("Mozilla")


@respx.mock
def test_token_cached_across_calls() -> None:
    _stub_token(respx, expire_time_ms=99999999999000)  # far future
    route = respx.post(f"{BASE}/ierp/kapi/v2/kded/kded_wos/lockKsmOrder").mock(
        return_value=httpx.Response(200, json={"status": True})
    )
    with _client() as c:
        for _ in range(3):
            c.lock_order(
                LockOrderRequest(bill_id="b", account="a", account_number="N", account_name="bob")
            )

    # Token endpoints called only once (first time); business 3 times
    assert respx.routes[0].call_count == 1  # getAppToken
    assert respx.routes[1].call_count == 1  # login
    assert route.call_count == 3
