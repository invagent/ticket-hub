"""KSMIngester — webhook → ticket pipeline.

Pipeline:
    1. Validate payload has billId + minimal fields
    2. Idempotency: if (source_code='ksm', source_ticket_id=billId) already exists,
       return the existing ticket (no-op write)
    3. Resolve customer identity via IdentityResolver
    4. Insert tickets row (type='Raw', status='received', short_code=TKT-NNNNNN)
    5. Apply Router → set assigned_user_id (skip on multi_match; D3 wires Conflict Detect)
    6. Write status_history (None → 'received')
    7. Return IngestResult

Caller (webhook endpoint) commits the transaction.

D1 scope: only the deterministic flow. Splitting (Conflict Detect → Parent/Child)
lands in D3 along with Agent integration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.logging import get_logger
from app.core.storage.minio_store import classify_attachment_kind, filename_from_url
from app.models import Attachment, HubIssue, SlaLevel, Ticket
from app.repositories.status_history import StatusHistoryRepository
from app.repositories.ticket import TicketRepository
from app.services.dispatch import dispatch_handler
from app.services.hub_issues.op_status import (
    OP_ANSWERED,
    OP_PROCESSING,
    OP_SUPPLEMENTING,
    OP_TRANSFERRED_RETURN,
    apply_op_status,
    resolve_op_handler,
)
from app.services.identity.resolver import IdentityInput, IdentityResolver
from app.services.ingest.catalog_upsert import safe_product_line_code, upsert_catalog
from app.services.ingest.content_refresh import apply_content_refresh

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class IngestResult:
    ticket_id: int
    short_code: str
    customer_id: int
    customer_identity_id: int
    routing_decision: str  # 'assigned' | 'multi_match' | 'default_pool'
    assigned_user_ids: list[int] = field(default_factory=list)
    deduped: bool = False  # True if (source, source_ticket_id) already existed


class IngestError(Exception):
    """Validation failure (missing required fields, etc.)."""


# KSM serviceLevel 对应中文服务等级名称（存量与兜底映射表，与 0032/0053 迁移及 /admin/sla 一致）
_KSM_SLA_FALLBACK: dict[str, str] = {
    "50": "战略客户绿色通道",
    "22": "标准成功服务（2023版）",
    "19": "标准成功服务",
    "54": "高级成功服务（含定制开发维）",
    "55": "高级成功服务（2023版）",
    "52": "高级成功服务（仅工单）",
    "10": "服务期外",
}


class KSMIngester:
    """Stateless. One per request; constructor params injected for testing."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._tickets = TicketRepository(db)
        self._history = StatusHistoryRepository(db)
        self._resolver = IdentityResolver(db)

    def _resolve_service_level(self, code: Any) -> str | None:
        """KSM serviceLevel code → 服务等级中文名称。
        优先查数据库 sla_levels 表（与管理后台配置动态对齐），回落至内置字典。"""
        if code is None:
            return None
        code_str = str(code).strip()
        if not code_str:
            return None

        # 1. 优先查 sla_levels 表
        try:
            row = (
                self._db.execute(
                    select(SlaLevel.name).where(
                        or_(
                            SlaLevel.code == code_str,
                            SlaLevel.source_system_code == code_str,
                            SlaLevel.name == code_str,
                        ),
                        or_(SlaLevel.source_system == "KSM", SlaLevel.source_system.is_(None)),
                    )
                )
                .scalars()
                .first()
            )
            if row:
                return row
        except Exception:
            logger.warning("failed_to_resolve_service_level_from_db", code=code_str)

        # 2. 内置字典兜底（离线/单测等未跑迁移环境）
        if code_str in _KSM_SLA_FALLBACK:
            return _KSM_SLA_FALLBACK[code_str]
        if code_str in _KSM_SLA_FALLBACK.values():
            return code_str

        # 3. 未知 code 原样保留
        return code_str

    # ---- public --------------------------------------------------------

    def ingest(self, payload: dict[str, Any]) -> IngestResult:
        bill_id = self._require_str(payload, "billId")

        # 1. Idempotency: skip if already ingested
        existing = self._tickets.find_by_source("ksm", bill_id)
        if existing is not None:
            # 重推（补料/驳回/状态推进/关单）：KSM 侧状态列均可能已变，先同步刷新，
            # 不受下面 op_status 分支影响——即便落到纯 no-op 分支（如工单关闭）也
            # 要把关单节点/接收状态等最新值落库，否则关单信息永远拿不到。
            self._sync_ksm_fields(existing, payload)
            hub = self._db.get(HubIssue, existing.hub_issue_id) if existing.hub_issue_id else None
            op = hub.op_status if hub is not None and hub.deleted_at is None else None

            # 若 KSM 推送携带结案状态（sourceStatus=4 或 status=4），
            # 属于答复后的关单状态同步或客户评价关单，绝不作为客户驳回重新打开工单
            is_ksm_closed = str(payload.get("sourceStatus") or payload.get("status") or "") == "4"
            if is_ksm_closed:
                logger.info(
                    "ksm_ingest_closed_sync", bill_id=bill_id, existing_ticket_id=existing.id
                )
                return self._dedup_result(existing)

            if op == OP_SUPPLEMENTING or existing.status == "supplementing":
                # 客户补料重推同 billId：content_refresh 刷内容 + 建新附件行，并把
                # 工单转回 processing/agent（Operation 类交 AI 重答，研发类恢复处理中）。
                apply_content_refresh(self._db, existing, payload)
                if existing.status == "supplementing":
                    prev = existing.status
                    existing.status = "processing"
                    self._history.record(
                        entity_type="ticket",
                        entity_id=existing.id,
                        from_status=prev,
                        to_status="processing",
                        changed_by="system:ksm_ingest",
                        reason="客户补料回流，工单重新进入处理中",
                    )
                if hub is not None and hub.type == "Operation":
                    apply_op_status(
                        self._db,
                        hub,
                        to_status=OP_PROCESSING,
                        handler="agent",
                        reason="客户补料回流，交回 AI 重答",
                    )
                logger.info(
                    "ksm_ingest_supplement_reopen", bill_id=bill_id, existing_ticket_id=existing.id
                )
                return self._dedup_result(existing)
            if op == OP_ANSWERED:
                # 驳回：已答复但客户不满意重提同一单。更新内容 + reject_count+1，
                # op_status 转回 processing 交主管人工介入。上一轮答复关单回写
                # 成功时 ticket.status 已被置 closed（见 writeback._close_local）；
                # 这里 hub 重新进入处理中，ticket 必须跟着 reopen，否则会出现
                # ticket=closed 但 hub.op_status=processing 的矛盾态（工单列表
                # 展示为已关闭，实际还在处理）。
                assert hub is not None
                apply_content_refresh(self._db, existing, payload)
                hub.reject_count += 1
                apply_op_status(
                    self._db,
                    hub,
                    to_status=OP_PROCESSING,
                    handler=resolve_op_handler(self._db, hub, get_settings()),
                    reason=f"客户驳回（第{hub.reject_count}次）",
                )
                if existing.status in ("closed", "done"):
                    prev = existing.status
                    existing.status = "processing"
                    self._history.record(
                        entity_type="ticket",
                        entity_id=existing.id,
                        from_status=prev,
                        to_status="processing",
                        changed_by="system:ksm_ingest",
                        reason=f"客户驳回（第{hub.reject_count}次），重新打开工单",
                    )
                logger.info(
                    "ksm_ingest_reject",
                    bill_id=bill_id,
                    existing_ticket_id=existing.id,
                    reject_count=hub.reject_count,
                )
                return self._dedup_result(existing)

            # 转单退回(transferred_return)后，客户/上游在 KSM 调整模块或重新分派回流
            is_reopened_from_returned = (
                existing.status == "transferred_return"
                or op == OP_TRANSFERRED_RETURN
                or (hub is not None and hub.status == "returned")
            )
            if is_reopened_from_returned:
                # 客户调整模块/重新分派回流（此时 KSM 状态非结案）：
                apply_content_refresh(self._db, existing, payload)
                raw_plc = payload.get("productLineCode") or payload.get("product_line")
                raw_mod = payload.get("moduleName") or payload.get("module")
                raw_feat = payload.get("featureName") or payload.get("feature")
                if raw_mod:
                    existing.module = raw_mod
                if raw_feat:
                    existing.feature = raw_feat
                if raw_plc:
                    existing.product_line_code = safe_product_line_code(self._db, raw_plc)
                upsert_catalog(self._db, product_line_code=raw_plc, module=raw_mod)

                prev_ticket_status = existing.status
                existing.status = "processing"
                existing.process_stage = "服务处理"
                self._history.record(
                    entity_type="ticket",
                    entity_id=existing.id,
                    from_status=prev_ticket_status,
                    to_status="processing",
                    changed_by="system:ksm_ingest",
                    reason="转单退回后客户变更模块重推，重新接入处理中",
                )

                if hub is not None:
                    prev_hub_status = hub.status
                    hub.status = "draft"
                    hub.linear_uuid = None
                    hub.linear_identifier = None
                    hub.linear_status = None
                    hub.linear_status_synced_at = None
                    if existing.module:
                        hub.module = existing.module
                    if existing.product_line_code:
                        hub.product_line_code = existing.product_line_code
                    if hub.type == "Operation":
                        apply_op_status(
                            self._db,
                            hub,
                            to_status=OP_PROCESSING,
                            handler=resolve_op_handler(self._db, hub, get_settings()),
                            reason="转单退回后客户变更模块重推，任务重置为处理中",
                        )
                    else:
                        hub.op_status = None
                    self._history.record(
                        entity_type="hub_issue",
                        entity_id=hub.id,
                        from_status=prev_hub_status,
                        to_status="draft",
                        changed_by="system:ksm_ingest",
                        reason="转单退回后客户变更模块重推，任务重置为待确认",
                    )

                logger.info(
                    "ksm_ingest_reopened_from_returned",
                    bill_id=bill_id,
                    existing_ticket_id=existing.id,
                    module=existing.module,
                    product_line_code=existing.product_line_code,
                )
                return IngestResult(
                    ticket_id=existing.id,
                    short_code=existing.short_code,
                    customer_id=(existing.customer_identity_id and self._customer_id_of(existing))
                    or 0,
                    customer_identity_id=existing.customer_identity_id or 0,
                    routing_decision="reopened_transferred_return",
                    assigned_user_ids=[existing.assigned_user_id]
                    if existing.assigned_user_id
                    else [],
                    deduped=False,
                )

            # closed（硬终态）/ 其他（未毕业 hub / 研发类无 op_status）→ 原 no-op
            logger.info("ksm_ingest_dedup", bill_id=bill_id, existing_ticket_id=existing.id)
            return self._dedup_result(existing)

        # 2. Resolve customer identity
        identity_input = self._extract_identity(payload)
        resolve = self._resolver.resolve(identity_input)

        # 3. Ensure product_line + module exist (auto-create if unknown)
        upsert_catalog(
            self._db,
            product_line_code=payload.get("productLineCode") or payload.get("product_line"),
            module=payload.get("moduleName") or payload.get("module"),
        )

        # 4. Create ticket (type=Raw, status=received)
        short_code = self._tickets.next_short_code()
        is_already_closed = str(payload.get("sourceStatus") or "") == "4"
        initial_status = "closed" if is_already_closed else "processing"
        initial_stage = "完成" if is_already_closed else "服务处理"
        ticket = Ticket(
            short_code=short_code,
            source_code="ksm",
            source_ticket_id=bill_id,
            source_ticket_number=payload.get("billNumber"),
            type="Raw",
            status=initial_status,
            process_stage=initial_stage,
            source_payload=payload,
            customer_identity_id=resolve.customer_identity_id,
            product_line_code=safe_product_line_code(
                self._db, payload.get("productLineCode") or payload.get("product_line")
            ),
            module=payload.get("moduleName") or payload.get("module"),
            feature=payload.get("featureName") or payload.get("feature"),
            title=payload.get("title"),
            body=payload.get("content") or payload.get("description"),
            reporter={
                "name": payload.get("accountName"),
                "email": payload.get("email"),
                "mobile": payload.get("mobile"),
                "tel": payload.get("tel"),
                "source_user_id": payload.get("account"),
            },
            reporter_company=payload.get("reporterCompany"),
            service_level=self._resolve_service_level(
                payload.get("serviceLevel") or payload.get("service_level")
            ),
        )
        self._sync_ksm_fields(ticket, payload)
        self._tickets.add(ticket)

        # 4. Dispatch（按来源+规则派单，取代旧 Router 按产品线/模块路由——分派提前到
        # 产品线/模块判定之前，处理人=责任人，triage/module_resolve 之后不再改写）
        self._db.flush()  # dispatch_handler.add_log 需要 ticket.id 已落库
        dr = dispatch_handler(self._db, ticket)
        if dr.user_id is not None:
            ticket.assigned_user_id = dr.user_id
            ticket.handler_user_id = dr.user_id  # 处理人初始=责任人
        # 无匹配规则/无可用处理人 → leave None; 主管经「仅未分配」筛选后人工归属

        self._db.flush()

        # 4b. 建附件行（仅建行，不下载；下载+OCR 由异步流水线处理）。
        # KSM 附件真实文件名在 name（url 是下载动作端点，尾段是 accessory!download.action，
        # 不能当文件名）；kind 用真实文件名判扩展名——只 image 进 OCR。
        # 新格式 attachments=[{url,name}]；兼容 full-payload 旧格式 attachment_urls=[str]。
        atts = payload.get("attachments")
        if not atts:
            atts = [{"url": u, "name": None} for u in (payload.get("attachment_urls") or [])]
        for a in atts:
            url = a.get("url")
            if not url:
                continue
            name = a.get("name")
            self._db.add(
                Attachment(
                    ticket_id=ticket.id,
                    source_url=url,
                    filename=name or filename_from_url(url),
                    kind=classify_attachment_kind(name or url),
                    vision_status="queued",
                )
            )

        dispatch_decision = "assigned" if dr.user_id is not None else "no_match"

        # 5. Status history
        self._history.record(
            entity_type="ticket",
            entity_id=ticket.id,
            from_status=None,
            to_status=initial_status,
            changed_by="system:ingest",
            reason=f"ksm webhook: {bill_id}"
            + (" (KSM已处理完成关单)" if is_already_closed else ""),
            metadata={
                "source": "ksm",
                "routing_decision": dispatch_decision,
                "matched_scope": "dispatch_rule" if dr.rule_id is not None else "none",
                "rationale": dr.reason,
            },
        )

        logger.info(
            "ksm_ingest_committed",
            ticket_id=ticket.id,
            short_code=short_code,
            customer_id=resolve.customer_id,
            routing_decision=dispatch_decision,
        )

        return IngestResult(
            ticket_id=ticket.id,
            short_code=short_code,
            customer_id=resolve.customer_id,
            customer_identity_id=resolve.customer_identity_id,
            routing_decision=dispatch_decision,
            assigned_user_ids=[dr.user_id] if dr.user_id is not None else [],
            deduped=False,
        )

    # ---- internal ------------------------------------------------------

    def _sync_ksm_fields(self, ticket: Ticket, payload: dict[str, Any]) -> None:
        """把 KSM 重推payload 里的状态类字段刷到已存在 ticket——接收状态/关单节点
        随工单流转变化，每次重推都要跟最新值同步，不止首次入库写一次。"""
        ticket.source_status = payload.get("sourceStatus")
        ticket.ksm_reporter_product_line = payload.get("reporterProductLine")
        ticket.ksm_reporter_module = payload.get("reporterModule")
        ticket.ksm_linkman = payload.get("linkman")
        ticket.ksm_contact_mobile = payload.get("contactMobile")
        ticket.ksm_contact_email = payload.get("contactEmail")
        ticket.ksm_close_node_id = payload.get("closeNodeId")
        ticket.ksm_close_node_name = payload.get("closeNodeName")
        ticket.ksm_close_node_status = payload.get("closeNodeStatus")
        raw_sl = payload.get("serviceLevel") or payload.get("service_level")
        if raw_sl is not None:
            resolved_sl = self._resolve_service_level(raw_sl)
            if resolved_sl:
                ticket.service_level = resolved_sl

    @staticmethod
    def _require_str(payload: dict[str, Any], key: str) -> str:
        v = payload.get(key)
        if not isinstance(v, str) or not v:
            raise IngestError(f"missing or non-string {key}")
        return v

    @staticmethod
    def _extract_identity(payload: dict[str, Any]) -> IdentityInput:
        return IdentityInput(
            source_code="ksm",
            source_user_id=payload.get("account"),
            erp_uid=payload.get("erpUid") or payload.get("erp_uid"),
            email=payload.get("email"),
            mobile=payload.get("mobile"),
            raw_name=payload.get("accountName") or payload.get("linkman"),
            raw_payload=payload,
        )

    def _customer_id_of(self, ticket: Ticket) -> int:
        """Resolve a ticket's customer_id via its customer_identity (for dedup result)."""
        if ticket.customer_identity_id is None:
            return 0
        from app.models import CustomerIdentity

        ident = self._db.get(CustomerIdentity, ticket.customer_identity_id)
        return ident.customer_id if ident else 0

    def _dedup_result(self, existing: Ticket) -> IngestResult:
        """已存在 ticket 的返回结果（重复心跳与补料回填共用；字段全取自 existing）。"""
        return IngestResult(
            ticket_id=existing.id,
            short_code=existing.short_code,
            customer_id=(existing.customer_identity_id and self._customer_id_of(existing)) or 0,
            customer_identity_id=existing.customer_identity_id or 0,
            routing_decision="dedup",
            assigned_user_ids=[existing.assigned_user_id] if existing.assigned_user_id else [],
            deduped=True,
        )
