"""Source-system webhooks.

  POST /webhook/ksm?access_token=<webhook_token>      → KSMIngester
  POST /webhook/zhichi?access_token=<webhook_token>   → ZhichiIngester
  POST /webhook/zammad?access_token=<webhook_token>   → ZammadIngester
  GET  /webhook/tickets/lookup?access_token=&ticket_no= → 第三方工单号/本系统
                                                           short_code 查详情

  Future:
    - /webhook/linear (D4)

Each webhook authenticates via constant-time compare with
settings.webhook_access_token, parses payload as a JSON object, dispatches to
the source-specific ingester, commits, and returns IngestResponse.

KSM specifics (D2-F): KSM pushes a *lightweight* ping with just billId +
noticeNum + subscribeNum, expects an IMMEDIATE `{"code": 0}` reply, then we
call /subscribeCallback async to fetch the full ticket. See ksm_payload.py
for the field mapping and notice_store.py for the per-billId latest-pair
cache that handles rapid re-pushes correctly.
"""

from __future__ import annotations

import hmac
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from adapters.ksm import KSMClient, KSMConfig, KSMError
from app.api.tickets import TicketDetail, build_ticket_detail
from app.config import get_settings
from app.core.logging import get_logger
from app.core.trace import get_trace_id
from app.db import get_session, make_session
from app.models import HubIssue, Ticket
from app.repositories.ticket import TicketRepository
from app.services.agents.classify import classify_ticket
from app.services.agents.escalation_classify import classify_escalation_ticket
from app.services.agents.split import execute_split_for_ticket as execute_split_for_ticket
from app.services.agents.triage import run_ticket_triage
from app.services.agents.vision_extract import extract_ticket_attachments
from app.services.hub_issues.creator import create_hub_issue_for_ticket_auto
from app.services.ingest.escalation_ingester import EscalationIngester
from app.services.ingest.escalation_ingester import IngestError as EscalationIngestError
from app.services.ingest.feishu_ai_ingester import FeishuAiIngester
from app.services.ingest.feishu_ai_ingester import IngestError as FeishuAiIngestError
from app.services.ingest.ksm_ingester import IngestError as KSMIngestError
from app.services.ingest.ksm_ingester import KSMIngester
from app.services.ingest.ksm_payload import from_subscribe_callback
from app.services.ingest.zammad_ingester import IngestError as ZammadIngestError
from app.services.ingest.zammad_ingester import ZammadIngester
from app.services.ingest.zhichi_ingester import IngestError as ZhichiIngestError
from app.services.ingest.zhichi_ingester import ZhichiIngester
from app.services.ksm.notice_store import NoticeInfo, NoticeStore
from app.services.ksm.takeover import takeover_ksm_ticket

router = APIRouter()
logger = get_logger(__name__)


def _route_by_type(
    ticket_id: int, ticket_type: str | None, confidence: float, *, bar: float
) -> None:
    """ADR-0016：按类型分流。Complaint 停 ticket 层转人工（不毕业）；其余在置信度
    过门槛时毕业 hub_issue（内部对 Bug/Demand 做 hub_dedup + 推 Linear）。"""
    settings = get_settings()
    if ticket_type == "Complaint":
        return  # 投诉停 ticket 层，进工作台高亮人工队列
    if not (settings.hub_issue_auto_enabled and confidence >= bar):
        return
    create_hub_issue_for_ticket_auto(ticket_id)
    # Operation 自动答复不在入库主链路同步跑——replay 走 LLM 可能长达 2-3 分钟，会长时间
    # 占用 worker。改由 Celery beat 任务 drain_operation_auto_reply 异步处理（每 2min 扫描
    # 已毕业未答复的 Operation hub），兼作偶发失败的补偿重试。


def _route_child(child_id: int) -> None:
    """拆分子单分流：优先用继承的 predicted_type（triage sub_type，原子不再分类）；
    旧提案无继承类型时兜底跑 classify（不跑 triage，避免递归拆分）。"""
    settings = get_settings()
    db = make_session()
    try:
        child = db.get(Ticket, child_id)
        ptype = child.predicted_type if child else None
        pconf = float(child.predicted_confidence) if child and child.predicted_confidence else None
    finally:
        db.close()
    if ptype is None:
        cls = classify_ticket(child_id)  # 兜底：向后兼容旧 conflict_detect 提案
        if cls is None:
            return
        ptype, pconf = cls.type, cls.confidence
    _route_by_type(child_id, ptype, pconf or 0.0, bar=settings.hub_issue_auto_confidence)


def _resolve_module(ticket_id: int) -> None:
    """AI 产品模块归类（判定 product_line_code/module，供人工审核确认分类时校验/展示）。

    处理人已在入库阶段由 dispatch_handler 按来源+规则分配好，不再因模块归类
    结果重新路由——归类只覆盖 ticket.product_line_code/module 为现有 active
    目录内的规范值（绝不自建）。归类失败/关闭时静默跳过，不阻塞分流。
    """
    settings = get_settings()
    if not settings.module_classify_enabled:
        return
    from app.services.agents.module_resolve import resolve_module

    db = make_session()
    try:
        ticket = db.get(Ticket, ticket_id)
        if ticket is None:
            return
        resolve_module(db, ticket)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("module_resolve_failed", ticket_id=ticket_id)
    finally:
        db.close()


def _populate_subtasks_from_triage(ticket_id: int, sub_problems: Sequence[Any]) -> None:
    """分诊识别出混合问题时，直接为工单创建对应的 Hub 子任务（废除旧 Child ticket 拆分）。"""
    from app.services.hub_issues.creator import _next_hub_short_code

    db = make_session()
    try:
        ticket = db.get(Ticket, ticket_id)
        if ticket is None:
            return

        existing_count = (
            db.execute(
                select(func.count(HubIssue.id)).where(
                    HubIssue.ticket_id == ticket.id,
                    HubIssue.deleted_at.is_(None),
                )
            ).scalar()
            or 0
        )
        if existing_count > 0:
            return

        assignee_id = ticket.handler_user_id or ticket.assigned_user_id
        for sp in sub_problems:
            st_type = getattr(sp, "type", "Operation")
            if st_type not in ("Operation", "Bug_fix", "Demand", "Internal_task"):
                st_type = "Operation"
            st_title = str(getattr(sp, "title", "")).strip() or "子任务"
            st_summary = str(getattr(sp, "summary", "")).strip() or ticket.body

            st = HubIssue(
                short_code=_next_hub_short_code(db),
                ticket_id=ticket.id,
                type=st_type,
                title=st_title,
                canonical_body=st_summary,
                product_line_code=ticket.product_line_code,
                module=ticket.module,
                status="draft",
                assigned_user_id=assignee_id,
                occurrence_count=1,
            )
            db.add(st)
            db.flush()
        db.commit()
        logger.info(
            "subtasks_populated_from_triage",
            ticket_id=ticket.id,
            count=len(sub_problems),
        )
    except Exception:
        db.rollback()
        logger.exception("subtasks_populate_failed", ticket_id=ticket_id)
    finally:
        db.close()


def run_post_ingest_agents(ticket_id: int) -> None:
    """入库后 LLM 链（子任务架构升级）：分诊 → 归类模块 → 分流毕业主任务 → 自动落库子任务.

    vision_extract(截图OCR) → triage(classify+conflict 合一：定型+是否混合) →
      module_resolve(AI 判产品线/模块，覆盖生效值为现有目录规范值) →
      按类型分流毕业（Complaint 停 ticket 层；其余毕业主 hub_issue）→
      若判定包含多个子问题，直接自动落库为关联的 Hub 子任务。
    单一 BG task；各步失败自吞不阻塞。
    """
    settings = get_settings()
    if settings.vision_enabled:
        extract_ticket_attachments(ticket_id)

    tri = run_ticket_triage(ticket_id)
    if tri is None:
        return

    # 产品模块归类（覆盖生效 plc/module，供人工审核）。在分流前——毕业 hub 要继承规范值。
    _resolve_module(ticket_id)

    # 分流毕业主 Hub 任务
    _route_by_type(ticket_id, tri.type, tri.confidence, bar=settings.hub_issue_auto_confidence)

    # 混合单直接生成对应的 Hub 子任务（废弃原 Child 工单拆分机制）
    if tri.is_mixed and tri.sub_problems:
        _populate_subtasks_from_triage(ticket_id, tri.sub_problems)


def run_escalation_agents(ticket_id: int) -> None:
    """AI 客服 escalation 链（D4 第③段，ADR-0016 P2c 对齐分流）.

    escalation 有黄金三元组上下文，**保留专用 escalation_classify**（不走 triage）。
    escalation 工单已聚焦（AI 客服筛过一轮），不做混合拆分。分流终局与主链一致：
    Complaint 停 ticket；其余在 ESCALATION_AUTO_CONFIDENCE 过门槛时毕业。
    """
    settings = get_settings()
    if settings.vision_enabled:
        extract_ticket_attachments(ticket_id)
    cls = classify_escalation_ticket(ticket_id)
    if cls is None:
        return
    _route_by_type(ticket_id, cls.type, cls.confidence, bar=settings.escalation_auto_confidence)


class IngestResponse(BaseModel):
    """Used by zhichi / zammad webhooks (synchronous response shape)."""

    ticket_id: int
    short_code: str
    deduped: bool
    routing_decision: str
    assigned_user_ids: list[int] = []
    trace_id: str | None = None


class KSMAck(BaseModel):
    """KSM expects {"code": 0} as the ack — anything else is treated as a
    delivery failure and KSM will retry."""

    code: int = 0


def _verify_webhook_token(provided: str | None) -> None:
    expected = get_settings().webhook_access_token
    if not expected:
        raise HTTPException(status_code=503, detail="webhook auth not configured")
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid webhook access_token")


async def _read_object(request: Request) -> dict[str, Any]:
    try:
        payload: Any = await request.json()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid JSON: {e}") from e
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be a JSON object")
    return payload


# ---- KSM ------------------------------------------------------------------

# Module-level so it can be monkey-patched in tests (FakeNoticeStore).
_notice_store: NoticeStore | None = None


def _get_notice_store() -> NoticeStore | object:
    """Lazy singleton. Returns NoticeStore(redis_url=...) by default; tests
    override this via app.dependency_overrides — but since we use a module
    global rather than a dep, tests monkey-patch _notice_store directly.
    """
    global _notice_store
    if _notice_store is None:
        _notice_store = NoticeStore(redis_url=get_settings().redis_url)
    return _notice_store


def _run_ksm_takeover(
    db: Session,
    *,
    ticket_id: int,
    is_new: bool,
    detail: dict[str, Any],
    client: KSMClient,
    notice_store: Any,
    settings: Any,
) -> None:
    """派单后立即接管（用刚分配好的处理人身份），独立 try 保证接管失败不影响
    入库/后续 triage/人工审核。commits。"""
    try:
        ticket = db.get(Ticket, ticket_id)
        if ticket is None:
            return
        if ticket.status in ("closed", "transferred_return"):
            logger.info("ksm_takeover_skip_terminal", ticket_id=ticket_id, status=ticket.status)
            return
        if str(detail.get("status") or ticket.source_status or "") == "6":
            logger.info("ksm_takeover_skip_status_6", ticket_id=ticket_id)
            return
        takeover_ksm_ticket(
            db,
            ticket,
            detail=detail,
            is_new=is_new,
            client=client,
            notice_store=notice_store,
            settings=settings,
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("ksm_takeover_unexpected_failure", ticket_id=ticket_id)


def _ksm_async_fetch_and_ingest(bill_id: str) -> None:
    """BackgroundTask body. Fetch latest detail from KSM via subscribeCallback,
    then run it through KSMIngester in a fresh DB session.

    Errors are swallowed (logged) — KSM has already received {"code": 0} and
    won't retry. If we propagate, the request is finished anyway.
    """
    settings = get_settings()
    notice = _get_notice_store().get(bill_id)  # type: ignore[attr-defined]
    if notice is None:
        logger.warning("ksm_async_no_notice_in_store", bill_id=bill_id)
        return

    cfg = KSMConfig(
        base_url=settings.ksm_base_url,
        app_id=settings.ksm_app_id,
        app_secret=settings.ksm_app_secret,
        tenant_id=settings.ksm_tenant_id,
        account_id=settings.ksm_account_id,
        user=settings.ksm_user,
    )
    # client 生命周期覆盖到派单即接管（takeover 需在 detail 拉取后、同一 client 上
    # lock→重拉→handle），故 close 延后到最外层 finally。
    client = KSMClient(cfg)
    ingested_ticket_id: int | None = None
    try:
        try:
            detail = client.get_order_detail(
                bill_id=bill_id,
                notice_num=notice.notice_num,
                subscribe_num=notice.subscribe_num,
            )
        except KSMError as e:
            logger.exception("ksm_async_fetch_detail_failed", bill_id=bill_id, error=str(e))
            return

        payload = from_subscribe_callback(detail)
        if not payload.get("billId"):
            logger.warning("ksm_async_detail_missing_billid", bill_id=bill_id)
            return

        db = make_session()
        try:
            try:
                result = KSMIngester(db).ingest(payload)
            except KSMIngestError as e:
                db.rollback()
                logger.warning("ksm_async_ingest_validation_failed", bill_id=bill_id, error=str(e))
                return
            # 持久化本次成功拉取用的 notice（不设过期时间）：Redis 缓存 24h 会过期，
            # 但 KSM 服务端凭证实际有效期比这更长（2026-09 实测 4 天前的旧 notice
            # 依然能用）。落库后，Redis 过期时退回/重拉详情仍有得回落，不必人工
            # 去 KSM 系统翻找。每次成功拉取都覆盖为最新一次，见 writeback/takeover
            # 里的 _resolve_notice 消费方。
            ticket_row = db.get(Ticket, result.ticket_id)
            if ticket_row is not None:
                ticket_row.ksm_notice_num = notice.notice_num
                ticket_row.ksm_subscribe_num = notice.subscribe_num
            db.commit()
            ingested_ticket_id = result.ticket_id if not result.deduped else None
            logger.info(
                "ksm_async_ingest_committed",
                bill_id=bill_id,
                ticket_id=result.ticket_id,
                short_code=result.short_code,
                deduped=result.deduped,
                routing_decision=result.routing_decision,
            )
            # 派单（dispatch_handler，在 ingest 内）已拿到处理人 → 立即用该处理人
            # 身份接管 KSM，不再等 triage/模块归类/人工审核确认分类。分类判断仍走
            # 后续人工审核，与接管解耦——接管早晚不影响分流/推 Linear 的判断。
            _run_ksm_takeover(
                db,
                ticket_id=result.ticket_id,
                is_new=not result.deduped,
                detail=detail,
                client=client,
                notice_store=_get_notice_store(),
                settings=settings,
            )
        except Exception:
            db.rollback()
            logger.exception("ksm_async_ingest_unexpected_failure", bill_id=bill_id)
        finally:
            db.close()
    finally:
        client.close()

    # D3-C/D: post-ingest agents (classify + conflict_detect). Skip on
    # dedupe (already processed) and on ingest failure. Errors swallowed
    # inside each agent.
    if ingested_ticket_id is not None:
        run_post_ingest_agents(ingested_ticket_id)


@router.post("/ksm", response_model=KSMAck)
async def ksm_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    access_token: str = Query(...),
    db: Session = Depends(get_session),
) -> KSMAck:
    """KSM webhook receiver.

    Two payload modes (the endpoint accepts both, KSM only sends the first):

    1. **Lightweight ping** (production KSM contract per doc § 二):
       `{billId|id, noticeNum, subscribeNum}` → store latest pair → schedule
       BackgroundTask to call subscribeCallback and ingest → return
       `{"code": 0}` immediately.

    2. **Full payload** (legacy / tests / xlsx replay):
       caller already provides title/content/productLineCode/...; ingest
       synchronously and still return `{"code": 0}` (KSM contract).

    Per doc: 校验三个字段均不为空，否则忽略（log warning + 200）—
    important so KSM doesn't retry malformed pushes.
    """
    _verify_webhook_token(access_token)
    payload = await _read_object(request)

    bill_id = payload.get("billId") or payload.get("id")  # KSM sometimes uses `id`
    notice_num = payload.get("noticeNum")
    subscribe_num = payload.get("subscribeNum")

    is_lightweight_ping = bool(bill_id and notice_num and subscribe_num)
    looks_like_lightweight = any(payload.get(k) for k in ("noticeNum", "subscribeNum"))

    if is_lightweight_ping:
        # JSON values may arrive as numbers — normalize to str at the boundary.
        bill_id_s, notice_s, subscribe_s = str(bill_id), str(notice_num), str(subscribe_num)
        # Store the LATEST pair (overwrite on every push) so concurrent
        # background fetches all converge on the freshest notice values.
        _get_notice_store().put(  # type: ignore[attr-defined]
            bill_id_s,
            NoticeInfo(notice_num=notice_s, subscribe_num=subscribe_s),
        )
        background_tasks.add_task(_ksm_async_fetch_and_ingest, bill_id_s)
        logger.info(
            "ksm_webhook_lightweight_ping",
            bill_id=bill_id,
            notice_num=notice_num,
            subscribe_num=subscribe_num,
        )
        return KSMAck(code=0)

    if looks_like_lightweight and not is_lightweight_ping:
        # Mishaped lightweight push (some required field missing). Per doc
        # § 二 接收端处理逻辑 #1: 校验三个字段均不为空，否则忽略.
        logger.warning(
            "ksm_webhook_lightweight_missing_fields",
            has_billId=bool(bill_id),
            has_noticeNum=bool(notice_num),
            has_subscribeNum=bool(subscribe_num),
        )
        return KSMAck(code=0)

    # Legacy / test path: full payload, sync ingest.
    if not bill_id:
        logger.warning("ksm_webhook_full_payload_missing_billid")
        return KSMAck(code=0)
    try:
        result = KSMIngester(db).ingest(payload)
    except KSMIngestError as e:
        logger.warning("ksm_webhook_sync_validation_failed", bill_id=bill_id, error=str(e))
        return KSMAck(code=0)
    db.commit()
    logger.info(
        "ksm_webhook_sync_committed",
        ticket_id=result.ticket_id,
        short_code=result.short_code,
        deduped=result.deduped,
        trace_id=get_trace_id(),
    )
    # D3-C/D: post-ingest agents after sync ingest (skip dedup).
    if not result.deduped:
        background_tasks.add_task(run_post_ingest_agents, result.ticket_id)
    return KSMAck(code=0)


# ---- Zhichi ---------------------------------------------------------------


@router.post("/zhichi", response_model=IngestResponse)
async def zhichi_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    access_token: str = Query(...),
    db: Session = Depends(get_session),
) -> IngestResponse:
    _verify_webhook_token(access_token)
    payload = await _read_object(request)
    try:
        result = ZhichiIngester(db).ingest(payload)
    except ZhichiIngestError as e:
        raise HTTPException(status_code=400, detail=f"ingest failed: {e}") from e
    db.commit()
    logger.info(
        "zhichi_webhook_committed",
        ticket_id=result.ticket_id,
        short_code=result.short_code,
        deduped=result.deduped,
    )
    # D3-C/D: post-ingest agents after ingest (skip dedup). skip_post_ingest：
    # 全新工单入库即终态（ticket_status=3/99）已直接毕业 Operation hub 落终态，
    # 不再进 triage 分类链路（见 ZhichiIngester._graduate_as_terminal_operation）。
    if not result.deduped and not result.skip_post_ingest:
        background_tasks.add_task(run_post_ingest_agents, result.ticket_id)
    return IngestResponse(
        ticket_id=result.ticket_id,
        short_code=result.short_code,
        deduped=result.deduped,
        routing_decision=result.routing_decision,
        assigned_user_ids=result.assigned_user_ids,
        trace_id=get_trace_id(),
    )


# ---- AI 客服 escalation (D4 第③段) ----------------------------------------


@router.post("/cs-escalation", response_model=IngestResponse)
async def cs_escalation_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    access_token: str = Query(...),
    db: Session = Depends(get_session),
) -> IngestResponse:
    """AI 客服「会话失败/转人工」实时回调 → 建 ai_cs 工单 → escalation 链
    （vision → 黄金三元组二次分类 → dedup/conflict）。"""
    _verify_webhook_token(access_token)
    payload = await _read_object(request)
    try:
        result = EscalationIngester(db).ingest(payload)
    except EscalationIngestError as e:
        raise HTTPException(status_code=400, detail=f"ingest failed: {e}") from e
    db.commit()
    logger.info(
        "cs_escalation_webhook_committed",
        ticket_id=result.ticket_id,
        short_code=result.short_code,
        deduped=result.deduped,
        attachments=len(result.attachment_ids),
    )
    if not result.deduped:
        background_tasks.add_task(run_escalation_agents, result.ticket_id)
    return IngestResponse(
        ticket_id=result.ticket_id,
        short_code=result.short_code,
        deduped=result.deduped,
        routing_decision=result.routing_decision,
        assigned_user_ids=result.assigned_user_ids,
        trace_id=get_trace_id(),
    )


# ---- 飞书AI (feishu_ai) ----------------------------------------------------


@router.post("/feishu_ai", response_model=IngestResponse)
async def feishu_ai_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    access_token: str = Query(...),
    db: Session = Depends(get_session),
) -> IngestResponse:
    """飞书AI 工单入库：复用 ai_cs 载荷契约，但入库后走标准 triage 链
    （run_post_ingest_agents，与 KSM/智齿一致），而非 escalation 二次分类。"""
    _verify_webhook_token(access_token)
    payload = await _read_object(request)
    try:
        result = FeishuAiIngester(db).ingest(payload)
    except FeishuAiIngestError as e:
        raise HTTPException(status_code=400, detail=f"ingest failed: {e}") from e
    db.commit()
    logger.info(
        "feishu_ai_webhook_committed",
        ticket_id=result.ticket_id,
        short_code=result.short_code,
        deduped=result.deduped,
        attachments=len(result.attachment_ids),
    )
    # 关键差异：走标准 triage 链（非 escalation）。
    if not result.deduped:
        background_tasks.add_task(run_post_ingest_agents, result.ticket_id)
    return IngestResponse(
        ticket_id=result.ticket_id,
        short_code=result.short_code,
        deduped=result.deduped,
        routing_decision=result.routing_decision,
        assigned_user_ids=result.assigned_user_ids,
        trace_id=get_trace_id(),
    )


# ---- Zammad ---------------------------------------------------------------


@router.post("/zammad", response_model=IngestResponse)
async def zammad_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    access_token: str = Query(...),
    db: Session = Depends(get_session),
) -> IngestResponse:
    _verify_webhook_token(access_token)
    payload = await _read_object(request)
    try:
        result = ZammadIngester(db).ingest(payload)
    except ZammadIngestError as e:
        raise HTTPException(status_code=400, detail=f"ingest failed: {e}") from e
    db.commit()
    logger.info(
        "zammad_webhook_committed",
        ticket_id=result.ticket_id,
        short_code=result.short_code,
        deduped=result.deduped,
    )
    # D3-C/D: post-ingest agents after ingest (skip dedup).
    if not result.deduped:
        background_tasks.add_task(run_post_ingest_agents, result.ticket_id)
    return IngestResponse(
        ticket_id=result.ticket_id,
        short_code=result.short_code,
        deduped=result.deduped,
        routing_decision=result.routing_decision,
        assigned_user_ids=result.assigned_user_ids,
        trace_id=get_trace_id(),
    )


# ---- 外部工单号查询 ---------------------------------------------------------


class TicketLookupResponse(BaseModel):
    items: list[TicketDetail]


@router.get("/tickets/lookup", response_model=TicketLookupResponse)
def lookup_ticket(
    ticket_no: str = Query(..., min_length=1, max_length=128),
    access_token: str = Query(...),
    db: Session = Depends(get_session),
) -> TicketLookupResponse:
    """供第三方系统按工单号查询工单详情，鉴权复用 webhook 同一个
    access_token（settings.webhook_access_token），非登录用户 token。

    ticket_no 同时精确匹配三个字段之一（同 /api/tickets 列表页
    source_ticket_q 的字段口径，但这里是精确匹配不是子串搜索）：
      - source_ticket_number（来源工单编号，如 KSM billNumber）
      - source_ticket_id（来源工单 id，如 KSM billId）
      - short_code（本系统工单号，如 TKT-006797）

    三者理论上不会跨行冲突，但历史数据不排除极少数例外，命中多条时
    全部返回，交调用方按自己的字段辨认；未命中返回空列表（不是 404，
    「查无此单」对第三方系统是正常结果，不是错误）。
    """
    _verify_webhook_token(access_token)
    tickets = TicketRepository(db).find_by_any_ticket_no(ticket_no)
    return TicketLookupResponse(items=[build_ticket_detail(db, t) for t in tickets])
