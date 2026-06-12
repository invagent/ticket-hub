"""Cascade tests (D4 第②段) — reply_sync（决策 15）+ status_cascade（决策 14）."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models import (
    HubIssue,
    HubIssueReplyHistory,
    Source,
    StatusHistory,
    SyncOutbox,
    Ticket,
)
from app.services.cascade.reply_sync import ReplySyncError, author_reply
from app.services.cascade.status_cascade import apply_hub_status


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add_all([Source(code="ksm", name="KSM"), Source(code="zhichi", name="智齿")])
    db_session.commit()
    return db_session


def _hub(db: Session, n: int, **ov) -> HubIssue:  # type: ignore[no-untyped-def]
    base = {
        "short_code": f"HUB-CSC-{n}",
        "type": "Operation",
        "title": f"问题 {n}",
        "status": "created",
    }
    base.update(ov)
    h = HubIssue(**base)
    db.add(h)
    db.commit()
    db.refresh(h)
    return h


def _ticket(db: Session, n: int, hub: HubIssue, **ov) -> Ticket:  # type: ignore[no-untyped-def]
    base = {
        "short_code": f"TKT-CSC-{n}",
        "source_code": "ksm",
        "source_ticket_id": f"csc-{n}",
        "type": "Raw",
        "status": "received",
        "title": f"工单 {n}",
        "hub_issue_id": hub.id,
    }
    base.update(ov)
    t = Ticket(**base)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


# ---- reply_sync ---------------------------------------------------------------


def test_author_reply_versions_and_cascades(world: Session) -> None:
    hub = _hub(world, 1)
    t1 = _ticket(world, 1, hub)
    t2 = _ticket(world, 2, hub, source_code="zhichi", source_ticket_id="z-2")

    res = author_reply(world, hub.id, content="请按以下步骤操作……", authored_by="user:carol")
    assert res.version == 1
    assert sorted(res.cascaded_ticket_ids) == sorted([t1.id, t2.id])
    assert len(res.outbox_ids) == 2

    world.refresh(hub)
    assert hub.reply_content == "请按以下步骤操作……"
    assert hub.reply_content_version == 1
    assert hub.reply_authored_by == "user:carol"

    world.refresh(t1)
    assert t1.cached_reply_content == "请按以下步骤操作……"
    assert t1.cached_reply_version == 1

    hist = world.query(HubIssueReplyHistory).filter_by(hub_issue_id=hub.id).all()
    assert len(hist) == 1 and hist[0].version == 1

    rows = world.query(SyncOutbox).filter_by(kind="reply").all()
    assert {r.target_source_code for r in rows} == {"ksm", "zhichi"}
    assert all(r.status == "pending" for r in rows)
    assert rows[0].payload["reply_version"] == 1


def test_author_reply_second_version(world: Session) -> None:
    hub = _hub(world, 2)
    _ticket(world, 3, hub)
    author_reply(world, hub.id, content="v1", authored_by="user:carol")
    res = author_reply(world, hub.id, content="v2 更准确的回复", authored_by="user:dave")
    assert res.version == 2
    world.refresh(hub)
    assert hub.reply_content_version == 2
    assert world.query(HubIssueReplyHistory).filter_by(hub_issue_id=hub.id).count() == 2


def test_author_reply_child_ticket_cached_but_no_outbox(world: Session) -> None:
    hub = _hub(world, 3)
    parent = _ticket(world, 4, hub)
    child = Ticket(
        short_code="TKT-CSC-C1",
        type="Child",
        status="received",
        internal_split_id=f"{parent.short_code}-C1",
        parent_ticket_id=parent.id,
        title="child",
        hub_issue_id=hub.id,
    )
    world.add(child)
    world.commit()

    res = author_reply(world, hub.id, content="回复", authored_by="user:carol")
    assert child.id in res.cascaded_ticket_ids
    world.refresh(child)
    assert child.cached_reply_content == "回复"
    # outbox 只有 sourced parent 一条
    assert world.query(SyncOutbox).count() == 1


def test_author_reply_non_operation_rejected(world: Session) -> None:
    hub = _hub(world, 4, type="Bug_fix")
    with pytest.raises(ReplySyncError, match="Operation-only"):
        author_reply(world, hub.id, content="x", authored_by="user:carol")


def test_author_reply_empty_rejected(world: Session) -> None:
    hub = _hub(world, 5)
    with pytest.raises(ReplySyncError, match="empty"):
        author_reply(world, hub.id, content="   ", authored_by="user:carol")


def test_author_reply_missing_hub(world: Session) -> None:
    with pytest.raises(ReplySyncError, match="not found"):
        author_reply(world, 99999, content="x", authored_by="user:carol")


# ---- status_cascade -------------------------------------------------------------


def test_released_cascades_tickets_and_outbox(world: Session) -> None:
    hub = _hub(world, 6, type="Bug_fix", status="in_progress")
    t1 = _ticket(world, 6, hub, status="in_progress")
    t2 = _ticket(world, 7, hub, source_code="zhichi", source_ticket_id="z-7", status="received")

    res = apply_hub_status(
        world, hub, to_status="released", changed_by="agent:linear_status_sync", reason="Done"
    )
    world.commit()
    assert res.changed is True
    assert sorted(res.cascaded_ticket_ids) == sorted([t1.id, t2.id])
    assert len(res.outbox_ids) == 2

    world.refresh(hub)
    world.refresh(t1)
    assert hub.status == "released" and hub.actual_released_at is not None
    assert t1.status == "released" and t1.actual_released_at is not None

    # 双方都有 history
    assert (
        world.query(StatusHistory)
        .filter_by(entity_type="hub_issue", entity_id=hub.id, to_status="released")
        .count()
        == 1
    )
    sh = (
        world.query(StatusHistory)
        .filter_by(entity_type="ticket", entity_id=t1.id, to_status="released")
        .one()
    )
    assert "cascade from" in (sh.reason or "")

    rows = world.query(SyncOutbox).filter_by(kind="status").all()
    assert all(r.payload["to_status"] == "released" for r in rows)


def test_terminal_tickets_not_touched(world: Session) -> None:
    hub = _hub(world, 7, type="Bug_fix", status="in_progress")
    done = _ticket(world, 8, hub, status="done")
    apply_hub_status(world, hub, to_status="released", changed_by="user:carol")
    world.commit()
    world.refresh(done)
    assert done.status == "done"  # 终态不动
    assert world.query(SyncOutbox).count() == 0


def test_non_cascade_status_is_hub_only(world: Session) -> None:
    hub = _hub(world, 8, type="Bug_fix")
    t = _ticket(world, 9, hub)
    res = apply_hub_status(world, hub, to_status="pending", changed_by="agent:linear_push")
    world.commit()
    assert res.changed is True
    assert res.cascaded_ticket_ids == []
    world.refresh(t)
    assert t.status == "received"  # 工单不受 hub 内部工作流影响


def test_same_status_noop(world: Session) -> None:
    hub = _hub(world, 9, type="Bug_fix", status="released")
    res = apply_hub_status(world, hub, to_status="released", changed_by="x")
    assert res.changed is False
    assert world.query(StatusHistory).count() == 0
