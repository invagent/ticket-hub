"""ZhichiIngester unit tests + webhook e2e."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models import (
    AssignmentScopeModule,
    Attachment,
    Customer,
    CustomerIdentity,
    HubIssue,
    ProductLine,
    Source,
    Ticket,
    User,
)
from app.services.hub_issues.op_status import OP_PROCESSING
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


def test_ingest_handler_defaults_to_assignee(world: Session) -> None:
    """入库时处理人(handler_user_id)默认=责任人(assigned_user_id)。"""
    world.add(AssignmentScopeModule(user_id=1, product_line_code="cloud-erp", module="应付管理"))
    world.commit()
    res = ZhichiIngester(world).ingest(_payload())
    world.commit()
    ticket = world.get(Ticket, res.ticket_id)
    assert ticket is not None
    assert ticket.assigned_user_id == 1
    assert ticket.handler_user_id == 1  # 处理人初始=责任人


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


# ---- 智齿原生扁平格式（线上真实推送，TKT-000015 结构）----
# 顶层直接是 ticket_*/user_*/extend_fields_list，无 raw/fields 外壳。


def _native_flat(**overrides) -> dict:  # type: ignore[no-untyped-def]
    base = {
        "ticketid": "e240351f6a7e4e518df9d61d1fa5af11",
        "ticket_code": "20260720000001",
        "ticket_title": "客户留言-13800000000",
        "ticket_content": "<p>发票云找不到出货的注册邮件无法进行配置，如何处理</p>",
        "user_emails": "user@example.com",
        "user_tels": "13800000000",
        "userid": "8bd452fb32274a22a03ce6a854ebc15b",
        "enterprise_name": "",
        "deal_agent_name": "",
        "ticket_status": 0,
        "ticket_level": 0,
        "extend_fields_list": [
            {
                "field_name": "产品分类",
                "field_type": "6",
                "field_text": "星瀚-收票",
                "field_value": "cf4110965a3f4077be70c7796f90b819",
            },
            {
                "field_name": "对接ERP",
                "field_type": "6",
                "field_text": "星瀚",
                "field_value": "751275257313121",
            },
            {"field_name": "联系人", "field_type": "1", "field_value": "李志坚"},
            {"field_name": "联系手机", "field_type": "1", "field_value": "13800000000"},
            {
                "field_name": "公司/项目名称",
                "field_type": "1",
                "field_value": "金蝶软件（中国）有限公司",
            },
        ],
    }
    base.update(overrides)
    return base


def test_ingest_native_flat_maps_fields(world: Session) -> None:
    """线上真实扁平格式：extend_fields_list + 顶层字段全部正确映射（此前全落空）。"""
    res = ZhichiIngester(world).ingest(_native_flat())
    world.commit()
    t = world.get(Ticket, res.ticket_id)
    assert t is not None
    assert t.source_ticket_id == "e240351f6a7e4e518df9d61d1fa5af11"
    assert t.product_line_code == "星瀚-收票"
    assert t.module == "星瀚-收票"
    assert t.reporter["name"] == "李志坚"
    assert t.reporter["mobile"] == "13800000000"
    assert t.reporter["email"] == "user@example.com"
    # body 保留完整（含 HTML）
    assert t.body == "<p>发票云找不到出货的注册邮件无法进行配置，如何处理</p>"
    # source_payload 存原样，出站回写读 deal_agent_name / ticket_level
    assert t.source_payload["ticket_code"] == "20260720000001"
    assert t.source_payload["extend_fields_list"][0]["field_name"] == "产品分类"


def test_native_flat_creates_attachment_from_file_str(world: Session) -> None:
    """智齿 file_str（sobot CDN 图片 URL）→ 建 Attachment 行（queued，待异步下载+OCR）。"""
    url = "https://img.sobot.com/console/ticket/20260810/abc/pic_123.jpg"
    res = ZhichiIngester(world).ingest(_native_flat(file_str=url))
    world.commit()
    atts = world.query(Attachment).filter_by(ticket_id=res.ticket_id).all()
    assert len(atts) == 1
    assert atts[0].source_url == url
    assert atts[0].kind == "image"
    assert atts[0].vision_status == "queued"


def test_native_flat_multiple_file_str(world: Session) -> None:
    """file_str 多个 URL（逗号/空格分隔）→ 每个建一行。"""
    two = (
        "https://img.sobot.com/a/1.jpg, https://img.sobot.com/b/2.png"
    )
    res = ZhichiIngester(world).ingest(_native_flat(file_str=two))
    world.commit()
    atts = world.query(Attachment).filter_by(ticket_id=res.ticket_id).all()
    assert len(atts) == 2
    assert {a.source_url for a in atts} == {
        "https://img.sobot.com/a/1.jpg",
        "https://img.sobot.com/b/2.png",
    }


def test_native_flat_no_file_str_no_attachment(world: Session) -> None:
    """无 file_str → 不建附件行。"""
    res = ZhichiIngester(world).ingest(_native_flat())
    world.commit()
    assert world.query(Attachment).filter_by(ticket_id=res.ticket_id).count() == 0


def test_native_flat_fallback_title_uses_content(world: Session) -> None:
    """「客户留言-手机号」兜底标题 → 视同无标题，改用去 HTML 的内容。"""
    res = ZhichiIngester(world).ingest(_native_flat())
    world.commit()
    t = world.get(Ticket, res.ticket_id)
    assert t is not None
    assert t.title == "发票云找不到出货的注册邮件无法进行配置，如何处理"


def test_native_flat_real_title_kept(world: Session) -> None:
    """正常人工标题保留，不被内容覆盖。"""
    res = ZhichiIngester(world).ingest(_native_flat(ticket_title="发票红冲失败"))
    world.commit()
    t = world.get(Ticket, res.ticket_id)
    assert t is not None
    assert t.title == "发票红冲失败"


def test_native_flat_title_truncated_to_150(world: Session) -> None:
    """内容超 150 字 → 标题截前 150。"""
    long_content = "<p>" + ("问" * 300) + "</p>"
    res = ZhichiIngester(world).ingest(
        _native_flat(ticket_title="客户留言-13800000000", ticket_content=long_content)
    )
    world.commit()
    t = world.get(Ticket, res.ticket_id)
    assert t is not None
    assert t.title == "问" * 150
    assert len(t.title) == 150


def test_native_flat_title_strips_html(world: Session) -> None:
    """标题去 HTML 标签与常见实体。"""
    res = ZhichiIngester(world).ingest(
        _native_flat(ticket_content="<p>发票&nbsp;报错<br/>无法开具</p>")
    )
    world.commit()
    t = world.get(Ticket, res.ticket_id)
    assert t is not None
    assert "<" not in t.title
    assert "&nbsp;" not in t.title
    assert t.title == "发票 报错 无法开具"


def test_native_flat_empty_title_and_content_falls_back(world: Session) -> None:
    """兜底标题 + 内容也空 → 退回原兜底标题（至少有手机号），不为 None。"""
    res = ZhichiIngester(world).ingest(
        _native_flat(ticket_title="客户留言-13800000000", ticket_content="")
    )
    world.commit()
    t = world.get(Ticket, res.ticket_id)
    assert t is not None
    assert t.title == "客户留言-13800000000"


# ---- ticket_status → op_status 终态同步（新单/已存在工单/已毕业 hub 三场景）----


def test_ticket_status_0_unaffected(world: Session) -> None:
    """ticket_status=0（尚未受理）不受影响，正常入库，不建 hub。"""
    res = ZhichiIngester(world).ingest(_native_flat(ticket_status=0))
    world.commit()
    assert res.skip_post_ingest is False
    t = world.get(Ticket, res.ticket_id)
    assert t is not None
    assert t.hub_issue_id is None


def test_ticket_status_3_new_ticket_answered(world: Session) -> None:
    """全新工单首次入库即 ticket_status=3（已解决）→ 直接建 Operation hub 落 answered。"""
    res = ZhichiIngester(world).ingest(_native_flat(ticket_status=3))
    world.commit()
    assert res.skip_post_ingest is True
    t = world.get(Ticket, res.ticket_id)
    assert t is not None
    assert t.hub_issue_id is not None
    hub = world.get(HubIssue, t.hub_issue_id)
    assert hub is not None
    assert hub.type == "Operation"
    assert hub.op_status == "answered"


def test_ticket_status_99_new_ticket_closed(world: Session) -> None:
    """全新工单首次入库即 ticket_status=99（已关闭）→ 直接建 Operation hub 落 closed。"""
    res = ZhichiIngester(world).ingest(_native_flat(ticket_status=99))
    world.commit()
    assert res.skip_post_ingest is True
    t = world.get(Ticket, res.ticket_id)
    assert t is not None
    hub = world.get(HubIssue, t.hub_issue_id)
    assert hub is not None
    assert hub.op_status == "closed"


def test_ticket_status_3_existing_no_hub_graduates(world: Session) -> None:
    """已存在工单（未毕业）重推变 ticket_status=3 → 命中 dedup 分支，直接建 hub 落 answered。"""
    ingester = ZhichiIngester(world)
    res1 = ingester.ingest(_native_flat(ticket_status=0))
    world.commit()
    t1 = world.get(Ticket, res1.ticket_id)
    assert t1 is not None
    assert t1.hub_issue_id is None

    res2 = ingester.ingest(_native_flat(ticket_status=3))
    world.commit()
    assert res2.deduped is True
    assert res2.ticket_id == res1.ticket_id
    t2 = world.get(Ticket, res2.ticket_id)
    assert t2 is not None
    assert t2.hub_issue_id is not None
    hub = world.get(HubIssue, t2.hub_issue_id)
    assert hub is not None
    assert hub.type == "Operation"
    assert hub.op_status == "answered"


def test_ticket_status_99_existing_with_hub_transitions(world: Session) -> None:
    """已毕业 Operation hub（processing）重推变 ticket_status=99 → 转态为 closed。"""
    ingester = ZhichiIngester(world)
    res1 = ingester.ingest(_native_flat(ticket_status=0))
    world.commit()
    t1 = world.get(Ticket, res1.ticket_id)
    assert t1 is not None

    hub = HubIssue(
        short_code="HUB-000001",
        type="Operation",
        status="created",
        op_status=OP_PROCESSING,
        title=t1.title,
    )
    world.add(hub)
    world.flush()
    t1.hub_issue_id = hub.id
    world.commit()

    res2 = ingester.ingest(_native_flat(ticket_status=99))
    world.commit()
    assert res2.deduped is True
    world.refresh(hub)
    assert hub.op_status == "closed"


def test_ticket_status_string_and_int_both_work(world: Session) -> None:
    """ticket_status 字符串 "3" 和整数 3 都能命中终态映射（智齿文档类型定义不一致）。"""
    res_str = ZhichiIngester(world).ingest(_native_flat(ticketid="T-STR", ticket_status="3"))
    world.commit()
    assert res_str.skip_post_ingest is True
    t_str = world.get(Ticket, res_str.ticket_id)
    assert t_str is not None
    hub_str = world.get(HubIssue, t_str.hub_issue_id)
    assert hub_str is not None
    assert hub_str.op_status == "answered"

    res_int = ZhichiIngester(world).ingest(_native_flat(ticketid="T-INT", ticket_status=3))
    world.commit()
    assert res_int.skip_post_ingest is True
    t_int = world.get(Ticket, res_int.ticket_id)
    assert t_int is not None
    hub_int = world.get(HubIssue, t_int.hub_issue_id)
    assert hub_int is not None
    assert hub_int.op_status == "answered"
