"""apply_op_status 单测。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import HubIssue, StatusHistory
from app.services.hub_issues.op_status import (
    OP_ANSWERED,
    OP_PROCESSING,
    apply_op_status,
)


def _hub(db: Session) -> HubIssue:
    hub = HubIssue(
        short_code="HUB-OPS-A",
        type="Operation",
        title="t",
        status="created",
        op_status="processing",
        op_handler="agent",
    )
    db.add(hub)
    db.flush()
    return hub


def test_apply_changes_status_handler_and_records_history(db_session: Session) -> None:
    hub = _hub(db_session)
    db_session.commit()
    changed = apply_op_status(
        db_session, hub, to_status=OP_ANSWERED, handler="agent", reason="answered"
    )
    db_session.commit()
    assert changed is True
    db_session.refresh(hub)
    assert hub.op_status == OP_ANSWERED
    assert hub.op_status_changed_at is not None
    h = (
        db_session.query(StatusHistory)
        .filter_by(entity_type="hub_issue", entity_id=hub.id, to_status=OP_ANSWERED)
        .first()
    )
    assert h is not None


def test_apply_idempotent_noop(db_session: Session) -> None:
    hub = _hub(db_session)
    db_session.commit()
    changed = apply_op_status(db_session, hub, to_status=OP_PROCESSING, handler="agent")
    assert changed is False


def test_apply_handler_change_only(db_session: Session) -> None:
    """同状态但换处理人（agent→主管，转人工）也算变更。"""
    hub = _hub(db_session)
    db_session.commit()
    changed = apply_op_status(
        db_session, hub, to_status=OP_PROCESSING, handler="主管", reason="转人工"
    )
    db_session.commit()
    assert changed is True
    db_session.refresh(hub)
    assert hub.op_handler == "主管"
