"""ZhichiIngester — parallel of KSMIngester for 智齿 webhook payloads.

Field mapping diffs vs KSM:
  - billId        → ticketid
  - account       → customerid (Zhichi customer ID)
  - accountName   → name
  - email/mobile  → same shape, may live under nested `customer` block
  - moduleName    → category / subcategory
  - productLineCode → product

Idempotency: dedupe by (source='zhichi', source_ticket_id=ticketid).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import Ticket
from app.repositories.status_history import StatusHistoryRepository
from app.repositories.ticket import TicketRepository
from app.services.identity.resolver import IdentityInput, IdentityResolver
from app.services.ingest.catalog_upsert import upsert_catalog
from app.services.routing.router import Router, RouteRequest

logger = get_logger(__name__)


def _parse_extend_fields(raw: dict[str, Any]) -> dict[str, str]:
    """extend_fields_list → {field_name: value}。

    field_type=='6'（下拉列表）取 field_text，其余取 field_value（智齿契约）。
    """
    out: dict[str, str] = {}
    lst = raw.get("extend_fields_list")
    if not isinstance(lst, list):
        return out
    for f in lst:
        if not isinstance(f, dict):
            continue
        name = f.get("field_name")
        if not name:
            continue
        val = f.get("field_text") if str(f.get("field_type")) == "6" else f.get("field_value")
        if val:
            out[str(name)] = str(val)
    return out


def _flatten_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """智齿真实推送 {source, raw, fields} → 归一化扁平 dict。

    fields 中文块为主源（智齿已映射好），raw + extend_fields_list 兜底。
    无 raw/fields 时原样返回（向后兼容旧扁平格式）。
    整个原始信封挂 `_envelope`，供 source_payload 存档 + 出站回写读 raw。
    """
    raw_obj = payload.get("raw")
    fields_obj = payload.get("fields")
    if not isinstance(raw_obj, dict) and not isinstance(fields_obj, dict):
        return payload  # 旧扁平格式，原样
    raw = raw_obj if isinstance(raw_obj, dict) else {}
    fields = fields_obj if isinstance(fields_obj, dict) else {}
    ext = _parse_extend_fields(raw)

    def pick(*cands: Any) -> Any:
        for c in cands:
            if c:
                return c
        return None

    return {
        "ticketid": pick(fields.get("工单来源ID"), raw.get("ticketid")),
        "title": pick(fields.get("主题"), raw.get("ticket_title")),
        "content": pick(fields.get("问题描述"), raw.get("ticket_content")),
        "productLineCode": pick(fields.get("产品线"), ext.get("产品分类")),
        "moduleName": pick(fields.get("产品模块"), ext.get("产品分类")),
        "customer": {
            "name": pick(fields.get("联系人"), fields.get("反馈人"), ext.get("联系人")),
            "mobile": pick(fields.get("联系人手机"), fields.get("反馈人手机"), ext.get("联系手机")),
            "email": pick(fields.get("反馈人邮箱"), raw.get("user_emails")),
            "erp_uid": pick(fields.get("对接ERP"), ext.get("对接ERP")),
        },
        "company": pick(fields.get("客户名称"), raw.get("enterprise_name")),
        "_envelope": payload,  # 出站回写要用的原始信封整体
    }


@dataclass(slots=True, frozen=True)
class IngestResult:
    ticket_id: int
    short_code: str
    customer_id: int
    customer_identity_id: int
    routing_decision: str
    assigned_user_ids: list[int] = field(default_factory=list)
    deduped: bool = False


class IngestError(Exception):
    """Validation failure."""


class ZhichiIngester:
    def __init__(self, db: Session, *, default_pool_user_id: int | None = None) -> None:
        self._db = db
        self._tickets = TicketRepository(db)
        self._history = StatusHistoryRepository(db)
        self._resolver = IdentityResolver(db)
        self._router = Router(db, default_pool_user_id=default_pool_user_id)

    def ingest(self, payload: dict[str, Any]) -> IngestResult:
        payload = _flatten_envelope(payload)
        ticketid = self._require_str(payload, "ticketid")

        existing = self._tickets.find_by_source("zhichi", ticketid)
        if existing is not None:
            logger.info(
                "zhichi_ingest_dedup",
                ticketid=ticketid,
                existing_ticket_id=existing.id,
            )
            return IngestResult(
                ticket_id=existing.id,
                short_code=existing.short_code,
                customer_id=self._customer_id_of(existing),
                customer_identity_id=existing.customer_identity_id or 0,
                routing_decision="dedup",
                assigned_user_ids=(
                    [existing.assigned_user_id] if existing.assigned_user_id else []
                ),
                deduped=True,
            )

        identity_input = self._extract_identity(payload)
        resolve = self._resolver.resolve(identity_input)

        # Ensure product_line and module rows exist (auto-create if new)
        upsert_catalog(
            self._db,
            product_line_code=payload.get("productLineCode") or payload.get("product"),
            module=payload.get("moduleName")
            or payload.get("category")
            or payload.get("subcategory"),
        )

        short_code = self._tickets.next_short_code()
        ticket = Ticket(
            short_code=short_code,
            source_code="zhichi",
            source_ticket_id=ticketid,
            type="Raw",
            status="received",
            source_payload=payload.get("_envelope") or payload,
            customer_identity_id=resolve.customer_identity_id,
            product_line_code=payload.get("productLineCode") or payload.get("product"),
            module=payload.get("moduleName")
            or payload.get("category")
            or payload.get("subcategory"),
            feature=payload.get("featureName") or payload.get("feature"),
            title=payload.get("title") or payload.get("ticket_title"),
            body=payload.get("content") or payload.get("ticket_content"),
            reporter={
                "name": _customer_field(payload, "name"),
                "email": _customer_field(payload, "email"),
                "mobile": _customer_field(payload, "mobile"),
                "source_user_id": payload.get("customerid")
                or _customer_field(payload, "customerid"),
            },
        )
        self._tickets.add(ticket)

        route = self._router.route(
            RouteRequest(
                ticket_id=ticket.id,
                source_code="zhichi",
                product_line_code=ticket.product_line_code,
                raw_module=ticket.module,
                raw_feature=ticket.feature,
                customer_id=resolve.customer_id,
            )
        )
        if (route.decision == "assigned" and len(route.assigned_user_ids) == 1) or (
            route.decision == "default_pool" and route.assigned_user_ids
        ):
            ticket.assigned_user_id = route.assigned_user_ids[0]

        self._db.flush()

        self._history.record(
            entity_type="ticket",
            entity_id=ticket.id,
            from_status=None,
            to_status="received",
            changed_by="system:ingest",
            reason=f"zhichi webhook: {ticketid}",
            metadata={
                "source": "zhichi",
                "routing_decision": route.decision,
                "matched_scope": route.matched_scope,
                "rationale": route.rationale,
            },
        )

        logger.info(
            "zhichi_ingest_committed",
            ticket_id=ticket.id,
            short_code=short_code,
            customer_id=resolve.customer_id,
            routing_decision=route.decision,
        )
        return IngestResult(
            ticket_id=ticket.id,
            short_code=short_code,
            customer_id=resolve.customer_id,
            customer_identity_id=resolve.customer_identity_id,
            routing_decision=route.decision,
            assigned_user_ids=route.assigned_user_ids,
            deduped=False,
        )

    @staticmethod
    def _require_str(payload: dict[str, Any], key: str) -> str:
        v = payload.get(key)
        if not isinstance(v, str) or not v:
            raise IngestError(f"missing or non-string {key}")
        return v

    @staticmethod
    def _extract_identity(payload: dict[str, Any]) -> IdentityInput:
        return IdentityInput(
            source_code="zhichi",
            source_user_id=payload.get("customerid") or _customer_field(payload, "customerid"),
            erp_uid=payload.get("erp_uid") or _customer_field(payload, "erp_uid"),
            email=_customer_field(payload, "email"),
            mobile=_customer_field(payload, "mobile"),
            raw_name=_customer_field(payload, "name"),
            raw_payload=payload,
        )

    def _customer_id_of(self, ticket: Ticket) -> int:
        if ticket.customer_identity_id is None:
            return 0
        from app.models import CustomerIdentity

        ident = self._db.get(CustomerIdentity, ticket.customer_identity_id)
        return ident.customer_id if ident else 0


def _customer_field(payload: dict[str, Any], key: str) -> Any:
    """Zhichi nests customer info under `customer`; fall back to top-level."""
    cust = payload.get("customer")
    if isinstance(cust, dict) and cust.get(key):
        return cust.get(key)
    return payload.get(key)
