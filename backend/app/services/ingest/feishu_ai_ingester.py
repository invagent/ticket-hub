"""FeishuAiIngester — 飞书AI 工单入库（复用 ai_cs 载荷契约，走标准 triage 链）.

与 EscalationIngester 的关系：**请求参数形状完全一致**（直接复用
`parse_escalation_payload`），差别只在入库后链路——本来源的工单由 webhook 挂
`run_post_ingest_agents`（triage：classify + 混合单拆分 + 标准毕业门槛），
而非 escalation 的 `run_escalation_agents`（黄金三元组二次分类）。

因此三元组（ai_answer / dissatisfaction）在这里只是**存档**（写进
source_payload['ai_cs'] 供审计/回查），triage 下游用 ticket.body 分类，不读它。
去重域：(source='feishu_ai', source_ticket_id=session_id)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import Attachment, Ticket
from app.repositories.status_history import StatusHistoryRepository
from app.repositories.ticket import TicketRepository
from app.services.identity.resolver import IdentityInput, IdentityResolver
from app.services.ingest.catalog_upsert import safe_product_line_code, upsert_catalog

# 复用 ai_cs 的载荷解析层（参数形状完全一致）。IngestError 显式 re-export，
# 供 webhook 层 catch（与 escalation_ingester.IngestError 是同一个异常类）。
from app.services.ingest.escalation_ingester import IngestError as IngestError
from app.services.ingest.escalation_ingester import parse_escalation_payload
from app.services.routing.router import Router, RouteRequest

logger = get_logger(__name__)

_SOURCE = "feishu_ai"
_TITLE_MAX = 120


@dataclass(slots=True, frozen=True)
class IngestResult:
    ticket_id: int
    short_code: str
    routing_decision: str
    assigned_user_ids: list[int] = field(default_factory=list)
    attachment_ids: list[int] = field(default_factory=list)
    deduped: bool = False


class FeishuAiIngester:
    def __init__(self, db: Session, *, default_pool_user_id: int | None = None) -> None:
        self._db = db
        self._tickets = TicketRepository(db)
        self._history = StatusHistoryRepository(db)
        self._resolver = IdentityResolver(db)
        self._router = Router(db, default_pool_user_id=default_pool_user_id)

    def ingest(self, payload: dict[str, Any]) -> IngestResult:
        p = parse_escalation_payload(payload)

        existing = self._tickets.find_by_source(_SOURCE, p.session_id)
        if existing is not None:
            logger.info("feishu_ai_ingest_dedup", session_id=p.session_id, ticket_id=existing.id)
            return IngestResult(
                ticket_id=existing.id,
                short_code=existing.short_code,
                routing_decision="dedup",
                assigned_user_ids=[existing.assigned_user_id] if existing.assigned_user_id else [],
                deduped=True,
            )

        resolve = self._resolver.resolve(
            IdentityInput(
                source_code=_SOURCE,
                source_user_id=p.customer.get("source_user_id") or p.customer.get("erp_uid"),
                erp_uid=p.customer.get("erp_uid"),
                email=p.customer.get("email"),
                mobile=p.customer.get("mobile"),
                raw_name=p.customer.get("name"),
            )
        )
        upsert_catalog(self._db, product_line_code=p.product_line_code, module=p.module)

        # 三元组仅存档（triage 用 ticket.body 分类，不读此块）。conversation/
        # cited_knowledge/skills_used 缺省时不写 key，保持载荷形状与 ai_cs 一致。
        ai_cs_ctx: dict[str, Any] = {
            "original_question": p.original_question,
            "ai_answer": p.ai_answer,
            "dissatisfaction": p.dissatisfaction,
        }
        if p.conversation:
            ai_cs_ctx["conversation"] = p.conversation
        if p.cited_knowledge:
            ai_cs_ctx["cited_knowledge"] = p.cited_knowledge
        if p.skills_used:
            ai_cs_ctx["skills_used"] = p.skills_used

        ticket = Ticket(
            short_code=self._tickets.next_short_code(),
            source_code=_SOURCE,
            source_ticket_id=p.session_id,
            type="Raw",
            status="received",
            source_payload={"ai_cs": ai_cs_ctx},
            customer_identity_id=resolve.customer_identity_id,
            product_line_code=safe_product_line_code(self._db, p.product_line_code),
            module=p.module,
            title=p.original_question[:_TITLE_MAX],
            body=p.original_question,
            reporter={
                "name": p.customer.get("name"),
                "email": p.customer.get("email"),
                "mobile": p.customer.get("mobile"),
                "source_user_id": p.customer.get("source_user_id"),
            },
        )
        self._tickets.add(ticket)

        route = self._router.route(
            RouteRequest(
                ticket_id=ticket.id,
                source_code=_SOURCE,
                product_line_code=ticket.product_line_code,
                raw_module=ticket.module,
                customer_id=resolve.customer_id,
            )
        )
        if (route.decision == "assigned" and len(route.assigned_user_ids) == 1) or (
            route.decision == "default_pool" and route.assigned_user_ids
        ):
            ticket.assigned_user_id = route.assigned_user_ids[0]
            ticket.handler_user_id = ticket.assigned_user_id  # 处理人初始=责任人
        self._db.flush()

        attachment_ids: list[int] = []
        for a in p.attachments:
            url = a.get("url") or a.get("source_url")
            if not url:
                continue
            att = Attachment(
                ticket_id=ticket.id,
                source_url=str(url),
                filename=a.get("filename"),
                mime=a.get("mime"),
                kind="image",  # 截图为主；非图后续按 mime 细分
                vision_status="pending",
            )
            self._db.add(att)
            self._db.flush()
            attachment_ids.append(att.id)

        self._history.record(
            entity_type="ticket",
            entity_id=ticket.id,
            from_status=None,
            to_status="received",
            changed_by="system:ingest",
            reason=f"feishu_ai ingest: {p.session_id}",
            metadata={
                "source": _SOURCE,
                "routing_decision": route.decision,
                "attachment_count": len(attachment_ids),
            },
        )
        logger.info(
            "feishu_ai_ingest_committed",
            ticket_id=ticket.id,
            short_code=ticket.short_code,
            attachments=len(attachment_ids),
            routing_decision=route.decision,
        )
        return IngestResult(
            ticket_id=ticket.id,
            short_code=ticket.short_code,
            routing_decision=route.decision,
            assigned_user_ids=route.assigned_user_ids,
            attachment_ids=attachment_ids,
            deduped=False,
        )
