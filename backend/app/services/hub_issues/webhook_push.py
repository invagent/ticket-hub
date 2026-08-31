"""转研发出口：把 Bug_fix / Demand hub_issue 以约定 fields 推送到飞书 webhook。

这是 push_hub_issue_to_linear 的 webhook 分支（settings.linear_webhook_enabled）。
组装 `{"fields": {...}}` payload：数据取自 hub_issue + 其主源工单（第一条关联 ticket）
+ 客户 + 产品线目录。成功后同直连一样回写 hub.linear_uuid/identifier，让幂等/
状态回同步逻辑复用（webhook 无 Linear id 时用回执或占位标记）。

字段口径（与用户确认）：
  productLine        顶级产品，默认 "金蝶发票云"（linear_webhook_default_product_line）
  productCategory    产品线名（ProductLine.name），不为空
  productModule      主产品（hub.product）
  productIssueModule 产品模块（hub.module），不为空
  customerName       客户名称（Customer.display_name / company）
  reporter/phone/telephone/email  主源工单 reporter.{name,mobile,tel,email}
  handleUser         处理人姓名（模块研发责任人，查不到回落 assigned_user.name）
  handleSteps        KSM 主源工单节点串（非 KSM 为空）
  feishuUrl          本系统工单详情链接 {hub_public_base_url}/tickets/{主源工单 id}
  ticketId/ticketNo  主源工单 source_ticket_id / short_code
  ticketSource       来源中文名（KSM / 智齿）
  transferType/operate  按 hub.type 固定文案
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from adapters.linear import LinearWebhookClient, LinearWebhookConfig
from app.api.ksm_nodes import parse_ksm_nodes
from app.config import get_settings
from app.core.logging import get_logger
from app.models import Customer, CustomerIdentity, HubIssue, ProductLine, Ticket, User
from app.services.hub_issues.module_owner import resolve_module_owner

logger = get_logger(__name__)

# hub.priority → webhook priority 文案（对方系统按中文/英文皆可，这里用中文优先级）
_PRIORITY_ZH = {
    "critical": "紧急",
    "high": "高",
    "medium": "中",
    "low": "低",
    "lowest": "低",
}

# source_code → 工单来源中文名（当前分为 智齿 / KSM）
_SOURCE_ZH = {
    "ksm": "KSM",
    "zhichi": "智齿",
    "zammad": "Zammad",
    "feishu_ai": "飞书AI",
    "ai_cs": "AI客服",
}

_TRANSFER_TEXT = {
    "Bug_fix": "BUG转产研修改工单状态及提单类型",
    "Demand": "需求转产研修改工单状态及提单类型",
}
_TRANSFER_TYPE = {"Bug_fix": "BUG转产研", "Demand": "需求转产研"}


@dataclass(slots=True, frozen=True)
class WebhookPushResult:
    hub_issue_id: int
    ok: bool
    response: dict[str, Any]


def _primary_source_ticket(db: Session, hub: HubIssue) -> Ticket | None:
    """主源工单：第一条关联的、有来源的工单（Child 无来源，靠 order_by id 取最早的有源单）。"""
    return (
        db.query(Ticket)
        .filter(
            Ticket.hub_issue_id == hub.id,
            Ticket.deleted_at.is_(None),
            Ticket.source_code.isnot(None),
        )
        .order_by(Ticket.id)
        .first()
    )


def _customer_name(db: Session, ticket: Ticket | None) -> str:
    if ticket is None or ticket.customer_identity_id is None:
        return ""
    identity = db.get(CustomerIdentity, ticket.customer_identity_id)
    if identity is None:
        return ""
    customer = db.get(Customer, identity.customer_id)
    if customer is None:
        return identity.raw_name or ""
    return customer.display_name or customer.company or identity.raw_name or ""


def _product_line_name(db: Session, code: str | None) -> str:
    if not code:
        return ""
    pl = db.query(ProductLine).filter(ProductLine.code == code).first()
    return pl.name if pl else code


def _handle_steps_text(ticket: Ticket | None) -> str:
    """KSM 主源工单的处理节点串成一段文本；非 KSM / 无节点 → 空串。"""
    if ticket is None or ticket.source_code != "ksm":
        return ""
    nodes = parse_ksm_nodes(ticket.source_payload)
    lines = []
    for n in nodes:
        seg = f"{n.handled_at or ''} {n.node_name} [{n.handler_name}]"
        if n.content:
            seg += f": {n.content}"
        lines.append(seg.strip())
    return "\n".join(lines)


def _feishu_url(ticket: Ticket | None) -> str:
    settings = get_settings()
    base = (settings.hub_public_base_url or "").rstrip("/")
    if not base or ticket is None:
        return ""
    return f"{base}/tickets/{ticket.id}"


def _reporter_field(ticket: Ticket | None, key: str) -> str:
    if ticket is None or not isinstance(ticket.reporter, dict):
        return ""
    return str(ticket.reporter.get(key) or "")


def build_webhook_fields(db: Session, hub: HubIssue) -> dict[str, Any]:
    """组装 webhook `fields`。所有值转为字符串（缺失→空串），对方系统按字符串接收。"""
    settings = get_settings()
    src = _primary_source_ticket(db, hub)

    # handleUser：转研发责任人 = 模块研发责任人（assignment_scopes_module，按当前
    # 产品线+模块查），查不到回落入库责任人（hub.assigned_user_id）。
    assignee_name = ""
    owner = resolve_module_owner(db, hub.product_line_code, hub.module)
    if owner is not None:
        assignee_name = owner.name or ""
    elif hub.assigned_user_id is not None:
        assignee = db.get(User, hub.assigned_user_id)
        if assignee is not None:
            assignee_name = assignee.name or ""

    product_line = settings.linear_webhook_default_product_line
    product_category = _product_line_name(db, hub.product_line_code)

    return {
        "title": hub.title or "",
        "description": hub.canonical_body or "",
        "ticketSource": _SOURCE_ZH.get(src.source_code or "", src.source_code or "") if src else "",
        "priority": _PRIORITY_ZH.get(hub.priority or "", ""),
        "ticketType": hub.type,
        "ticketId": (src.source_ticket_id or "") if src else "",
        "ticketNo": (src.short_code or "") if src else "",
        "customerName": _customer_name(db, src),
        "tenantName": (src.reporter_tenant or "") if src else "",
        "productLine": product_line,
        "productModule": hub.product or "",
        "productCategory": product_category,
        "productIssueModule": hub.module or "",
        "transferType": _TRANSFER_TYPE.get(hub.type, ""),
        "subCategory": "",
        "reporter": _reporter_field(src, "name"),
        "phone": _reporter_field(src, "mobile"),
        "telephone": _reporter_field(src, "tel"),
        "email": _reporter_field(src, "email"),
        "handleUser": assignee_name,
        "handleSteps": _handle_steps_text(src),
        "feishuUrl": _feishu_url(src),
        "handleDescription": hub.canonical_body or "",
        "operate": _TRANSFER_TEXT.get(hub.type, ""),
    }


def push_hub_issue_to_webhook(
    db: Session,
    hub: HubIssue,
    *,
    client: LinearWebhookClient | None = None,
) -> WebhookPushResult:
    """POST hub 到飞书 webhook。失败抛异常（由 linear_push 统一转 pending）。"""
    settings = get_settings()
    fields = build_webhook_fields(db, hub)

    owns_client = client is None
    if client is None:
        client = LinearWebhookClient(LinearWebhookConfig.from_settings(settings))
    try:
        resp = client.send_ticket(fields)
    finally:
        if owns_client:
            client.close()

    logger.info(
        "linear_webhook_push_ok",
        hub_issue_id=hub.id,
        type=hub.type,
        ticket_no=fields.get("ticketNo"),
    )
    return WebhookPushResult(hub_issue_id=hub.id, ok=True, response=resp)
