"""ManualAssignService unit tests (supervisor manual-assign, bypasses Router)."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Source, StatusHistory, Ticket, User
from app.services.supervisor.manual_assign import (
    AssignRequest,
    ManualAssignService,
    TargetUserInvalidError,
)


def _mk_user(db: Session, *, name: str, role: str, is_active: bool = True) -> User:
    u = User(feishu_uid=f"fs-{name}", name=name, role=role, is_active=is_active)
    db.add(u)
    db.flush()
    return u


def _mk_ticket(db: Session, *, short_code: str, assigned_user_id: int | None = None) -> Ticket:
    t = Ticket(
        type="Raw",
        source_code="ksm",
        source_ticket_id=f"src-{short_code}",
        short_code=short_code,
        title="t",
        body="b",
        status="received",
        assigned_user_id=assigned_user_id,
    )
    db.add(t)
    db.flush()
    return t


def _status_rows(db: Session, ticket_id: int) -> list[StatusHistory]:
    return list(
        db.execute(
            select(StatusHistory).where(
                StatusHistory.entity_type == "ticket",
                StatusHistory.entity_id == ticket_id,
            )
        )
        .scalars()
        .all()
    )


@pytest.fixture(autouse=True)
def _seed_source(db_session: Session) -> None:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.flush()


def test_assign_single_success(db_session: Session) -> None:
    op = _mk_user(db_session, name="op", role="supervisor")
    target = _mk_user(db_session, name="dev", role="assignee")
    t = _mk_ticket(db_session, short_code="T-1")

    res = ManualAssignService(db_session).assign(
        AssignRequest(ticket_ids=[t.id], assigned_user_id=target.id, operator_user_id=op.id)
    )

    assert res.assigned_count == 1
    assert res.not_found_count == 0
    assert res.results[0].success is True
    assert res.results[0].prev_assigned_user_id is None
    db_session.flush()
    assert db_session.get(Ticket, t.id).assigned_user_id == target.id


def test_assign_records_status_history(db_session: Session) -> None:
    op = _mk_user(db_session, name="op", role="admin")
    target = _mk_user(db_session, name="dev", role="assignee")
    t = _mk_ticket(db_session, short_code="T-2")

    ManualAssignService(db_session).assign(
        AssignRequest(ticket_ids=[t.id], assigned_user_id=target.id, operator_user_id=op.id)
    )

    rows = _status_rows(db_session, t.id)
    assert any(r.changed_by == "system:manual_assign" for r in rows)


def test_assign_target_role_not_allowed(db_session: Session) -> None:
    op = _mk_user(db_session, name="op", role="supervisor")
    member = _mk_user(db_session, name="m", role="member")
    t = _mk_ticket(db_session, short_code="T-3")

    with pytest.raises(TargetUserInvalidError):
        ManualAssignService(db_session).assign(
            AssignRequest(ticket_ids=[t.id], assigned_user_id=member.id, operator_user_id=op.id)
        )


def test_assign_target_inactive(db_session: Session) -> None:
    op = _mk_user(db_session, name="op", role="supervisor")
    dead = _mk_user(db_session, name="x", role="assignee", is_active=False)
    t = _mk_ticket(db_session, short_code="T-4")

    with pytest.raises(TargetUserInvalidError):
        ManualAssignService(db_session).assign(
            AssignRequest(ticket_ids=[t.id], assigned_user_id=dead.id, operator_user_id=op.id)
        )


def test_assign_target_not_found(db_session: Session) -> None:
    op = _mk_user(db_session, name="op", role="supervisor")
    t = _mk_ticket(db_session, short_code="T-5")

    with pytest.raises(TargetUserInvalidError):
        ManualAssignService(db_session).assign(
            AssignRequest(ticket_ids=[t.id], assigned_user_id=99999, operator_user_id=op.id)
        )


def test_assign_partial_ticket_not_found(db_session: Session) -> None:
    op = _mk_user(db_session, name="op", role="supervisor")
    target = _mk_user(db_session, name="dev", role="assignee")
    t = _mk_ticket(db_session, short_code="T-6")

    res = ManualAssignService(db_session).assign(
        AssignRequest(ticket_ids=[t.id, 88888], assigned_user_id=target.id, operator_user_id=op.id)
    )
    assert res.assigned_count == 1
    assert res.not_found_count == 1
    by_id = {r.ticket_id: r for r in res.results}
    assert by_id[t.id].success is True
    assert by_id[88888].success is False
