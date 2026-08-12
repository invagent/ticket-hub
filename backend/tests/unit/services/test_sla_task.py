"""SLA monitor beat task — switch gating + wiring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

import app.services.sla.sla_task as sla_task
from app.config import get_settings
from app.models import HubIssue, NotificationLog, Source, User


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def world(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(User(id=1, feishu_uid="ou_a", name="alice", role="assignee"))
    # An Operation hub_issue overdue past the 4h default, assignee set.
    db_session.add(
        HubIssue(
            short_code="HUB-000001",
            type="Operation",
            status="in_progress",
            title="stuck op",
            first_seen_at=datetime.now(UTC) - timedelta(hours=9),
            last_seen_at=datetime.now(UTC) - timedelta(hours=9),
            assigned_user_id=1,
        )
    )
    db_session.commit()
    # task builds its own session via make_session; point it at the test session
    monkeypatch.setattr(sla_task, "make_session", lambda: db_session)
    return db_session


def test_disabled_switch_skips_scan(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLA_WATCHER_ENABLED", "false")
    get_settings.cache_clear()

    result = sla_task.run_sla_monitor()

    assert result == {"notifications_written": 0, "escalated": 0, "skipped": 0}
    # nothing written to notification_log
    assert world.query(NotificationLog).count() == 0


def test_enabled_switch_runs_scan_and_writes_notification(
    world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SLA_WATCHER_ENABLED", "true")
    get_settings.cache_clear()

    result = sla_task.run_sla_monitor()

    assert result["notifications_written"] == 1
    notif = world.query(NotificationLog).one()
    assert notif.notify_type == "sla_overdue"
    assert notif.related_entity_type == "hub_issue"
    assert notif.recipient_user_id == 1
