"""SLAWatcher 工作日感知精筛测试（SLA_WORKDAY_AWARE 开）。

时间统一用 UTC-aware（与存量 watcher 测试一致，避免 SQLite 混合时区比较问题）；
workday_hours_between 内部自会转北京时区切「日」。BJ = UTC+8。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Holiday, Source, Ticket, User
from app.services.sla.watcher import SLAWatcher


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(User(id=1, feishu_uid="ou_a", name="a", role="assignee"))
    db_session.commit()
    return db_session


@pytest.fixture(autouse=True)
def _aware(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLA_WORKDAY_AWARE", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _ticket(db: Session, received: datetime) -> Ticket:
    t = Ticket(
        short_code="TKT-WD-1",
        source_code="ksm",
        source_ticket_id="wd-1",
        type="Raw",
        status="received",
        received_at=received,
        assigned_user_id=1,
    )
    db.add(t)
    db.commit()
    return t


def test_workday_elapsed_overdue(world: Session) -> None:
    """周四 09:00BJ(01:00UTC) 收 → 周四 15:00BJ(07:00UTC) → 工作日 6h ≥ 4h → 超时。"""
    _ticket(world, datetime(2026, 6, 25, 1, tzinfo=UTC))
    res = SLAWatcher(world).scan(now=datetime(2026, 6, 25, 7, tzinfo=UTC))
    assert res.notifications_written == 1


def test_weekend_elapsed_not_overdue(world: Session) -> None:
    """周六 10:00BJ(02:00UTC) 收 → 周六 18:00BJ(10:00UTC)：墙钟 8h 但工作日小时=0 → 不超时。"""
    _ticket(world, datetime(2026, 6, 27, 2, tzinfo=UTC))
    res = SLAWatcher(world).scan(now=datetime(2026, 6, 27, 10, tzinfo=UTC))
    assert res.notifications_written == 0


def test_holiday_pauses_clock(world: Session) -> None:
    world.add(Holiday(holiday_date=date(2026, 10, 1), day_type="holiday"))
    world.commit()
    # 9-30(周三)23:00BJ(15:00UTC) 收 → 10-1(节假)18:00BJ(10:00UTC)：工作日仅周三 1h < 4h
    _ticket(world, datetime(2026, 9, 30, 15, tzinfo=UTC))
    res = SLAWatcher(world).scan(now=datetime(2026, 10, 1, 10, tzinfo=UTC))
    assert res.notifications_written == 0


def test_disabled_uses_wallclock(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLA_WORKDAY_AWARE", "false")
    get_settings.cache_clear()
    # 周末墙钟 8h → 关工作日感知则按墙钟超时
    _ticket(world, datetime(2026, 6, 27, 2, tzinfo=UTC))
    res = SLAWatcher(world).scan(now=datetime(2026, 6, 27, 10, tzinfo=UTC))
    assert res.notifications_written == 1
