"""ZhichiIngester unit tests + webhook e2e."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models import (
    AssignmentScopeModule,
    Customer,
    CustomerIdentity,
    ProductLine,
    Source,
    Ticket,
    User,
)
from app.services.ingest.zhichi_ingester import IngestError, ZhichiIngester


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="zhichi", name="智齿"))
    db_session.add(ProductLine(code="cloud-erp", name="Cloud ERP"))
    db_session.add(User(id=1, feishu_uid="ou_alice", name="alice", role="assignee"))
    db_session.commit()
    return db_session


def _payload(**overrides) -> dict:  # type: ignore[no-untyped-def]
    base = {
        "ticketid": "zhichi-001",
        "ticket_title": "智齿来的",
        "ticket_content": "客户找不到入口",
        "customerid": "cust-zhichi-001",
        "customer": {
            "name": "李四",
            "email": "lisi@example.com",
            "mobile": "13900139000",
            "erp_uid": "ERP-LI",
        },
        "productLineCode": "cloud-erp",
        "moduleName": "应付管理",
    }
    base.update(overrides)
    return base


def test_first_ingest(world: Session) -> None:
    world.add(AssignmentScopeModule(user_id=1, product_line_code="cloud-erp", module="应付管理"))
    world.commit()
    res = ZhichiIngester(world).ingest(_payload())
    world.commit()
    assert res.deduped is False
    assert res.routing_decision == "assigned"
    assert res.assigned_user_ids == [1]

    ticket = world.get(Ticket, res.ticket_id)
    assert ticket is not None
    assert ticket.source_code == "zhichi"
    assert ticket.source_ticket_id == "zhichi-001"


def test_idempotent(world: Session) -> None:
    ZhichiIngester(world).ingest(_payload())
    world.commit()
    res2 = ZhichiIngester(world).ingest(_payload())
    world.commit()
    assert res2.deduped is True
    assert world.query(Ticket).count() == 1


def test_customer_block_extraction(world: Session) -> None:
    """Identity extracted from nested `customer` dict."""
    res = ZhichiIngester(world).ingest(_payload())
    world.commit()
    ident = world.get(CustomerIdentity, res.customer_identity_id)
    assert ident is not None
    assert ident.email == "lisi@example.com"
    assert ident.mobile == "13900139000"
    assert ident.erp_uid == "ERP-LI"


def test_customer_match_via_erp_uid(world: Session) -> None:
    """Existing customer with same erp_uid matched cross-source."""
    cust = Customer(display_name="known")
    world.add(cust)
    world.flush()
    world.add(
        CustomerIdentity(
            customer_id=cust.id,
            source_code="ksm",
            source_user_id="ksm-user-x",
            erp_uid="ERP-LI",
            resolved_by_key="manual",
        )
    )
    world.commit()
    res = ZhichiIngester(world).ingest(_payload())
    world.commit()
    assert res.customer_id == cust.id


def test_missing_ticketid_raises(world: Session) -> None:
    with pytest.raises(IngestError, match="ticketid"):
        ZhichiIngester(world).ingest(_payload(ticketid=""))


def test_webhook_zhichi_e2e(app_client, db_session: Session) -> None:  # type: ignore[no-untyped-def]
    db_session.add(Source(code="zhichi", name="智齿"))
    db_session.add(ProductLine(code="cloud-erp", name="Cloud ERP"))
    db_session.add(User(id=1, feishu_uid="ou_alice", name="alice", role="assignee"))
    db_session.flush()
    db_session.add(
        AssignmentScopeModule(user_id=1, product_line_code="cloud-erp", module="应付管理")
    )
    db_session.commit()
    resp = app_client.post(
        "/webhook/zhichi?access_token=test-token",
        json={
            "ticketid": "zhichi-e2e",
            "customerid": "u",
            "customer": {"erp_uid": "ERP-X", "name": "alice"},
            "productLineCode": "cloud-erp",
            "moduleName": "应付管理",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["routing_decision"] == "assigned"
    assert body["assigned_user_ids"] == [1]


def test_webhook_zhichi_invalid_token(app_client) -> None:  # type: ignore[no-untyped-def]
    resp = app_client.post("/webhook/zhichi?access_token=wrong", json={"ticketid": "x"})
    assert resp.status_code == 401


def test_webhook_zhichi_missing_ticketid_returns_400(app_client) -> None:  # type: ignore[no-untyped-def]
    resp = app_client.post(
        "/webhook/zhichi?access_token=test-token",
        json={"title": "no id"},
    )
    assert resp.status_code == 400
