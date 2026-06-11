"""SupervisorRelinkService unit tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from app.models import HubIssue, Source, Ticket, TicketHubIssueHistory, User
from app.services.supervisor.relink import (
    HubIssueNotFoundError,
    PermissionDeniedError,
    RelinkRequest,
    SupervisorRelinkService,
    TicketNotFoundError,
)


@pytest.fixture
def relink_world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add_all(
        [
            User(id=1, feishu_uid="ou_alice", name="alice", role="assignee"),
            User(id=2, feishu_uid="ou_carol", name="carol", role="supervisor"),
            User(id=3, feishu_uid="ou_dave", name="dave", role="admin"),
        ]
    )
    db_session.flush()
    # 2 hub_issues
    db_session.add_all(
        [
            HubIssue(
                id=10,
                short_code="HUB-A",
                type="Operation",
                title="A",
                status="created",
            ),
            HubIssue(
                id=20,
                short_code="HUB-B",
                type="Operation",
                title="B",
                status="created",
            ),
        ]
    )
    # ticket starts linked to HUB-A
    db_session.add(
        Ticket(
            id=100,
            short_code="TKT-1",
            source_code="ksm",
            source_ticket_id="ksm-1",
            type="Raw",
            status="linked",
            hub_issue_id=10,
        )
    )
    db_session.flush()
    db_session.add(TicketHubIssueHistory(ticket_id=100, hub_issue_id=10, change_reason="initial"))
    db_session.commit()
    return db_session


# ---- happy path -----------------------------------------------------------


def test_relink_to_new_hub_issue_succeeds(relink_world: Session) -> None:
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    svc = SupervisorRelinkService(relink_world)
    res = svc.relink(
        RelinkRequest(
            ticket_id=100,
            new_hub_issue_id=20,
            supervisor_user_id=2,
            reason="customer clarified that issue is unrelated to A",
        ),
        now=now,
    )
    assert res.no_op is False
    assert res.old_hub_issue_id == 10
    assert res.new_hub_issue_id == 20
    assert res.closed_history_id is not None

    # ticket points at new hub_issue
    relink_world.expire_all()
    ticket = relink_world.get(Ticket, 100)
    assert ticket is not None
    assert ticket.hub_issue_id == 20

    # old history row closed
    closed = relink_world.get(TicketHubIssueHistory, res.closed_history_id)
    assert closed is not None
    assert closed.effective_to is not None

    # new history row open
    new_row = relink_world.get(TicketHubIssueHistory, res.new_history_id)
    assert new_row is not None
    assert new_row.hub_issue_id == 20
    assert new_row.effective_to is None
    assert new_row.human_confirmed is True
    assert new_row.change_reason == "customer clarified that issue is unrelated to A"


def test_relink_to_same_hub_is_noop(relink_world: Session) -> None:
    svc = SupervisorRelinkService(relink_world)
    res = svc.relink(
        RelinkRequest(ticket_id=100, new_hub_issue_id=10, supervisor_user_id=2, reason="x")
    )
    assert res.no_op is True
    assert res.closed_history_id is None
    assert res.old_hub_issue_id == 10
    assert res.new_hub_issue_id == 10


def test_admin_can_relink(relink_world: Session) -> None:
    svc = SupervisorRelinkService(relink_world)
    res = svc.relink(
        RelinkRequest(ticket_id=100, new_hub_issue_id=20, supervisor_user_id=3, reason="admin")
    )
    assert res.no_op is False


# ---- error paths ----------------------------------------------------------


def test_assignee_role_cannot_relink(relink_world: Session) -> None:
    svc = SupervisorRelinkService(relink_world)
    with pytest.raises(PermissionDeniedError):
        svc.relink(
            RelinkRequest(
                ticket_id=100,
                new_hub_issue_id=20,
                supervisor_user_id=1,  # alice is assignee
                reason="oops",
            )
        )


def test_unknown_user_cannot_relink(relink_world: Session) -> None:
    svc = SupervisorRelinkService(relink_world)
    with pytest.raises(PermissionDeniedError):
        svc.relink(
            RelinkRequest(
                ticket_id=100,
                new_hub_issue_id=20,
                supervisor_user_id=999,
                reason="ghost",
            )
        )


def test_unknown_ticket_raises(relink_world: Session) -> None:
    svc = SupervisorRelinkService(relink_world)
    with pytest.raises(TicketNotFoundError):
        svc.relink(
            RelinkRequest(
                ticket_id=9999,
                new_hub_issue_id=20,
                supervisor_user_id=2,
            )
        )


def test_soft_deleted_ticket_treated_as_not_found(relink_world: Session) -> None:
    ticket = relink_world.get(Ticket, 100)
    assert ticket is not None
    ticket.deleted_at = datetime(2026, 5, 1, tzinfo=UTC)
    relink_world.commit()
    svc = SupervisorRelinkService(relink_world)
    with pytest.raises(TicketNotFoundError):
        svc.relink(
            RelinkRequest(ticket_id=100, new_hub_issue_id=20, supervisor_user_id=2, reason="x")
        )


def test_unknown_hub_issue_raises(relink_world: Session) -> None:
    svc = SupervisorRelinkService(relink_world)
    with pytest.raises(HubIssueNotFoundError):
        svc.relink(RelinkRequest(ticket_id=100, new_hub_issue_id=9999, supervisor_user_id=2))


def test_soft_deleted_hub_issue_treated_as_not_found(relink_world: Session) -> None:
    hub = relink_world.get(HubIssue, 20)
    assert hub is not None
    hub.deleted_at = datetime(2026, 5, 1, tzinfo=UTC)
    relink_world.commit()
    svc = SupervisorRelinkService(relink_world)
    with pytest.raises(HubIssueNotFoundError):
        svc.relink(
            RelinkRequest(ticket_id=100, new_hub_issue_id=20, supervisor_user_id=2, reason="x")
        )
