"""Zammad adapter DTOs.

Zammad webhook payload shape (v6.x):
  {
    "ticket": {
      "id": 12345,
      "number": "22012",
      "title": "...",
      "state": "open",
      "priority": "2 normal",
      "group": "Support",
      "customer": {"id": 42, "name": "...", "email": "...", "phone": "..."},
      "tags": ["invoicing", "urgent"],
      "created_at": "2024-01-15T10:30:00.000Z",
      "updated_at": "2024-01-15T10:30:00.000Z"
    },
    "article": {
      "id": 456,
      "body": "...",
      "content_type": "text/plain"
    }
  }
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class ZammadConfig:
    base_url: str = "https://zammad.example.com"
    api_token: str = ""

    @classmethod
    def from_settings(cls, s: Any) -> ZammadConfig:
        return cls(
            base_url=getattr(s, "zammad_base_url", "https://zammad.example.com"),
            api_token=getattr(s, "zammad_api_token", ""),
        )


@dataclass(slots=True, frozen=True)
class ZammadCustomer:
    id: int
    name: str
    email: str
    phone: str
    login: str  # usually email-based login

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ZammadCustomer:
        return cls(
            id=int(d.get("id") or 0),
            name=str(d.get("name") or ""),
            email=str(d.get("email") or ""),
            phone=str(d.get("phone") or ""),
            login=str(d.get("login") or d.get("email") or ""),
        )


@dataclass(slots=True, frozen=True)
class ZammadArticle:
    id: int
    body: str
    content_type: str  # "text/plain" or "text/html"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ZammadArticle:
        return cls(
            id=int(d.get("id") or 0),
            body=str(d.get("body") or ""),
            content_type=str(d.get("content_type") or "text/plain"),
        )


@dataclass(slots=True)
class ZammadTicket:
    id: int
    number: str
    title: str
    state: str  # open / closed / pending reminder / etc.
    priority: str  # "1 low" / "2 normal" / "3 high"
    group: str  # Zammad Group name → maps to module
    customer: ZammadCustomer
    tags: list[str]
    article: ZammadArticle
    created_at: str
    updated_at: str
    # optional erp_uid extension field (configurable in Zammad)
    erp_uid: str | None = None
    product_line_code: str | None = None  # custom object field if set

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ZammadTicket:
        ticket = payload.get("ticket") or {}
        article = payload.get("article") or {}
        customer_raw = ticket.get("customer") or {}

        customer = ZammadCustomer.from_dict(customer_raw)
        article_obj = ZammadArticle.from_dict(article)

        tags = ticket.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        return cls(
            id=int(ticket.get("id") or 0),
            number=str(ticket.get("number") or ""),
            title=str(ticket.get("title") or ""),
            state=str(ticket.get("state") or ""),
            priority=str(ticket.get("priority") or ""),
            group=str(ticket.get("group") or ""),
            customer=customer,
            tags=tags,
            article=article_obj,
            created_at=str(ticket.get("created_at") or ""),
            updated_at=str(ticket.get("updated_at") or ""),
            erp_uid=ticket.get("erp_uid") or customer_raw.get("erp_uid"),
            product_line_code=ticket.get("product_line_code"),
        )
