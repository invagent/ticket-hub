"""D3-A: agent_decisions table constraints + revert flow."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AgentDecision


def _decision(**overrides) -> AgentDecision:  # type: ignore[no-untyped-def]
    base = {
        "decision_type": "classify_type",
        "subject_type": "ticket",
        "subject_id": 1,
        "proposal": {"predicted_type": "Bug_fix", "confidence": 0.9},
    }
    base.update(overrides)
    return AgentDecision(**base)


def test_default_status_is_executed(db_session: Session) -> None:
    db_session.add(_decision())
    db_session.commit()
    d = db_session.query(AgentDecision).one()
    assert d.status == "executed"
    assert d.reverted_at is None
    assert d.executed_at is not None


def test_invalid_decision_type_rejected(db_session: Session) -> None:
    db_session.add(_decision(decision_type="bogus"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_invalid_subject_type_rejected(db_session: Session) -> None:
    db_session.add(_decision(subject_type="customer"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_reverted_consistency_status_executed_with_reverted_at(
    db_session: Session,
) -> None:
    """status=executed but reverted_at set → CHECK violation."""
    db_session.add(_decision(status="executed", reverted_at=datetime.now(UTC)))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_reverted_consistency_status_reverted_without_reverted_at(
    db_session: Session,
) -> None:
    """status=reverted but reverted_at missing → CHECK violation."""
    db_session.add(_decision(status="reverted"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_revert_flow(db_session: Session) -> None:
    db_session.add(_decision(subject_id=42))
    db_session.commit()
    d = db_session.query(AgentDecision).one()

    d.status = "reverted"
    d.reverted_at = datetime.now(UTC)
    d.reverted_by = "user:zhangsan"
    d.revert_reason = "误判"
    db_session.commit()

    db_session.refresh(d)
    assert d.status == "reverted"
    assert d.reverted_by == "user:zhangsan"
    assert d.revert_reason == "误判"
