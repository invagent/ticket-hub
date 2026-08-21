"""转研发 webhook 出口测试 —— fields 组装、回写、幂等、失败转 pending。

覆盖 push_hub_issue_to_linear 的 webhook 分支（settings.linear_webhook_enabled）
与 webhook_push.build_webhook_fields 的字段映射。LinearWebhookClient 注入 fake。
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from adapters.linear import LinearNetworkError
from app.config import get_settings
from app.models import (
    Customer,
    CustomerIdentity,
    HubIssue,
    ProductLine,
    Source,
    StatusHistory,
    Ticket,
)
from app.services.hub_issues.linear_push import push_hub_issue_to_linear
from app.services.hub_issues.webhook_push import build_webhook_fields, push_hub_issue_to_webhook


class _FakeWebhookClient:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self._raises = raises
        self.sent: list[dict] = []

    def send_ticket(self, fields):  # type: ignore[no-untyped-def]
        self.sent.append(fields)
        if self._raises is not None:
            raise self._raises
        return {"code": 0, "msg": "ok"}

    def close(self) -> None:
        pass


@pytest.fixture
def world(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("LINEAR_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("HUB_PUBLIC_BASE_URL", "https://hub.example.com/ticket-hub")
    get_settings.cache_clear()
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(Source(code="zhichi", name="智齿"))
    db_session.commit()
    yield db_session
    get_settings.cache_clear()


def _make_hub(db: Session, n: int, **overrides) -> HubIssue:  # type: ignore[no-untyped-def]
    base = {
        "short_code": f"HUB-WH-{n}",
        "type": "Bug_fix",
        "title": "开票失败",
        "canonical_body": "详细复现步骤",
        "status": "created",
        "priority": "high",
        "product": "发票云主产品",
        "module": "开票模块",
        "product_line_code": "fpy",
    }
    base.update(overrides)
    h = HubIssue(**base)
    db.add(h)
    db.commit()
    db.refresh(h)
    return h


def _make_ksm_ticket(db: Session, hub: HubIssue, **overrides) -> Ticket:  # type: ignore[no-untyped-def]
    base = {
        "short_code": "TKT-WH-1",
        "source_code": "ksm",
        "source_ticket_id": "ksm-bill-999",
        "type": "Raw",
        "status": "received",
        "title": "开票失败",
        "hub_issue_id": hub.id,
        "reporter": {
            "name": "张三",
            "mobile": "13800001111",
            "tel": "020-12345678",
            "email": "zhangsan@corp.com",
        },
        "reporter_tenant": "租户A",
    }
    base.update(overrides)
    t = Ticket(**base)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def test_build_fields_full_mapping(world: Session) -> None:
    world.add(ProductLine(code="fpy", name="发票云产品线"))
    world.commit()
    hub = _make_hub(world, 1)
    ticket = _make_ksm_ticket(world, hub)

    # 客户
    cust = Customer(display_name="金蝶软件", company="金蝶软件有限公司")
    world.add(cust)
    world.commit()
    world.refresh(cust)
    ident = CustomerIdentity(customer_id=cust.id, source_code="ksm", source_user_id="u-1")
    world.add(ident)
    world.commit()
    world.refresh(ident)
    ticket.customer_identity_id = ident.id
    world.commit()

    fields = build_webhook_fields(world, hub)

    assert fields["title"] == "开票失败"
    assert fields["description"] == "详细复现步骤"
    assert fields["ticketSource"] == "KSM"
    assert fields["priority"] == "高"
    assert fields["ticketType"] == "Bug_fix"
    assert fields["ticketId"] == "ksm-bill-999"
    assert fields["ticketNo"] == "TKT-WH-1"
    assert fields["customerName"] == "金蝶软件"
    assert fields["tenantName"] == "租户A"
    assert fields["productLine"] == "金蝶发票云"  # 默认顶级产品
    assert fields["productCategory"] == "发票云产品线"  # 产品线名，不为空
    assert fields["productModule"] == "发票云主产品"  # 主产品
    assert fields["productIssueModule"] == "开票模块"  # 模块，不为空
    assert fields["transferType"] == "BUG转产研"
    assert fields["subCategory"] == ""
    assert fields["reporter"] == "张三"
    assert fields["phone"] == "13800001111"
    assert fields["telephone"] == "020-12345678"
    assert fields["email"] == "zhangsan@corp.com"
    assert fields["feishuUrl"] == f"https://hub.example.com/ticket-hub/tickets/{ticket.id}"
    assert fields["operate"] == "BUG转产研修改工单状态及提单类型"


def test_build_fields_demand_operate_text(world: Session) -> None:
    hub = _make_hub(world, 2, type="Demand")
    _make_ksm_ticket(world, hub, short_code="TKT-WH-2")
    fields = build_webhook_fields(world, hub)
    assert fields["ticketType"] == "Demand"
    assert fields["transferType"] == "需求转产研"
    assert fields["operate"] == "需求转产研修改工单状态及提单类型"


def test_build_fields_handle_steps_from_ksm(world: Session) -> None:
    hub = _make_hub(world, 3)
    _make_ksm_ticket(
        world,
        hub,
        short_code="TKT-WH-3",
        source_payload={
            "_subscribe_callback": {
                "handleSteps": [
                    {
                        "nodeName": "受理",
                        "handleDateTime": "2026-08-01 10:00:00",
                        "dealopinion": "已接单",
                        "assignUser": {"realname": "客服小王"},
                        "nodeStatus": "1",
                    }
                ]
            }
        },
    )
    fields = build_webhook_fields(world, hub)
    assert "受理" in fields["handleSteps"]
    assert "客服小王" in fields["handleSteps"]
    assert "已接单" in fields["handleSteps"]


def test_build_fields_zhichi_no_handle_steps(world: Session) -> None:
    hub = _make_hub(world, 4)
    _make_ksm_ticket(
        world, hub, short_code="TKT-WH-4", source_code="zhichi", source_ticket_id="zc-1"
    )
    fields = build_webhook_fields(world, hub)
    assert fields["ticketSource"] == "智齿"
    assert fields["handleSteps"] == ""  # 非 KSM 无节点


def test_build_fields_productline_missing_default(world: Session) -> None:
    """productLine 恒为默认顶级产品；productCategory 无 ProductLine 记录时回落 code。"""
    hub = _make_hub(world, 5, product_line_code="unknown_code")
    _make_ksm_ticket(world, hub, short_code="TKT-WH-5")
    fields = build_webhook_fields(world, hub)
    assert fields["productLine"] == "金蝶发票云"
    assert fields["productCategory"] == "unknown_code"  # 查无 → 回落 code


def test_push_via_webhook_writes_back(world: Session) -> None:
    hub = _make_hub(world, 6)
    _make_ksm_ticket(world, hub, short_code="TKT-WH-6")
    fake = _FakeWebhookClient()
    # 直接测底层 push（注入 client）
    res = push_hub_issue_to_webhook(world, hub, client=fake)  # type: ignore[arg-type]
    assert res.ok is True
    assert len(fake.sent) == 1


def test_push_hub_issue_to_linear_routes_to_webhook(world: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """linear_webhook_enabled=true → push_hub_issue_to_linear 走 webhook，回写占位。"""
    from app.services.hub_issues import webhook_push

    hub = _make_hub(world, 7)
    _make_ksm_ticket(world, hub, short_code="TKT-WH-7")

    fake = _FakeWebhookClient()
    monkeypatch.setattr(webhook_push, "LinearWebhookClient", lambda cfg, **kw: fake)

    res = push_hub_issue_to_linear(hub.id, world)
    assert res is not None
    assert res.linear_identifier == f"WEBHOOK-{hub.short_code}"
    assert res.linear_uuid == ""  # webhook 不产生真实 Linear UUID
    world.refresh(hub)
    assert hub.linear_uuid is None  # 保持 NULL，避免 status_sync 误查
    assert hub.linear_identifier == f"WEBHOOK-{hub.short_code}"
    assert hub.linear_status == "已转产研"
    assert hub.linear_status_synced_at is not None
    assert len(fake.sent) == 1


def test_webhook_idempotent(world: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """已推（linear_identifier 非空）→ 不重复推。"""
    from app.services.hub_issues import webhook_push

    hub = _make_hub(world, 8, linear_identifier="WEBHOOK-HUB-WH-8")
    fake = _FakeWebhookClient()
    monkeypatch.setattr(webhook_push, "LinearWebhookClient", lambda cfg, **kw: fake)
    res = push_hub_issue_to_linear(hub.id, world)
    assert res is None
    assert fake.sent == []


def test_webhook_failure_marks_pending(world: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.services.hub_issues import webhook_push

    hub = _make_hub(world, 9)
    _make_ksm_ticket(world, hub, short_code="TKT-WH-9")
    fake = _FakeWebhookClient(raises=LinearNetworkError("timeout"))
    monkeypatch.setattr(webhook_push, "LinearWebhookClient", lambda cfg, **kw: fake)
    res = push_hub_issue_to_linear(hub.id, world)
    assert res is None
    world.refresh(hub)
    assert hub.status == "pending"
    assert hub.linear_identifier is None  # 可重试
    sh = (
        world.query(StatusHistory)
        .filter_by(entity_type="hub_issue", entity_id=hub.id, to_status="pending")
        .one()
    )
    assert "webhook 推送失败" in (sh.reason or "")


def test_webhook_skips_operation_type(world: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.services.hub_issues import webhook_push

    hub = _make_hub(world, 10, type="Operation")
    fake = _FakeWebhookClient()
    monkeypatch.setattr(webhook_push, "LinearWebhookClient", lambda cfg, **kw: fake)
    assert push_hub_issue_to_linear(hub.id, world) is None
    assert fake.sent == []


def test_webhook_no_public_base_empty_feishu_url(
    world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HUB_PUBLIC_BASE_URL", "")
    get_settings.cache_clear()
    hub = _make_hub(world, 11)
    _make_ksm_ticket(world, hub, short_code="TKT-WH-11")
    fields = build_webhook_fields(world, hub)
    assert fields["feishuUrl"] == ""
