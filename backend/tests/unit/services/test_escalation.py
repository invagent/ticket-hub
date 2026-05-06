"""EscalationWorker unit tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models import NotificationLog, User, UserSupervisor
from app.services.sla.escalation import EscalationWorker


@pytest.fixture
def org_world(db_session: Session) -> Session:
    """Org chart:
    alice (id=1)  ── supervisor=carol  deputy=bob
    bob   (id=2)  ── supervisor=carol  deputy=None
    carol (id=3)  ── supervisor=dave   deputy=None
    dave  (id=4)  ── (top, no supervisor row)
    """
    db_session.add_all(
        [
            User(id=1, feishu_uid="ou_alice", name="alice", role="assignee"),
            User(id=2, feishu_uid="ou_bob", name="bob", role="assignee"),
            User(id=3, feishu_uid="ou_carol", name="carol", role="supervisor"),
            User(id=4, feishu_uid="ou_dave", name="dave", role="admin"),
        ]
    )
    db_session.flush()
    db_session.add_all(
        [
            UserSupervisor(user_id=1, supervisor_id=3, deputy_supervisor_id=2),
            UserSupervisor(user_id=2, supervisor_id=3),
            UserSupervisor(user_id=3, supervisor_id=4),
        ]
    )
    db_session.commit()
    return db_session


def _seed_notification(
    db: Session, *, recipient: int, sent_at: datetime, ack: datetime | None = None
) -> NotificationLog:
    n = NotificationLog(
        recipient_user_id=recipient,
        channel="feishu_bot",
        notify_type="sla_overdue",
        payload={"x": 1},
        sent_at=sent_at,
        acknowledged_at=ack,
    )
    db.add(n)
    db.commit()
    return n


# ---- escalation routing ----------------------------------------------------


def test_unack_after_2h_escalates_to_deputy_when_present(org_world: Session) -> None:
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    n = _seed_notification(org_world, recipient=1, sent_at=now - timedelta(hours=3))

    res = EscalationWorker(org_world).escalate_pending(now=now)
    assert len(res.escalated) == 1
    step = res.escalated[0]
    assert step.notification_id == n.id
    assert step.original_recipient_id == 1
    assert step.escalated_to_user_id == 2  # bob is alice's deputy
    assert step.via == "deputy"

    # original marked
    org_world.refresh(n)
    assert n.escalated_at is not None
    assert n.escalated_to_user_id == 2

    # new notification was created for bob
    new_notifs = (
        org_world.query(NotificationLog).filter(NotificationLog.recipient_user_id == 2).all()
    )
    assert len(new_notifs) == 1
    assert new_notifs[0].notify_type == "escalation"
    assert new_notifs[0].payload["escalation_of_notification_id"] == n.id


def test_no_deputy_escalates_to_supervisor(org_world: Session) -> None:
    """bob's notification — bob has supervisor=carol, no deputy → escalate to carol."""
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    _seed_notification(org_world, recipient=2, sent_at=now - timedelta(hours=3))
    res = EscalationWorker(org_world).escalate_pending(now=now)
    assert len(res.escalated) == 1
    assert res.escalated[0].escalated_to_user_id == 3
    assert res.escalated[0].via == "supervisor"


def test_acknowledged_not_escalated(org_world: Session) -> None:
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    _seed_notification(
        org_world,
        recipient=1,
        sent_at=now - timedelta(hours=5),
        ack=now - timedelta(hours=4),
    )
    res = EscalationWorker(org_world).escalate_pending(now=now)
    assert res.escalated == []


def test_within_2h_not_escalated(org_world: Session) -> None:
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    _seed_notification(org_world, recipient=1, sent_at=now - timedelta(hours=1))
    res = EscalationWorker(org_world).escalate_pending(now=now)
    assert res.escalated == []


def test_already_escalated_not_re_escalated(org_world: Session) -> None:
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    n = _seed_notification(org_world, recipient=1, sent_at=now - timedelta(hours=5))
    n.escalated_at = now - timedelta(hours=2)
    n.escalated_to_user_id = 2
    org_world.commit()

    res = EscalationWorker(org_world).escalate_pending(now=now)
    assert res.escalated == []


def test_top_of_chain_no_target(org_world: Session) -> None:
    """dave has no supervisor row → no_target."""
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    n = _seed_notification(org_world, recipient=4, sent_at=now - timedelta(hours=3))
    res = EscalationWorker(org_world).escalate_pending(now=now)
    assert res.escalated == []
    assert res.no_target == [n.id]


def test_two_step_chain_via_followup_run(org_world: Session) -> None:
    """alice → deputy bob (1st pass) → bob's escalation → carol (2nd pass)."""
    t0 = datetime(2026, 5, 6, 8, 0, tzinfo=UTC)
    n = _seed_notification(org_world, recipient=1, sent_at=t0)
    # 1st run at t0+3h: alice's notification escalates to bob
    EscalationWorker(org_world).escalate_pending(now=t0 + timedelta(hours=3))
    # the new notification for bob has sent_at = t0+3h
    bob_notif = (
        org_world.query(NotificationLog).filter(NotificationLog.recipient_user_id == 2).first()
    )
    assert bob_notif is not None
    bob_notif.sent_at = t0 + timedelta(hours=3)  # ensure deterministic timestamp
    org_world.commit()

    # 2nd run at t0+6h: bob's escalation notification goes 3h unacked → escalates
    res = EscalationWorker(org_world).escalate_pending(now=t0 + timedelta(hours=6))
    assert len(res.escalated) == 1
    # bob has no deputy, supervisor is carol
    assert res.escalated[0].escalated_to_user_id == 3
    assert res.escalated[0].via == "supervisor"
    # original alice notification stays escalated
    org_world.refresh(n)
    assert n.escalated_at is not None
