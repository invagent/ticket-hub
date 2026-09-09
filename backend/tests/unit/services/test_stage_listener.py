"""ADR-0017 Task 1.3：before_flush 监听器自动维护 stage（映射层对账）。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import HubIssue, StatusHistory, Ticket


def _mk_ticket(db: Session, **kw) -> Ticket:  # type: ignore[no-untyped-def]
    base = {
        "short_code": kw.pop("short_code", "TKT-000001"),
        "source_code": "ksm",
        "source_ticket_id": kw.pop("source_ticket_id", "k-1"),
        "type": "Raw",
        "status": "received",
        "title": "t",
    }
    base.update(kw)
    t = Ticket(**base)
    db.add(t)
    db.flush()
    return t


def _mk_hub(db: Session, **kw) -> HubIssue:  # type: ignore[no-untyped-def]
    base = {
        "short_code": kw.pop("short_code", "HUB-000001"),
        "type": "Operation",
        "title": "h",
        "status": "created",
    }
    base.update(kw)
    h = HubIssue(**base)
    db.add(h)
    db.flush()
    return h


def _stage_rows(db: Session, entity_type: str, entity_id: int) -> list[StatusHistory]:
    return list(
        db.execute(
            select(StatusHistory)
            .where(StatusHistory.entity_type == entity_type, StatusHistory.entity_id == entity_id)
            .order_by(StatusHistory.id)
        ).scalars()
    )


def test_new_ticket_gets_received_stage(db_session: Session) -> None:
    t = _mk_ticket(db_session)
    assert t.stage == "received"
    assert t.stage_changed_at is not None
    # 新建对象在 before_flush 无 id → 不写历史
    assert _stage_rows(db_session, "ticket_stage", t.id) == []


def test_hub_change_propagates_to_linked_tickets(db_session: Session) -> None:
    t1 = _mk_ticket(db_session, short_code="TKT-000010", source_ticket_id="k-10")
    t2 = _mk_ticket(db_session, short_code="TKT-000011", source_ticket_id="k-11")
    hub = _mk_hub(db_session, op_status="processing")
    assert hub.stage == "processing"

    t1.hub_issue_id = hub.id
    t2.hub_issue_id = hub.id
    db_session.flush()
    assert t1.stage == "processing" and t2.stage == "processing"

    # 清掉 identity map 里的 t2，验证监听器会主动查库找到未加载的挂载工单
    t1_id, t2_id, hub_id = t1.id, t2.id, hub.id
    db_session.commit()
    db_session.expunge(t2)

    hub = db_session.get(HubIssue, hub_id)
    assert hub is not None
    hub.op_status = "answered"
    db_session.flush()

    assert hub.stage == "answered"
    t1_fresh = db_session.get(Ticket, t1_id)
    t2_fresh = db_session.get(Ticket, t2_id)
    assert t1_fresh is not None and t2_fresh is not None
    assert t1_fresh.stage == "answered"
    assert t2_fresh.stage == "answered"

    rows = _stage_rows(db_session, "ticket_stage", t1_id)
    assert [r.to_status for r in rows] == ["processing", "answered"]
    assert rows[-1].from_status == "processing"
    assert rows[-1].changed_by == "system:stage_sync"
    assert "op_status=answered" in (rows[-1].reason or "")
    assert rows[-1].metadata_ == {"kind": "stage"}
    hub_rows = _stage_rows(db_session, "hub_stage", hub_id)
    assert [r.to_status for r in hub_rows] == ["answered"]


def test_noop_flush_writes_no_history(db_session: Session) -> None:
    t = _mk_ticket(db_session)
    t.title = "renamed"  # 非 watched 字段
    db_session.flush()
    t.status = "received"  # 同值赋值
    db_session.flush()
    assert _stage_rows(db_session, "ticket_stage", t.id) == []
    assert t.stage == "received"


def test_stage_actor_from_session_info(db_session: Session) -> None:
    t = _mk_ticket(db_session)
    db_session.info["stage_actor"] = "user:张三"
    t.status = "transferred_return"
    db_session.flush()
    rows = _stage_rows(db_session, "ticket_stage", t.id)
    assert rows[-1].to_status == "returned"
    assert rows[-1].changed_by == "user:张三"


def test_gate_and_linear_transitions(db_session: Session) -> None:
    t = _mk_ticket(db_session, predicted_type="Bug_fix")
    hub = _mk_hub(db_session, type="Bug_fix", status="pending_review")
    t.hub_issue_id = hub.id
    db_session.flush()
    assert t.stage == "pending_classify"

    hub.status = "pending_linear_review"
    db_session.flush()
    assert t.stage == "pending_push"

    hub.status = "created"
    hub.linear_status = "Backlog"
    db_session.flush()
    assert t.stage == "in_dev"

    hub.linear_status = "In Review"
    db_session.flush()
    assert t.stage == "dev_review"

    hub.status = "released"
    hub.linear_status = "Done"
    db_session.flush()
    assert t.stage == "released"
    assert hub.stage == "released"

    rows = _stage_rows(db_session, "ticket_stage", t.id)
    assert [r.to_status for r in rows] == [
        "pending_classify",
        "pending_push",
        "in_dev",
        "dev_review",
        "released",
    ]


def test_complaint_without_hub(db_session: Session) -> None:
    t = _mk_ticket(db_session)
    t.predicted_type = "Complaint"
    db_session.flush()
    assert t.stage == "complaint"
    t.status = "closed"
    db_session.flush()
    assert t.stage == "closed"
