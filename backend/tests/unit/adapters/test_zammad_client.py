"""Zammad client tests with respx mocking."""

from __future__ import annotations

import httpx
import pytest
import respx

from adapters.zammad import (
    ZammadAuthError,
    ZammadBusinessError,
    ZammadClient,
    ZammadConfig,
    ZammadTicket,
)

BASE = "https://zammad.example.com"


def _cfg() -> ZammadConfig:
    return ZammadConfig(base_url=BASE, api_token="secret-token")


def _client() -> ZammadClient:
    return ZammadClient(_cfg(), http_client=httpx.Client(timeout=5.0))


# ---- ZammadTicket.from_payload ---------------------------------------------


def test_from_payload_full() -> None:
    payload = {
        "ticket": {
            "id": 42,
            "number": "22042",
            "title": "Test ticket",
            "state": "open",
            "priority": "2 normal",
            "group": "Support",
            "customer": {
                "id": 7,
                "name": "Alice",
                "email": "alice@example.com",
                "phone": "+1234567890",
                "login": "alice@example.com",
            },
            "tags": ["billing", "urgent"],
            "created_at": "2026-05-07T10:00:00.000Z",
            "updated_at": "2026-05-07T10:00:00.000Z",
            "erp_uid": "ERP-A-001",
            "product_line_code": "cloud-fapiao",
        },
        "article": {
            "id": 100,
            "body": "This is the body",
            "content_type": "text/plain",
        },
    }
    zt = ZammadTicket.from_payload(payload)
    assert zt.id == 42
    assert zt.number == "22042"
    assert zt.title == "Test ticket"
    assert zt.state == "open"
    assert zt.group == "Support"
    assert zt.customer.name == "Alice"
    assert zt.customer.email == "alice@example.com"
    assert zt.customer.phone == "+1234567890"
    assert zt.tags == ["billing", "urgent"]
    assert zt.article.body == "This is the body"
    assert zt.erp_uid == "ERP-A-001"
    assert zt.product_line_code == "cloud-fapiao"


def test_from_payload_minimal() -> None:
    """Minimal payload doesn't crash."""
    payload = {
        "ticket": {
            "id": 1,
            "number": "1",
            "title": "",
            "state": "",
            "priority": "",
            "group": "",
            "customer": {},
            "tags": [],
            "created_at": "",
            "updated_at": "",
        },
        "article": {},
    }
    zt = ZammadTicket.from_payload(payload)
    assert zt.id == 1
    assert zt.customer.name == ""
    assert zt.tags == []
    assert zt.article.body == ""
    assert zt.erp_uid is None
    assert zt.product_line_code is None


def test_tags_comma_string_parsed() -> None:
    """Zammad may send tags as a comma-separated string in some versions."""
    payload = {
        "ticket": {
            "id": 5,
            "number": "5",
            "title": "t",
            "state": "open",
            "priority": "2 normal",
            "group": "G",
            "customer": {"id": 1, "name": "u", "email": "", "phone": "", "login": ""},
            "tags": "invoicing, urgent",
            "created_at": "",
            "updated_at": "",
        },
        "article": {"id": 1, "body": "", "content_type": "text/plain"},
    }
    zt = ZammadTicket.from_payload(payload)
    assert zt.tags == ["invoicing", "urgent"]


# ---- HTTP client -----------------------------------------------------------


@respx.mock
def test_get_ticket_success() -> None:
    respx.get(f"{BASE}/api/v1/tickets/42").mock(
        return_value=httpx.Response(200, json={"id": 42, "title": "Test"})
    )
    with _client() as c:
        data = c.get_ticket(42)
    assert data["id"] == 42


@respx.mock
def test_get_ticket_401_raises_auth_error() -> None:
    respx.get(f"{BASE}/api/v1/tickets/1").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    with _client() as c, pytest.raises(ZammadAuthError):
        c.get_ticket(1)


@respx.mock
def test_get_ticket_403_raises_auth_error() -> None:
    respx.get(f"{BASE}/api/v1/tickets/2").mock(
        return_value=httpx.Response(403, json={"error": "forbidden"})
    )
    with _client() as c, pytest.raises(ZammadAuthError):
        c.get_ticket(2)


@respx.mock
def test_get_ticket_500_raises_business_error() -> None:
    respx.get(f"{BASE}/api/v1/tickets/3").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    with _client() as c, pytest.raises(ZammadBusinessError):
        c.get_ticket(3)


@respx.mock
def test_list_tickets_success() -> None:
    respx.get(f"{BASE}/api/v1/tickets").mock(
        return_value=httpx.Response(200, json=[{"id": 1}, {"id": 2}])
    )
    with _client() as c:
        items = c.list_tickets()
    assert len(items) == 2
    assert items[0]["id"] == 1


@respx.mock
def test_list_tickets_empty_on_non_list_response() -> None:
    respx.get(f"{BASE}/api/v1/tickets").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    with _client() as c:
        items = c.list_tickets()
    assert items == []


def test_auth_header_uses_token() -> None:
    """The Authorization header must use Token scheme."""
    client = ZammadClient(_cfg(), http_client=httpx.Client())
    headers = client._headers()
    assert headers["Authorization"] == "Token token=secret-token"
