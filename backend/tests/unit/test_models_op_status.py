"""Operation op_status 字段模型测试。"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import HubIssue


def _op_hub(**kw) -> HubIssue:
    base = {
        "short_code": "HUB-OPS-1",
        "type": "Operation",
        "title": "开票失败",
        "status": "created",
    }
    base.update(kw)
    return HubIssue(**base)


def test_op_fields_default(db_session: Session) -> None:
    hub = _op_hub()
    db_session.add(hub)
    db_session.commit()
    db_session.refresh(hub)
    assert hub.op_status is None
    assert hub.op_handler is None
    assert hub.reject_count == 0
    assert hub.op_status_changed_at is None


def test_op_status_valid_value(db_session: Session) -> None:
    hub = _op_hub(op_status="processing", op_handler="agent")
    db_session.add(hub)
    db_session.commit()
    db_session.refresh(hub)
    assert hub.op_status == "processing"


def test_op_status_invalid_value_rejected(db_session: Session) -> None:
    hub = _op_hub(op_status="bogus")
    db_session.add(hub)
    with pytest.raises(IntegrityError):
        db_session.commit()
