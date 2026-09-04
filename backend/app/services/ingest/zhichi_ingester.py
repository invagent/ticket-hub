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

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.storage.minio_store import classify_attachment_kind, filename_from_url
from app.models import Attachment, HubIssue, Ticket
from app.repositories.status_history import StatusHistoryRepository
from app.repositories.ticket import TicketRepository
from app.services.dispatch import dispatch_handler
from app.services.hub_issues.creator import ensure_hub_issue_for_ticket
from app.services.hub_issues.op_status import OP_ANSWERED, OP_CLOSED, apply_op_status
from app.services.identity.resolver import IdentityInput, IdentityResolver
from app.services.ingest.catalog_upsert import safe_product_line_code, upsert_catalog

logger = get_logger(__name__)


_MAX_TITLE_LEN = 150
# 智齿在客户未填主题时自动生成的兜底标题：客户留言-<手机号>（- 半角 / — 破折号）
_FALLBACK_TITLE_RE = re.compile(r"^客户留言[-—]")
_TAG_RE = re.compile(r"<[^>]+>")
_ENTITIES = (("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"))


def _strip_html(s: str) -> str:
    """轻量去标签 + 常见实体 + 折叠空白（智齿内容是简单 <p>/<br> 段，够用）。"""
    text = _TAG_RE.sub(" ", s)
    for a, b in _ENTITIES:
        text = text.replace(a, b)
    return " ".join(text.split())


def _derive_title(raw_title: str | None, raw_content: str | None) -> str | None:
    """标题派生：
    - 正常人工标题 → 原样保留
    - 「客户留言-…」兜底标题 或 空 → 用去 HTML 的问题内容
    - 内容也空 → 退回原兜底标题（至少有手机号）
    最终一律截前 150 字符。
    """
    t = (raw_title or "").strip()
    is_fallback = not t or bool(_FALLBACK_TITLE_RE.match(t))
    if not is_fallback:
        return t[:_MAX_TITLE_LEN]
    content = _strip_html(raw_content or "").strip()
    if content:
        return content[:_MAX_TITLE_LEN]
    return (t or None) and t[:_MAX_TITLE_LEN]


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
        # 无信封：可能是智齿原生扁平推送（顶层 ticket_*/extend_fields_list，客户信息
        # 在 extend_fields_list 里），也可能是旧简化扁平格式（customer 块 + productLineCode）。
        # 原生格式的判别信号：有 extend_fields_list（原生必有）且无 customer 嵌套块。
        if (
            payload.get("ticketid")
            and isinstance(payload.get("extend_fields_list"), list)
            and not isinstance(payload.get("customer"), dict)
        ):
            return _flatten_native(payload)
        return payload  # 旧简化扁平格式，原样交给下游 legacy 分支
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
        "title": _derive_title(
            pick(fields.get("主题"), raw.get("ticket_title")),
            pick(fields.get("问题描述"), raw.get("ticket_content")),
        ),
        "content": pick(fields.get("问题描述"), raw.get("ticket_content")),
        "ticketStatus": raw.get("ticket_status"),  # fields 中文块无此字段，只能从 raw 拿
        "productLineCode": pick(fields.get("产品线"), ext.get("产品分类")),
        "moduleName": pick(fields.get("产品模块"), ext.get("产品分类")),
        "customer": {
            "name": pick(fields.get("联系人"), fields.get("反馈人"), ext.get("联系人")),
            "mobile": pick(fields.get("联系人手机"), fields.get("反馈人手机"), ext.get("联系手机")),
            "email": pick(fields.get("反馈人邮箱"), raw.get("user_emails")),
            "erp_uid": pick(fields.get("对接ERP"), ext.get("对接ERP")),
        },
        "company": pick(fields.get("客户名称"), raw.get("enterprise_name")),
        "tax_no": pick(fields.get("公司税号"), ext.get("公司税号")),  # 提单公司税号（扩展字段）
        "attachment_urls": _parse_file_str(pick(fields.get("附件"), raw.get("file_str"))),
        "_envelope": payload,  # 出站回写要用的原始信封整体
    }


def _parse_file_str(value: Any) -> list[str]:
    """智齿附件 file_str → URL 列表。可能是单个 URL 或逗号/空格/分号分隔的多个。"""
    if not value or not isinstance(value, str):
        return []
    parts = re.split(r"[,\s;]+", value.strip())
    return [u for u in parts if u.startswith("http")]


def _flatten_native(payload: dict[str, Any]) -> dict[str, Any]:
    """智齿原生扁平推送（顶层直接 ticket_*/user_*/extend_fields_list，无 raw/fields）
    → 归一化扁平 dict。字段来源见 docs/spec/2026-07-20-zhichi-real-payload-fix.md。

    整个原始 payload 挂 `_envelope`，供 source_payload 存档 + 出站回写读
    deal_agent_name / ticket_level。
    """
    ext = _parse_extend_fields(payload)
    return {
        "ticketid": payload.get("ticketid"),
        "title": _derive_title(payload.get("ticket_title"), payload.get("ticket_content")),
        "content": payload.get("ticket_content"),  # body 保留完整（含 HTML），只标题去 HTML
        "ticketStatus": payload.get("ticket_status"),
        "productLineCode": ext.get("产品分类"),
        "moduleName": ext.get("产品分类"),  # 智齿无独立模块，产品分类兼作模块
        "customer": {
            "name": ext.get("联系人"),
            "mobile": ext.get("联系手机") or payload.get("user_tels"),
            "email": payload.get("user_emails"),
            "erp_uid": ext.get("对接ERP"),
        },
        "customerid": payload.get("userid"),
        "company": payload.get("enterprise_name") or ext.get("公司/项目名称"),
        "tax_no": ext.get("公司税号"),  # 提单公司税号（扩展字段）
        "attachment_urls": _parse_file_str(payload.get("file_str")),
        "_envelope": payload,
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
    # True 仅在「全新工单入库即终态」路径：已直接毕业 Operation hub 并落
    # answered/closed，调用方（webhook）不应再调度 run_post_ingest_agents
    # 触发 triage 分类。已存在工单的终态同步走 dedup 分支，deduped=True 本就
    # 让调用方跳过 agent 链，不需要这个标志。
    skip_post_ingest: bool = False


class IngestError(Exception):
    """Validation failure."""


# 智齿 ticket_status → 终态 op_status 映射（3=已解决→已答复，99=已关闭→已关闭）。
# 其他状态（0/1/2/98）不受影响，继续走正常 triage 分流。
_ZHICHI_TERMINAL_OP_STATUS = {"3": OP_ANSWERED, "99": OP_CLOSED}


def _terminal_op_status(ticket_status: Any) -> str | None:
    """智齿 ticket_status 值可能是 int 或 str（docs 里两种类型定义都出现过），
    统一转字符串比较，不对提取处的类型做假设。"""
    if ticket_status is None:
        return None
    return _ZHICHI_TERMINAL_OP_STATUS.get(str(ticket_status).strip())


class ZhichiIngester:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._tickets = TicketRepository(db)
        self._history = StatusHistoryRepository(db)
        self._resolver = IdentityResolver(db)

    def ingest(self, payload: dict[str, Any]) -> IngestResult:
        payload = _flatten_envelope(payload)
        ticketid = self._require_str(payload, "ticketid")

        existing = self._tickets.find_by_source("zhichi", ticketid)
        if existing is not None:
            target_op = _terminal_op_status(payload.get("ticketStatus"))
            if target_op is not None:
                hub = (
                    self._db.get(HubIssue, existing.hub_issue_id) if existing.hub_issue_id else None
                )
                if hub is not None and hub.deleted_at is None and hub.type == "Operation":
                    # 已毕业 Operation hub：直接转态（镜像 KSM ingester 的转态处理）。
                    apply_op_status(
                        self._db,
                        hub,
                        to_status=target_op,
                        handler="agent",
                        reason=f"智齿工单状态同步（ticket_status={payload.get('ticketStatus')}）",
                    )
                    logger.info(
                        "zhichi_ingest_terminal_status_transition",
                        ticketid=ticketid,
                        existing_ticket_id=existing.id,
                        hub_issue_id=hub.id,
                        to_status=target_op,
                    )
                elif existing.hub_issue_id is None:
                    # 尚未毕业：直接建 Operation hub 并落终态（同新单逻辑）。
                    self._graduate_as_terminal_operation(
                        existing, target_op, payload.get("ticketStatus")
                    )
                    logger.info(
                        "zhichi_ingest_terminal_status_graduated",
                        ticketid=ticketid,
                        existing_ticket_id=existing.id,
                        to_status=target_op,
                    )
                # else: 已毕业但非 Operation（研发类/Internal_task）—— 不该出现，保护性 no-op
                return self._dedup_result(existing)
            logger.info(
                "zhichi_ingest_dedup",
                ticketid=ticketid,
                existing_ticket_id=existing.id,
            )
            return self._dedup_result(existing)

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
            product_line_code=safe_product_line_code(
                self._db, payload.get("productLineCode") or payload.get("product")
            ),
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
            # 提单快照：智齿 payload 有公司名 + 公司税号(扩展字段) + 工单等级；租户上游不带，留空。
            reporter_company=payload.get("company"),
            reporter_tax_no=payload.get("tax_no"),
            service_level=_zhichi_service_level(payload.get("_envelope") or {}),
        )
        self._tickets.add(ticket)
        self._db.flush()  # dispatch_handler.add_log 需要 ticket.id 已落库

        dr = dispatch_handler(self._db, ticket)
        if dr.user_id is not None:
            ticket.assigned_user_id = dr.user_id
            ticket.handler_user_id = dr.user_id  # 处理人初始=责任人
        dispatch_decision = "assigned" if dr.user_id is not None else "no_match"

        self._db.flush()

        # 全新工单首次入库即为终态（ticket_status=3/99）：跳过分类，直接毕业为
        # Operation hub 并落终态，不进正常 triage 链路。
        skip_post_ingest = False
        target_op = _terminal_op_status(payload.get("ticketStatus"))
        if target_op is not None:
            skip_post_ingest = self._graduate_as_terminal_operation(
                ticket, target_op, payload.get("ticketStatus")
            )

        # 建附件行（仅建行，不下载；下载+OCR 由异步流水线 drain_attachments 处理）。
        # 智齿附件来自 file_str，可能是图片/日志/zip 等；按扩展名判定 kind——
        # 只 image 进 OCR，其余（pdf/video/other）仅下载存档供处理人查看/下载。
        for url in payload.get("attachment_urls") or []:
            self._db.add(
                Attachment(
                    ticket_id=ticket.id,
                    source_url=url,
                    filename=filename_from_url(url),
                    kind=classify_attachment_kind(url),
                    vision_status="queued",
                )
            )

        self._history.record(
            entity_type="ticket",
            entity_id=ticket.id,
            from_status=None,
            to_status="received",
            changed_by="system:ingest",
            reason=f"zhichi webhook: {ticketid}",
            metadata={
                "source": "zhichi",
                "routing_decision": dispatch_decision,
                "matched_scope": "dispatch_rule" if dr.rule_id is not None else "none",
                "rationale": dr.reason,
            },
        )

        logger.info(
            "zhichi_ingest_committed",
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
            skip_post_ingest=skip_post_ingest,
        )

    def _dedup_result(self, existing: Ticket) -> IngestResult:
        return IngestResult(
            ticket_id=existing.id,
            short_code=existing.short_code,
            customer_id=self._customer_id_of(existing),
            customer_identity_id=existing.customer_identity_id or 0,
            routing_decision="dedup",
            assigned_user_ids=([existing.assigned_user_id] if existing.assigned_user_id else []),
            deduped=True,
        )

    def _graduate_as_terminal_operation(
        self, ticket: Ticket, target_op: str, raw_status: Any
    ) -> bool:
        """智齿工单已处于终态（3已解决/99已关闭）：直接毕业为 Operation hub 并
        落终态 op_status，跳过 triage 分类。复用 ensure_hub_issue_for_ticket 建
        hub（内部固定把 op_status 先置 processing，见 creator.py），再用
        apply_op_status 覆盖成 answered/closed——两步操作，不是一步到位。
        不 commit（复用调用方事务）。返回 True 表示已处理（供调用方决定是否跳过
        run_post_ingest_agents）。"""
        from app.services.hub_issues.creator import HubIssueCreateError

        try:
            result = ensure_hub_issue_for_ticket(
                ticket.id,
                created_by="system:zhichi_terminal_sync",
                type_override="Operation",
                db=self._db,
            )
        except HubIssueCreateError as e:
            logger.warning("zhichi_terminal_graduate_failed", ticket_id=ticket.id, error=str(e))
            return False
        hub = self._db.get(HubIssue, result.hub_issue_id)
        if hub is None:
            return False
        apply_op_status(
            self._db,
            hub,
            to_status=target_op,
            handler="agent",
            reason=f"智齿工单入库即终态（ticket_status={raw_status}）",
        )
        return True

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


# 智齿 ticket_level(工单等级)→ 服务等级中文档位。未知/缺失返回 None（API 层默认"标准服务"）。
_ZHICHI_LEVEL_LABELS = {"0": "普通", "1": "标准", "2": "重要", "3": "紧急"}


def _zhichi_service_level(envelope: dict[str, Any]) -> str | None:
    lv = envelope.get("ticket_level")
    if lv is None or str(lv).strip() == "":
        return None
    return _ZHICHI_LEVEL_LABELS.get(str(lv).strip(), str(lv).strip())


def _customer_field(payload: dict[str, Any], key: str) -> Any:
    """Zhichi nests customer info under `customer`; fall back to top-level."""
    cust = payload.get("customer")
    if isinstance(cust, dict) and cust.get(key):
        return cust.get(key)
    return payload.get(key)
