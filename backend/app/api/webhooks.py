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
from app.services.agents.classify import classify_ticket
from app.services.ingest.ksm_ingester import IngestError as KSMIngestError
from app.services.ingest.ksm_ingester import KSMIngester
from app.services.ingest.ksm_payload import from_subscribe_callback
from app.services.ingest.zammad_ingester import IngestError as ZammadIngestError
from app.services.ingest.zammad_ingester import ZammadIngester
from app.services.ingest.zhichi_ingester import IngestError as ZhichiIngestError
from app.services.ingest.zhichi_ingester import ZhichiIngester
from app.services.ksm.notice_store import NoticeInfo, NoticeStore

router = APIRouter()
logger = get_logger(__name__)


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


def _get_notice_store() -> "NoticeStore | object":
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
            logger.exception(
                "ksm_async_fetch_detail_failed", bill_id=bill_id, error=str(e)
            )
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
            result = KSMIngester(db).ingest(payload)
        except KSMIngestError as e:
            db.rollback()
            logger.warning(
                "ksm_async_ingest_validation_failed", bill_id=bill_id, error=str(e)
            )
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

    # D3-C: classify the freshly-ingested ticket. Skip on dedupe (already
    # classified previously) and on ingest failure. Errors swallowed inside
    # classify_ticket.
    if ingested_ticket_id is not None:
        classify_ticket(ingested_ticket_id)


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
    looks_like_lightweight = any(
        payload.get(k) for k in ("noticeNum", "subscribeNum")
    )

    if is_lightweight_ping:
        # Store the LATEST pair (overwrite on every push) so concurrent
        # background fetches all converge on the freshest notice values.
        _get_notice_store().put(  # type: ignore[attr-defined]
            bill_id,
            NoticeInfo(notice_num=notice_num, subscribe_num=subscribe_num),
        )
        background_tasks.add_task(_ksm_async_fetch_and_ingest, bill_id)
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
        logger.warning(
            "ksm_webhook_sync_validation_failed", bill_id=bill_id, error=str(e)
        )
        return KSMAck(code=0)
    db.commit()
    logger.info(
        "ksm_webhook_sync_committed",
        ticket_id=result.ticket_id,
        short_code=result.short_code,
        deduped=result.deduped,
        trace_id=get_trace_id(),
    )
    # D3-C: classify after sync ingest (skip dedup so we don't re-classify).
    if not result.deduped:
        background_tasks.add_task(classify_ticket, result.ticket_id)
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
    # D3-C: classify after ingest (skip dedup).
    if not result.deduped:
        background_tasks.add_task(classify_ticket, result.ticket_id)
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
    # D3-C: classify after ingest (skip dedup).
    if not result.deduped:
        background_tasks.add_task(classify_ticket, result.ticket_id)
    return IngestResponse(
        ticket_id=result.ticket_id,
        short_code=result.short_code,
        deduped=result.deduped,
        routing_decision=result.routing_decision,
        assigned_user_ids=result.assigned_user_ids,
        trace_id=get_trace_id(),
    )
