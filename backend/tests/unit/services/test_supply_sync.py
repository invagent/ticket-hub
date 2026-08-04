"""Supply request cascade tests (补料, D4 第②段)."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models import HubIssue, Source, StatusHistory, SyncOutbox, Ticket
from app.services.cascade.supply_sync import (
    SupplySyncError,
    batch_request_supply,
    request_supply,
)


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add_all([Source(code="ksm", name="KSM"), Source(code="zhichi", name="智齿")])
    db_session.commit()
    return db_session


def _hub(db: Session) -> HubIssue:
    h = HubIssue(short_code="HUB-SUP-1", type="Operation", title="补料问题", status="created")
    db.add(h)
    db.commit()
    db.refresh(h)
    return h


def _ticket(db: Session, hub: HubIssue, **ov: object) -> Ticket:
    base: dict[str, object] = {
        "short_code": "TKT-SUP-1",
        "source_code": "ksm",
        "source_ticket_id": "sup-1",
        "type": "Raw",
        "status": "received",
        "title": "工单",
        "hub_issue_id": hub.id,
    }
    base.update(ov)
    t = Ticket(**base)  # type: ignore[arg-type]
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def test_request_supply_enqueues_per_sourced_ticket(world: Session) -> None:
    hub = _hub(world)
    t1 = _ticket(world, hub)
    t2 = _ticket(world, hub, short_code="TKT-SUP-2", source_code="zhichi", source_ticket_id="z-2")

    res = request_supply(world, hub.id, note="请提供操作日志", requested_by="user:carol")

    assert sorted(res.ticket_ids) == sorted([t1.id, t2.id])
    assert len(res.outbox_ids) == 2
    rows = world.query(SyncOutbox).filter_by(kind="supply").all()
    assert {r.target_source_code for r in rows} == {"ksm", "zhichi"}
    assert all(r.status == "pending" for r in rows)
    assert rows[0].payload["supply_note"] == "请提供操作日志"
    assert rows[0].payload["requested_by"] == "user:carol"
    # audit line written
    hist = world.query(StatusHistory).filter_by(entity_type="ticket").all()
    assert any("补料请求" in (h.reason or "") for h in hist)


def test_child_ticket_skipped(world: Session) -> None:
    hub = _hub(world)
    sourced = _ticket(world, hub)
    # Child: no source — must be skipped (nothing to ask)
    child = Ticket(
        short_code="TKT-SUP-C",
        type="Child",
        status="received",
        internal_split_id="TKT-SUP-1-C1",
        parent_ticket_id=sourced.id,
        title="子单",
        hub_issue_id=hub.id,
    )
    world.add(child)
    world.commit()

    res = request_supply(world, hub.id, note="补料", requested_by="user:carol")
    assert res.ticket_ids == [sourced.id]


def test_empty_note_rejected(world: Session) -> None:
    hub = _hub(world)
    _ticket(world, hub)
    with pytest.raises(SupplySyncError):
        request_supply(world, hub.id, note="   ", requested_by="user:carol")


def test_hub_not_found(world: Session) -> None:
    with pytest.raises(SupplySyncError):
        request_supply(world, 9999, note="补料", requested_by="user:carol")


# ---- 工单级批量补料（列表勾选，工单不必已毕业 hub）----


def test_batch_supply_enqueues_for_unlinked_tickets(world: Session) -> None:
    """未毕业 hub 的工单也能入队，outbox.hub_issue_id 留空。"""
    t1 = Ticket(
        short_code="TKT-B1",
        source_code="ksm",
        source_ticket_id="b-1",
        type="Raw",
        status="received",
        title="单1",
    )
    t2 = Ticket(
        short_code="TKT-B2",
        source_code="zhichi",
        source_ticket_id="b-2",
        type="Raw",
        status="received",
        title="单2",
    )
    world.add_all([t1, t2])
    world.commit()

    res = batch_request_supply(world, [t1.id, t2.id], note="请补充截图", requested_by="user:dave")
    assert res.enqueued_count == 2
    assert res.skipped_count == 0
    rows = world.query(SyncOutbox).filter_by(kind="supply").all()
    assert len(rows) == 2
    assert all(r.hub_issue_id is None for r in rows)  # 未毕业 → 空
    assert all(r.status == "pending" for r in rows)
    assert {r.target_source_code for r in rows} == {"ksm", "zhichi"}


def test_batch_supply_carries_hub_id_when_linked(world: Session) -> None:
    """已毕业工单入队时带上 hub_issue_id。"""
    hub = _hub(world)
    t = _ticket(world, hub)
    res = batch_request_supply(world, [t.id], note="补料", requested_by="user:dave")
    assert res.enqueued_count == 1
    row = world.query(SyncOutbox).filter_by(kind="supply").one()
    assert row.hub_issue_id == hub.id


def test_batch_supply_skips_sourceless_and_missing(world: Session) -> None:
    """无源工单（Child）+ 不存在的 id 跳过并给出原因。"""
    sourced = Ticket(
        short_code="TKT-S",
        source_code="ksm",
        source_ticket_id="s-1",
        type="Raw",
        status="received",
        title="有源",
    )
    world.add(sourced)
    world.commit()
    child = Ticket(
        short_code="TKT-C",
        type="Child",
        status="received",
        internal_split_id="TKT-S-C1",
        parent_ticket_id=sourced.id,
        title="子单",
    )
    world.add(child)
    world.commit()

    res = batch_request_supply(
        world, [sourced.id, child.id, 99999], note="补料", requested_by="user:dave"
    )
    assert res.enqueued_count == 1
    assert res.skipped_count == 2
    by_id = {r.ticket_id: r for r in res.results}
    assert by_id[sourced.id].success is True
    assert by_id[child.id].success is False
    assert "无源" in by_id[child.id].message
    assert by_id[99999].success is False
    assert "不存在" in by_id[99999].message


def test_batch_supply_empty_note_rejected(world: Session) -> None:
    t = Ticket(
        short_code="TKT-E",
        source_code="ksm",
        source_ticket_id="e-1",
        type="Raw",
        status="received",
        title="单",
    )
    world.add(t)
    world.commit()
    with pytest.raises(SupplySyncError):
        batch_request_supply(world, [t.id], note="  ", requested_by="user:dave")
