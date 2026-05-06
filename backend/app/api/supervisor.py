"""Supervisor work-bench API endpoints.

  GET  /api/supervisor/inbox                       — pending notifications
  POST /api/supervisor/notifications/{id}/ack      — mark acknowledged
  POST /api/supervisor/relink                      — re-link ticket↔hub_issue

All endpoints require role IN ('supervisor', 'admin').
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps.auth import AuthedUser, require_supervisor
from app.core.logging import get_logger
from app.db import get_session
from app.repositories.notification_log import NotificationLogRepository
from app.services.supervisor.relink import (
    HubIssueNotFoundError,
    PermissionDeniedError,
    RelinkRequest,
    SupervisorRelinkService,
    TicketNotFoundError,
)

router = APIRouter()
logger = get_logger(__name__)


# ---- DTOs -----------------------------------------------------------------


class InboxItem(BaseModel):
    id: int
    notify_type: str
    channel: str
    related_entity_type: str | None
    related_entity_id: int | None
    payload: dict[str, Any]
    sent_at: datetime


class InboxResponse(BaseModel):
    items: list[InboxItem]


class AckResponse(BaseModel):
    notification_id: int
    acknowledged_at: datetime


class RelinkBody(BaseModel):
    ticket_id: int
    new_hub_issue_id: int
    reason: str = ""


class RelinkResponse(BaseModel):
    ticket_id: int
    old_hub_issue_id: int | None
    new_hub_issue_id: int
    no_op: bool
    closed_history_id: int | None
    new_history_id: int


# ---- endpoints ------------------------------------------------------------


@router.get("/inbox", response_model=InboxResponse)
def list_inbox(
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
    limit: int = 100,
) -> InboxResponse:
    rows = NotificationLogRepository(db).list_pending_for_recipient(
        user.user_id, limit=min(limit, 200)
    )
    return InboxResponse(
        items=[
            InboxItem(
                id=r.id,
                notify_type=r.notify_type,
                channel=r.channel,
                related_entity_type=r.related_entity_type,
                related_entity_id=r.related_entity_id,
                payload=r.payload,
                sent_at=r.sent_at,
            )
            for r in rows
        ]
    )


@router.post("/notifications/{notification_id}/ack", response_model=AckResponse)
def ack_notification(
    notification_id: int,
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> AckResponse:
    repo = NotificationLogRepository(db)
    row = repo.get(notification_id)
    if row is None:
        raise HTTPException(status_code=404, detail="notification not found")
    if row.recipient_user_id != user.user_id:
        # Non-recipients cannot ack each other's notifications (audit cleanliness).
        # Admin override could be added later if needed.
        raise HTTPException(
            status_code=403, detail="cannot ack a notification addressed to another user"
        )
    if row.acknowledged_at is not None:
        return AckResponse(notification_id=row.id, acknowledged_at=row.acknowledged_at)
    repo.acknowledge(notification_id)
    db.commit()
    db.refresh(row)
    logger.info(
        "supervisor_ack",
        notification_id=notification_id,
        supervisor_user_id=user.user_id,
    )
    assert row.acknowledged_at is not None  # just set
    return AckResponse(notification_id=row.id, acknowledged_at=row.acknowledged_at)


@router.post("/relink", response_model=RelinkResponse)
def relink_ticket(
    body: RelinkBody,
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> RelinkResponse:
    svc = SupervisorRelinkService(db)
    try:
        result = svc.relink(
            RelinkRequest(
                ticket_id=body.ticket_id,
                new_hub_issue_id=body.new_hub_issue_id,
                supervisor_user_id=user.user_id,
                reason=body.reason,
            )
        )
    except (TicketNotFoundError, HubIssueNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PermissionDeniedError as e:
        # Only happens if JWT role got out of sync with DB; treat as 403
        raise HTTPException(status_code=403, detail=str(e)) from e
    db.commit()
    return RelinkResponse(
        ticket_id=result.ticket_id,
        old_hub_issue_id=result.old_hub_issue_id,
        new_hub_issue_id=result.new_hub_issue_id,
        no_op=result.no_op,
        closed_history_id=result.closed_history_id,
        new_history_id=result.new_history_id,
    )
