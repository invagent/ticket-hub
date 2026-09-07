from __future__ import annotations

from datetime import UTC, datetime

from app.models import HubIssue, StatusHistory, SyncOutbox, Ticket, User
from app.repositories.status_history import StatusHistoryRepository
from app.services.metrics.daily import compute_daily_dashboard

_TN = 0


def _tk(db, **kw):
    global _TN
    _TN += 1
    defaults = {
        "short_code": f"TKT-{_TN:06d}",
        "source_code": "ksm",
        "source_ticket_id": f"S{_TN}",
        "type": "Raw",
        "status": "received",
        "received_at": datetime(2026, 9, 1, 3, 0, tzinfo=UTC),  # 北京 9/1 11:00
    }
    defaults.update(kw)
    t = Ticket(**defaults)
    db.add(t)
    db.flush()
    return t


def _hub(db, **kw):
    n = db.query(HubIssue).count() + 1
    defaults = {
        "short_code": f"HUB-{n:06d}",
        "type": "Bug_fix",
        "title": "t",
        "status": "in_progress",
    }
    defaults.update(kw)
    h = HubIssue(**defaults)
    db.add(h)
    db.flush()
    return h


def test_received_counts_by_day_and_handler(db_session):
    db_session.add(User(id=1, feishu_uid="ou_a", name="甲", role="assignee"))
    db_session.commit()
    # 北京 9/1 11:00 → UTC 9/1 03:00
    _tk(db_session, handler_user_id=1, received_at=datetime(2026, 9, 1, 3, 0, tzinfo=UTC))
    # 北京 9/2 00:30 → UTC 9/1 16:30（跨天边界，不该算进 9/1）
    _tk(db_session, handler_user_id=1, received_at=datetime(2026, 9, 1, 16, 30, tzinfo=UTC))
    # 无处理人
    _tk(db_session, handler_user_id=None, received_at=datetime(2026, 9, 1, 3, 0, tzinfo=UTC))
    db_session.commit()

    r = compute_daily_dashboard(db_session, date="2026-09-01")
    assert r.totals.received == 2
    by_name = {a.name: a for a in r.by_assignee}
    assert by_name["甲"].received == 1
    assert by_name["(未分配)"].received == 1

    r2 = compute_daily_dashboard(db_session, date="2026-09-02")
    assert r2.totals.received == 1


def test_completed_counts_released_and_op_closed(db_session):
    db_session.add(User(id=1, feishu_uid="ou_a", name="甲", role="assignee"))
    db_session.commit()

    hub1 = _hub(db_session, type="Bug_fix", status="released")
    t1 = _tk(db_session, handler_user_id=1, hub_issue_id=hub1.id)
    StatusHistoryRepository(db_session).record(
        entity_type="hub_issue",
        entity_id=hub1.id,
        from_status="in_progress",
        to_status="released",
        changed_by="system:cascade",
        reason=None,
    )
    db_session.execute(
        StatusHistory.__table__.update()
        .where(StatusHistory.entity_id == hub1.id)
        .values(changed_at=datetime(2026, 9, 1, 3, 0, tzinfo=UTC))
    )

    hub2 = _hub(db_session, type="Operation", status="in_progress", op_status="closed")
    t2 = _tk(db_session, handler_user_id=1, hub_issue_id=hub2.id)
    StatusHistoryRepository(db_session).record(
        entity_type="hub_issue",
        entity_id=hub2.id,
        from_status="processing",
        to_status="closed",
        changed_by="op:agent",
        reason="agent 答复成功",
    )
    db_session.execute(
        StatusHistory.__table__.update()
        .where(StatusHistory.entity_id == hub2.id)
        .values(changed_at=datetime(2026, 9, 1, 4, 0, tzinfo=UTC))
    )
    db_session.commit()

    r = compute_daily_dashboard(db_session, date="2026-09-01")
    assert r.totals.completed == 2
    assert next(a for a in r.by_assignee if a.name == "甲").completed == 2
    assert t1.hub_issue_id == hub1.id and t2.hub_issue_id == hub2.id


def test_returned_to_ksm_counts_by_sent_at(db_session):
    db_session.add(User(id=1, feishu_uid="ou_a", name="甲", role="assignee"))
    db_session.commit()

    t = _tk(db_session, handler_user_id=1, ksm_takeover_status="handled")
    row = SyncOutbox(
        kind="return",
        target_source_code="ksm",
        ticket_id=t.id,
        source_ticket_id=t.source_ticket_id,
        payload={},
        status="sent",
        sent_at=datetime(2026, 9, 1, 5, 0, tzinfo=UTC),
    )
    # 一条未送达的不计
    row2 = SyncOutbox(
        kind="return",
        target_source_code="ksm",
        ticket_id=t.id,
        source_ticket_id=t.source_ticket_id,
        payload={},
        status="pending",
    )
    db_session.add_all([row, row2])
    db_session.commit()

    r = compute_daily_dashboard(db_session, date="2026-09-01")
    assert r.totals.returned_to_ksm == 1
    assert r.lifetime.returned_to_ksm_total == 0  # lifetime 口径是"客户驳回"不是退回


def test_ksm_rejected_and_supplemented_dont_cross_contaminate(db_session):
    db_session.add(User(id=1, feishu_uid="ou_a", name="甲", role="assignee"))
    db_session.commit()

    hub_reject = _hub(db_session, type="Operation", op_status="processing", reject_count=1)
    _tk(db_session, handler_user_id=1, hub_issue_id=hub_reject.id)
    StatusHistoryRepository(db_session).record(
        entity_type="hub_issue",
        entity_id=hub_reject.id,
        from_status="answered",
        to_status="processing",
        changed_by="op:agent",
        reason="客户驳回（第1次）",
    )
    db_session.execute(
        StatusHistory.__table__.update()
        .where(StatusHistory.entity_id == hub_reject.id)
        .values(changed_at=datetime(2026, 9, 1, 3, 0, tzinfo=UTC))
    )

    hub_supp = _hub(db_session, type="Operation", op_status="processing")
    _tk(db_session, handler_user_id=1, hub_issue_id=hub_supp.id)
    StatusHistoryRepository(db_session).record(
        entity_type="hub_issue",
        entity_id=hub_supp.id,
        from_status="supplementing",
        to_status="processing",
        changed_by="op:agent",
        reason="客户补料回流，交回 AI 重答",
    )
    db_session.execute(
        StatusHistory.__table__.update()
        .where(StatusHistory.entity_id == hub_supp.id)
        .values(changed_at=datetime(2026, 9, 1, 4, 0, tzinfo=UTC))
    )
    db_session.commit()

    r = compute_daily_dashboard(db_session, date="2026-09-01")
    assert r.totals.ksm_rejected == 1
    assert r.totals.supplemented == 1
    a = next(x for x in r.by_assignee if x.name == "甲")
    assert a.ksm_rejected == 1
    assert a.supplemented == 1


def test_lifetime_totals(db_session):
    _tk(db_session, status="received")
    _tk(db_session, status="in_progress")
    _tk(db_session, status="done")
    hub = _hub(db_session)
    StatusHistoryRepository(db_session).record(
        entity_type="hub_issue",
        entity_id=hub.id,
        from_status="answered",
        to_status="processing",
        changed_by="op:agent",
        reason="客户驳回（第1次）",
    )
    db_session.commit()

    r = compute_daily_dashboard(db_session, date="2026-09-01")
    assert r.lifetime.total == 3
    assert r.lifetime.in_progress == 1
    assert r.lifetime.completed == 1
    assert r.lifetime.returned_to_ksm_total == 1
