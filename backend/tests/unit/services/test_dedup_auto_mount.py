"""auto_mount_recent_duplicate 测试 — 90 天窗口 + 开关 + 守卫。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AgentDecision, HubIssue, Source, Ticket, TicketHubIssueHistory
from app.services.agents.dedup_execute import auto_mount_recent_duplicate


@pytest.fixture(autouse=True)
def _enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEDUP_AUTO_MOUNT_ENABLED", "true")
    monkeypatch.setenv("DEDUP_MOUNT_WINDOW_DAYS", "90")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(
        HubIssue(id=70, short_code="HUB-AM-70", type="Bug_fix", title="全票池", status="created")
    )
    db_session.flush()
    db_session.commit()
    return db_session


def _ticket(db: Session, tid: int, **ov) -> Ticket:  # type: ignore[no-untyped-def]
    base = {
        "id": tid,
        "short_code": f"TKT-AM-{tid}",
        "source_code": "ksm",
        "source_ticket_id": f"am-{tid}",
        "type": "Raw",
        "status": "received",
        "title": f"工单 {tid}",
    }
    base.update(ov)
    t = Ticket(**base)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _decision(db: Session, subject_id: int, target_id: int) -> AgentDecision:
    d = AgentDecision(
        decision_type="dedup_link",
        subject_type="ticket",
        subject_id=subject_id,
        proposal={"decision": "duplicate", "duplicate_of_ticket_id": target_id, "confidence": 0.9},
    )
    db.add(d)
    db.commit()
    return d


def test_auto_mount_within_window(world: Session) -> None:
    target = _ticket(
        world, 200, hub_issue_id=70, received_at=datetime.now(UTC) - timedelta(days=10)
    )
    dup = _ticket(world, 201)
    _decision(world, dup.id, target.id)

    hub_id = auto_mount_recent_duplicate(dup.id, world)
    assert hub_id == 70
    world.refresh(dup)
    assert dup.hub_issue_id == 70
    hub = world.get(HubIssue, 70)
    world.refresh(hub)
    assert hub.occurrence_count == 2
    link = world.query(TicketHubIssueHistory).filter_by(ticket_id=dup.id).one()
    assert link.human_confirmed is False  # agent: 前缀


def test_skip_out_of_window(world: Session) -> None:
    target = _ticket(
        world, 210, hub_issue_id=70, received_at=datetime.now(UTC) - timedelta(days=120)
    )
    dup = _ticket(world, 211)
    _decision(world, dup.id, target.id)
    assert auto_mount_recent_duplicate(dup.id, world) is None
    world.refresh(dup)
    assert dup.hub_issue_id is None  # 超 90 天不自动挂


def test_skip_target_without_hub(world: Session) -> None:
    target = _ticket(world, 220, received_at=datetime.now(UTC))  # 无 hub
    dup = _ticket(world, 221)
    _decision(world, dup.id, target.id)
    assert auto_mount_recent_duplicate(dup.id, world) is None


def test_disabled_switch(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEDUP_AUTO_MOUNT_ENABLED", "false")
    get_settings.cache_clear()
    target = _ticket(world, 230, hub_issue_id=70, received_at=datetime.now(UTC))
    dup = _ticket(world, 231)
    _decision(world, dup.id, target.id)
    assert auto_mount_recent_duplicate(dup.id, world) is None
    world.refresh(dup)
    assert dup.hub_issue_id is None


def test_no_decision_returns_none(world: Session) -> None:
    t = _ticket(world, 240)
    assert auto_mount_recent_duplicate(t.id, world) is None
