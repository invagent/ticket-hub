"""ZammadIngester unit tests + webhook e2e."""

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
from app.services.ingest.zammad_ingester import IngestError, ZammadIngester


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="zammad", name="Zammad"))
    db_session.add(ProductLine(code="cloud-fapiao", name="金蝶发票云"))
    db_session.add(User(id=1, feishu_uid="ou_alice", name="alice", role="assignee"))
    db_session.commit()
    return db_session


def _payload(**overrides) -> dict:  # type: ignore[no-untyped-def]
    base = {
        "ticket": {
            "id": 1001,
            "number": "22001",
            "title": "发票同步失败",
            "state": "open",
            "priority": "2 normal",
            "group": "数电开票",
            "customer": {
                "id": 42,
                "name": "张三",
                "email": "zhangsan@example.com",
                "phone": "13800138000",
                "login": "zhangsan@example.com",
            },
            "tags": ["urgent"],
            "created_at": "2026-05-07T08:00:00.000Z",
            "updated_at": "2026-05-07T08:00:00.000Z",
            "product_line_code": "cloud-fapiao",
        },
        "article": {
            "id": 5001,
            "body": "发票提交后系统一直显示同步中",
            "content_type": "text/plain",
        },
    }
    ticket_overrides = overrides.pop("ticket", {})
    base["ticket"].update(ticket_overrides)
    base.update(overrides)
    return base


# ---- core ingest -----------------------------------------------------------


def test_first_ingest(world: Session) -> None:
    world.add(AssignmentScopeModule(user_id=1, product_line_code="cloud-fapiao", module="数电开票"))
    world.commit()
    res = ZammadIngester(world).ingest(_payload())
    world.commit()
    assert res.deduped is False
    assert res.routing_decision == "assigned"
    assert res.assigned_user_ids == [1]

    ticket = world.get(Ticket, res.ticket_id)
    assert ticket is not None
    assert ticket.source_code == "zammad"
    assert ticket.source_ticket_id == "1001"
    assert ticket.title == "发票同步失败"
    assert ticket.module == "数电开票"
    assert ticket.product_line_code == "cloud-fapiao"
    assert ticket.feature == "urgent"  # first tag becomes feature hint


def test_idempotent(world: Session) -> None:
    ZammadIngester(world).ingest(_payload())
    world.commit()
    res2 = ZammadIngester(world).ingest(_payload())
    world.commit()
    assert res2.deduped is True
    assert world.query(Ticket).count() == 1


def test_identity_extracted(world: Session) -> None:
    res = ZammadIngester(world).ingest(_payload())
    world.commit()
    ident = world.get(CustomerIdentity, res.customer_identity_id)
    assert ident is not None
    assert ident.email == "zhangsan@example.com"
    assert ident.mobile == "13800138000"


def test_customer_match_via_email(world: Session) -> None:
    """Existing customer with same email matched cross-source."""
    cust = Customer(display_name="known")
    world.add(cust)
    world.flush()
    world.add(
        CustomerIdentity(
            customer_id=cust.id,
            source_code="ksm",
            source_user_id="ksm-x",
            email="zhangsan@example.com",
            resolved_by_key="manual",
        )
    )
    world.commit()
    res = ZammadIngester(world).ingest(_payload())
    world.commit()
    assert res.customer_id == cust.id


def test_default_pool_fallback(world: Session) -> None:
    """No matching scope → default_pool."""
    pool_user = User(id=99, feishu_uid="ou_pool", name="pool", role="supervisor")
    world.add(pool_user)
    world.commit()
    res = ZammadIngester(world, default_pool_user_id=99).ingest(_payload())
    world.commit()
    assert res.routing_decision == "default_pool"
    assert 99 in res.assigned_user_ids


def test_no_tags_no_feature(world: Session) -> None:
    """Ticket with no tags → feature is None."""
    p = _payload()
    p["ticket"]["tags"] = []
    res = ZammadIngester(world).ingest(p)
    world.commit()
    ticket = world.get(Ticket, res.ticket_id)
    assert ticket.feature is None


def test_missing_ticket_id_raises(world: Session) -> None:
    p = _payload()
    p["ticket"]["id"] = 0
    with pytest.raises(IngestError, match="ticket.id"):
        ZammadIngester(world).ingest(p)


def test_missing_ticket_block_raises(world: Session) -> None:
    with pytest.raises(IngestError, match="ticket.id"):
        ZammadIngester(world).ingest({"article": {"id": 1, "body": "no ticket block"}})


def test_erp_uid_custom_field(world: Session) -> None:
    """ticket.erp_uid custom field is passed to identity resolver."""
    p = _payload()
    p["ticket"]["erp_uid"] = "ERP-ZD-001"
    res = ZammadIngester(world).ingest(p)
    world.commit()
    ident = world.get(CustomerIdentity, res.customer_identity_id)
    assert ident is not None
    assert ident.erp_uid == "ERP-ZD-001"


def test_reporter_includes_zammad_number(world: Session) -> None:
    res = ZammadIngester(world).ingest(_payload())
    world.commit()
    ticket = world.get(Ticket, res.ticket_id)
    assert ticket.reporter is not None
    assert ticket.reporter["zammad_number"] == "22001"


# ---- webhook e2e -----------------------------------------------------------


def test_webhook_zammad_e2e(app_client, db_session: Session) -> None:  # type: ignore[no-untyped-def]
    db_session.add(Source(code="zammad", name="Zammad"))
    db_session.add(ProductLine(code="cloud-fapiao", name="金蝶发票云"))
    db_session.add(User(id=1, feishu_uid="ou_alice", name="alice", role="assignee"))
    db_session.flush()
    db_session.add(
        AssignmentScopeModule(user_id=1, product_line_code="cloud-fapiao", module="全票池同步")
    )
    db_session.commit()
    resp = app_client.post(
        "/webhook/zammad?access_token=test-token",
        json={
            "ticket": {
                "id": 9999,
                "number": "99999",
                "title": "发票池同步异常",
                "state": "open",
                "priority": "2 normal",
                "group": "全票池同步",
                "customer": {
                    "id": 10,
                    "name": "李四",
                    "email": "lisi@corp.com",
                    "phone": "13700137000",
                    "login": "lisi@corp.com",
                },
                "tags": [],
                "created_at": "2026-05-07T09:00:00.000Z",
                "updated_at": "2026-05-07T09:00:00.000Z",
                "product_line_code": "cloud-fapiao",
            },
            "article": {
                "id": 100,
                "body": "全票池数量不对",
                "content_type": "text/plain",
            },
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["routing_decision"] == "assigned"
    assert body["assigned_user_ids"] == [1]
    assert body["deduped"] is False


def test_webhook_zammad_invalid_token(app_client) -> None:  # type: ignore[no-untyped-def]
    resp = app_client.post(
        "/webhook/zammad?access_token=wrong",
        json={"ticket": {"id": 1}},
    )
    assert resp.status_code == 401


def test_webhook_zammad_missing_ticket_id_returns_400(app_client) -> None:  # type: ignore[no-untyped-def]
    resp = app_client.post(
        "/webhook/zammad?access_token=test-token",
        json={"ticket": {"id": 0, "title": "no id"}, "article": {}},
    )
    assert resp.status_code == 400


def test_webhook_zammad_idempotent_returns_200(app_client, db_session: Session) -> None:  # type: ignore[no-untyped-def]
    db_session.add(Source(code="zammad", name="Zammad"))
    db_session.add(ProductLine(code="cloud-fapiao", name="金蝶发票云"))
    db_session.commit()
    payload = {
        "ticket": {
            "id": 7777,
            "number": "7777",
            "title": "重复工单",
            "state": "open",
            "priority": "2 normal",
            "group": "G",
            "customer": {"id": 1, "name": "u", "email": "u@u.com", "phone": "", "login": "u"},
            "tags": [],
            "created_at": "2026-05-07T00:00:00Z",
            "updated_at": "2026-05-07T00:00:00Z",
        },
        "article": {"id": 1, "body": "body", "content_type": "text/plain"},
    }
    r1 = app_client.post("/webhook/zammad?access_token=test-token", json=payload)
    r2 = app_client.post("/webhook/zammad?access_token=test-token", json=payload)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.json()["deduped"] is True
