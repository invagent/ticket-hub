"""PortalIngester — 产品内提单（source_code='embedded'）入库（ADR-0017 D4）。

与 zammad_ingester 同一剧本：identity → upsert_catalog → Ticket(Raw/received)
→ dispatch_handler（只写 handler_user_id）→ status_history。差别：
- 没有外部 webhook 载荷，字段由门户接口校验后直接传入；
- source_ticket_id = f"{tenant_code}:{uuid4}"（唯一、可回查）；
- 落 tickets.tenant_user_id 供门户行级隔离；
- 之后与其它来源一样交给 run_post_ingest_agents 走完整 AI 链（不新增 Agent 环节）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import Tenant, TenantUser, Ticket
from app.repositories.status_history import StatusHistoryRepository
from app.repositories.ticket import TicketRepository
from app.services.dispatch import dispatch_handler
from app.services.identity.resolver import IdentityInput, IdentityResolver
from app.services.ingest.catalog_upsert import safe_product_line_code, upsert_catalog

logger = get_logger(__name__)

SOURCE_CODE = "embedded"


@dataclass(slots=True, frozen=True)
class PortalIngestResult:
    ticket_id: int
    short_code: str
    handler_user_id: int | None
    routing_decision: str


class PortalIngester:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._tickets = TicketRepository(db)
        self._history = StatusHistoryRepository(db)
        self._resolver = IdentityResolver(db)

    def ensure_identity(self, tenant: Tenant, tu: TenantUser) -> int:
        """租户用户 → customer_identities（幂等：同 (source, source_user_id) 复用）。"""
        if tu.customer_identity_id is not None:
            return tu.customer_identity_id
        res = self._resolver.resolve(
            IdentityInput(
                source_code=SOURCE_CODE,
                source_user_id=f"{tenant.code}:{tu.external_uid}",
                source_custom_id=tenant.code,
                email=tu.email or None,
                mobile=tu.mobile or None,
                raw_name=tu.name or None,
                raw_payload={"tenant_code": tenant.code, "external_uid": tu.external_uid},
            )
        )
        tu.customer_identity_id = res.customer_identity_id
        return res.customer_identity_id

    def ingest(
        self,
        *,
        tenant: Tenant,
        tenant_user: TenantUser,
        title: str,
        body: str,
        product_line_code: str | None = None,
        module: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> PortalIngestResult:
        identity_id = self.ensure_identity(tenant, tenant_user)
        plc = product_line_code or tenant.default_product_line_code
        upsert_catalog(self._db, product_line_code=plc, module=module)

        source_ticket_id = f"{tenant.code}:{uuid.uuid4().hex}"
        short_code = self._tickets.next_short_code()
        ticket = Ticket(
            short_code=short_code,
            source_code=SOURCE_CODE,
            source_ticket_id=source_ticket_id,
            type="Raw",
            status="received",
            source_payload={
                "embedded": {
                    "tenant_code": tenant.code,
                    "external_uid": tenant_user.external_uid,
                    **(extra or {}),
                }
            },
            customer_identity_id=identity_id,
            tenant_user_id=tenant_user.id,
            product_line_code=safe_product_line_code(self._db, plc),
            module=module or None,
            title=title.strip(),
            body=body.strip(),
            reporter={
                "name": tenant_user.name,
                "email": tenant_user.email,
                "mobile": tenant_user.mobile,
                "source_user_id": tenant_user.external_uid,
            },
            reporter_tenant=tenant.name,
        )
        self._tickets.add(ticket)
        self._db.flush()

        dr = dispatch_handler(self._db, ticket)
        if dr.user_id is not None:
            ticket.handler_user_id = dr.user_id  # ADR-0017 D3：只写处理人
        decision = "assigned" if dr.user_id is not None else "no_match"
        self._db.flush()

        self._history.record(
            entity_type="ticket",
            entity_id=ticket.id,
            from_status=None,
            to_status="received",
            changed_by="system:ingest",
            reason=f"产品内提单：{tenant.name} / {tenant_user.name or tenant_user.external_uid}",
            metadata={
                "source": SOURCE_CODE,
                "tenant_code": tenant.code,
                "routing_decision": decision,
                "matched_scope": "dispatch_rule" if dr.rule_id is not None else "none",
                "rationale": dr.reason,
            },
        )
        logger.info(
            "portal_ingest_committed",
            ticket_id=ticket.id,
            short_code=short_code,
            tenant=tenant.code,
            routing_decision=decision,
        )
        return PortalIngestResult(
            ticket_id=ticket.id,
            short_code=short_code,
            handler_user_id=dr.user_id,
            routing_decision=decision,
        )
