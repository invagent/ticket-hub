"""Supply request cascade tests (补料, D4 第②段)."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models import HubIssue, Source, StatusHistory, SyncOutbox, Ticket
from app.services.cascade.supply_sync import SupplySyncError, request_supply


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
