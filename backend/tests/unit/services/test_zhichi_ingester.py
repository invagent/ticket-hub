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


# ---- 真实信封格式 {source, raw, fields}（工单参数.txt 权威格式）----

_ENVELOPE = {
    "source": "zhichi",
    "raw": {
        "ticketid": "T20260101001",
        "ticket_title": "工单标题",
        "ticket_content": "问题描述内容",
        "ticket_level": 2,
        "user_emails": "user@example.com",
        "deal_agent_name": "莉莉",
        "enterprise_name": "某某有限公司",
        "extend_fields_list": [
            {
                "field_name": "产品分类",
                "field_type": "6",
                "field_text": "星空旗舰版-开票",
                "field_value": "opt1",
            },
            {
                "field_name": "联系手机",
                "field_type": "1",
                "field_text": "",
                "field_value": "13800000000",
            },
        ],
    },
    "fields": {
        "工单来源ID": "T20260101001",
        "主题": "工单标题",
        "问题描述": "问题描述内容",
        "产品线": "金蝶发票云",
        "产品模块": "星空旗舰版-开票",
        "联系人": "张三",
        "联系人手机": "13800000000",
        "反馈人邮箱": "user@example.com",
        "客户名称": "某某有限公司",
    },
}


def test_ingest_envelope_maps_fields(world: Session) -> None:
    res = ZhichiIngester(world).ingest(_ENVELOPE)
    world.commit()
    t = world.get(Ticket, res.ticket_id)
    assert t is not None
    assert t.source_ticket_id == "T20260101001"
    assert t.title == "工单标题"
    assert t.body == "问题描述内容"
    assert t.product_line_code == "金蝶发票云"
    assert t.module == "星空旗舰版-开票"
    assert t.reporter["name"] == "张三"
    assert t.reporter["mobile"] == "13800000000"
    assert t.reporter["email"] == "user@example.com"
    # source_payload 存整个信封，出站回写要用 raw.deal_agent_name / ticket_level
    assert t.source_payload["raw"]["deal_agent_name"] == "莉莉"
    assert t.source_payload["raw"]["ticket_level"] == 2


def test_ingest_legacy_flat_still_works(world: Session) -> None:
    """向后兼容：无 raw/fields 的旧扁平格式仍解析。"""
    res = ZhichiIngester(world).ingest(
        {"ticketid": "OLD1", "title": "旧格式", "content": "正文", "product": "cloud-erp"}
    )
    world.commit()
    t = world.get(Ticket, res.ticket_id)
    assert t is not None
    assert t.source_ticket_id == "OLD1"
    assert t.title == "旧格式"
    assert t.product_line_code == "cloud-erp"


def test_ingest_extend_fields_type6_takes_text(world: Session) -> None:
    """extend_fields_list field_type=6（下拉）取 field_text；仅 raw 无 fields 时兜底。"""
    payload = {
        "source": "zhichi",
        "raw": {
            "ticketid": "T-EXT",
            "ticket_title": "标题",
            "ticket_content": "正文",
            "extend_fields_list": [
                {
                    "field_name": "产品分类",
                    "field_type": "6",
                    "field_text": "云星空-税务",
                    "field_value": "code123",
                },
                {
                    "field_name": "对接ERP",
                    "field_type": "1",
                    "field_text": "",
                    "field_value": "ERP-777",
                },
            ],
        },
    }
    res = ZhichiIngester(world).ingest(payload)
    world.commit()
    t = world.get(Ticket, res.ticket_id)
    assert t is not None
    # field_type=6 取 field_text（不是 field_value 的 code123）
    assert t.product_line_code == "云星空-税务"
