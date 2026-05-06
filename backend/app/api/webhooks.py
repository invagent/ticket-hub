"""Source-system webhooks.

  POST /webhook/ksm?access_token=<webhook_token>      → KSMIngester
  POST /webhook/zhichi?access_token=<webhook_token>   → ZhichiIngester

  Future:
    - /webhook/zammad (D2)
    - /webhook/linear (D4)

Each webhook authenticates via constant-time compare with
settings.webhook_access_token, parses payload as a JSON object, dispatches to
the source-specific ingester, commits, and returns IngestResponse.
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.logging import get_logger
from app.core.trace import get_trace_id
from app.db import get_session
from app.services.ingest.ksm_ingester import IngestError as KSMIngestError
from app.services.ingest.ksm_ingester import KSMIngester
from app.services.ingest.zhichi_ingester import IngestError as ZhichiIngestError
from app.services.ingest.zhichi_ingester import ZhichiIngester

router = APIRouter()
logger = get_logger(__name__)


class IngestResponse(BaseModel):
    ticket_id: int
    short_code: str
    deduped: bool
    routing_decision: str
    assigned_user_ids: list[int] = []
    trace_id: str | None = None


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


@router.post("/ksm", response_model=IngestResponse)
async def ksm_webhook(
    request: Request,
    access_token: str = Query(...),
    db: Session = Depends(get_session),
) -> IngestResponse:
    _verify_webhook_token(access_token)
    payload = await _read_object(request)
    try:
        result = KSMIngester(db).ingest(payload)
    except KSMIngestError as e:
        raise HTTPException(status_code=400, detail=f"ingest failed: {e}") from e
    db.commit()
    logger.info(
        "ksm_webhook_committed",
        ticket_id=result.ticket_id,
        short_code=result.short_code,
        deduped=result.deduped,
    )
    return IngestResponse(
        ticket_id=result.ticket_id,
        short_code=result.short_code,
        deduped=result.deduped,
        routing_decision=result.routing_decision,
        assigned_user_ids=result.assigned_user_ids,
        trace_id=get_trace_id(),
    )


@router.post("/zhichi", response_model=IngestResponse)
async def zhichi_webhook(
    request: Request,
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
    return IngestResponse(
        ticket_id=result.ticket_id,
        short_code=result.short_code,
        deduped=result.deduped,
        routing_decision=result.routing_decision,
        assigned_user_ids=result.assigned_user_ids,
        trace_id=get_trace_id(),
    )
