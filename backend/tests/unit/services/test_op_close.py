"""T+N 自动关闭（close_overdue_answered）单测。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import HubIssue, StatusHistory
from app.services.hub_issues.op_status import (
    OP_ANSWERED,
    OP_CLOSED,
    OP_PROCESSING,
    close_overdue_answered,
)


@dataclass
class _S:
    operation_auto_close_enabled: bool = True
    operation_auto_close_days: int = 7
    default_pool_user_id: int | None = None


def _hub(
    db: Session,
    *,
    short_code: str,
    op_status: str = OP_ANSWERED,
    op_handler: str = "agent",
    changed_at: datetime,
    type_: str = "Operation",
    deleted: bool = False,
) -> HubIssue:
    hub = HubIssue(
        short_code=short_code,
        type=type_,
        title="t",
        status="created",
        op_status=op_status,
        op_handler=op_handler,
        op_status_changed_at=changed_at,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    db.add(hub)
    db.flush()
    return hub


def test_close_overdue_answered_closes_only_stale(db_session: Session) -> None:
    now = datetime.now(UTC)
    overdue = _hub(
        db_session,
        short_code="HUB-CLOSE-OLD",
        changed_at=now - timedelta(days=8),
    )
    fresh = _hub(
        db_session,
        short_code="HUB-CLOSE-NEW",
        changed_at=now - timedelta(days=1),
    )
    db_session.commit()

    count = close_overdue_answered(db_session, settings=_S())
    db_session.commit()

    assert count == 1
    db_session.refresh(overdue)
    db_session.refresh(fresh)
    assert overdue.op_status == OP_CLOSED
    assert fresh.op_status == OP_ANSWERED

    h = (
        db_session.query(StatusHistory)
        .filter_by(entity_type="hub_issue", entity_id=overdue.id, to_status=OP_CLOSED)
        .first()
    )
    assert h is not None


def test_close_overdue_answered_disabled_switch_noop(db_session: Session) -> None:
    now = datetime.now(UTC)
    overdue = _hub(
        db_session,
        short_code="HUB-CLOSE-OFF",
        changed_at=now - timedelta(days=30),
    )
    db_session.commit()

    count = close_overdue_answered(db_session, settings=_S(operation_auto_close_enabled=False))
    db_session.commit()

    assert count == 0
    db_session.refresh(overdue)
    assert overdue.op_status == OP_ANSWERED


def test_close_overdue_answered_ignores_non_answered_and_non_operation(
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    processing = _hub(
        db_session,
        short_code="HUB-CLOSE-PROC",
        op_status=OP_PROCESSING,
        changed_at=now - timedelta(days=30),
    )
    other_type = _hub(
        db_session,
        short_code="HUB-CLOSE-BUG",
        op_status=None,
        type_="Bug_fix",
        changed_at=now - timedelta(days=30),
    )
    deleted = _hub(
        db_session,
        short_code="HUB-CLOSE-DEL",
        changed_at=now - timedelta(days=30),
        deleted=True,
    )
    db_session.commit()

    count = close_overdue_answered(db_session, settings=_S())
    db_session.commit()

    assert count == 0
    db_session.refresh(processing)
    db_session.refresh(other_type)
    db_session.refresh(deleted)
    assert processing.op_status == OP_PROCESSING
    assert other_type.op_status is None
    assert deleted.op_status == OP_ANSWERED


def test_close_overdue_answered_reject_refresh_protects_from_close(db_session: Session) -> None:
    """驳回会把 op_status 转回 processing 并刷新 changed_at —— 不该被 T+7 误关。"""
    now = datetime.now(UTC)
    hub = _hub(
        db_session,
        short_code="HUB-CLOSE-REJECTED",
        op_status=OP_PROCESSING,  # 驳回后已经不是 answered
        changed_at=now - timedelta(days=30),  # 即便原答复很久之前
    )
    db_session.commit()

    count = close_overdue_answered(db_session, settings=_S())
    db_session.commit()

    assert count == 0
    db_session.refresh(hub)
    assert hub.op_status == OP_PROCESSING
