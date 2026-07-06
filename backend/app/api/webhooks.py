"""Source-system webhooks.

  POST /webhook/ksm?access_token=<webhook_token>      → KSMIngester
  POST /webhook/zhichi?access_token=<webhook_token>   → ZhichiIngester
  POST /webhook/zammad?access_token=<webhook_token>   → ZammadIngester

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
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from adapters.ksm import KSMClient, KSMConfig, KSMError
from app.config import get_settings
from app.core.logging import get_logger
from app.core.trace import get_trace_id
from app.db import get_session, make_session
from app.models import Ticket
from app.services.agents.classify import classify_ticket
from app.services.agents.escalation_classify import classify_escalation_ticket
from app.services.agents.split import execute_split_for_ticket
from app.services.agents.triage import run_ticket_triage
from app.services.agents.vision_extract import extract_ticket_attachments
from app.services.hub_issues.creator import create_hub_issue_for_ticket_auto
from app.services.ingest.escalation_ingester import EscalationIngester
from app.services.ingest.escalation_ingester import IngestError as EscalationIngestError
from app.services.ingest.ksm_ingester import IngestError as KSMIngestError
from app.services.ingest.ksm_ingester import KSMIngester
from app.services.ingest.ksm_payload import from_subscribe_callback
from app.services.ingest.zammad_ingester import IngestError as ZammadIngestError
from app.services.ingest.zammad_ingester import ZammadIngester
from app.services.ingest.zhichi_ingester import IngestError as ZhichiIngestError
from app.services.ingest.zhichi_ingester import ZhichiIngester
from app.services.ksm.notice_store import NoticeInfo, NoticeStore
from app.services.system_settings import get_default_pool_user_id

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
    if settings.hub_issue_auto_enabled and confidence >= bar:
        create_hub_issue_for_ticket_auto(ticket_id)


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


def run_post_ingest_agents(ticket_id: int) -> None:
    """入库后 LLM 链（ADR-0016 P2c 重排）：分诊 → 先原子化 → 按类型分流.

    vision_extract(截图OCR) → triage(classify+conflict 合一：定型+是否混合) →
      混合: 自动拆开关开且过门槛 → split 原子化 → 每子单按继承类型分流；
            否则停摆进「待拆分」人工队列（split_ticket 审计已由 triage 写）。
      非混合: 按 type 分流（Complaint 停 ticket 层；其余毕业 hub_issue）。
    单一 BG task；各步失败自吞不阻塞。子单原子、永不再拆。
    """
    settings = get_settings()
    if settings.vision_enabled:
        extract_ticket_attachments(ticket_id)

    tri = run_ticket_triage(ticket_id)
    if tri is None:
        return  # triage 失败：留 predicted_type=None，人工可见（不误分流）

    if tri.is_mixed:
        if settings.split_auto_enabled and tri.confidence >= settings.split_auto_confidence:
            split_res = execute_split_for_ticket(ticket_id, executed_by="agent:split_auto")
            if split_res is not None:
                for child_id in split_res.child_ticket_ids:
                    _route_child(child_id)
        # 未开自动拆 → 停摆等人工（split_ticket 提案已在队列）
        return

    _route_by_type(ticket_id, tri.type, tri.confidence, bar=settings.hub_issue_auto_confidence)


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
    client = KSMClient(cfg)
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
    finally:
        client.close()

    payload = from_subscribe_callback(detail)
    if not payload.get("billId"):
        logger.warning("ksm_async_detail_missing_billid", bill_id=bill_id)
        return

    db = make_session()
    ingested_ticket_id: int | None = None
    try:
        try:
            result = KSMIngester(db, default_pool_user_id=get_default_pool_user_id(db)).ingest(
                payload
            )
        except KSMIngestError as e:
            db.rollback()
            logger.warning("ksm_async_ingest_validation_failed", bill_id=bill_id, error=str(e))
            return
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
    except Exception:
        db.rollback()
        logger.exception("ksm_async_ingest_unexpected_failure", bill_id=bill_id)
    finally:
        db.close()

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
        result = KSMIngester(db, default_pool_user_id=get_default_pool_user_id(db)).ingest(payload)
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
        result = ZhichiIngester(db, default_pool_user_id=get_default_pool_user_id(db)).ingest(
            payload
        )
    except ZhichiIngestError as e:
        raise HTTPException(status_code=400, detail=f"ingest failed: {e}") from e
    db.commit()
    logger.info(
        "zhichi_webhook_committed",
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
        result = EscalationIngester(db, default_pool_user_id=get_default_pool_user_id(db)).ingest(
            payload
        )
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
        result = ZammadIngester(db, default_pool_user_id=get_default_pool_user_id(db)).ingest(
            payload
        )
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
