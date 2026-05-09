"""SLAWatcher unit tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models import HubIssue, NotificationLog, Source, Ticket, User
from app.services.sla.watcher import SLAWatcher


@pytest.fixture
def base_world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add_all(
        [
            User(id=1, feishu_uid="ou_alice", name="alice", role="assignee"),
            User(id=2, feishu_uid="ou_bob", name="bob", role="assignee"),
            User(id=99, feishu_uid="ou_pool", name="pool", role="supervisor"),
        ]
    )
    db_session.commit()
    return db_session


# ---- ticket SLA ------------------------------------------------------------


def test_ticket_no_reply_past_threshold_emits_notification(
    base_world: Session,
) -> None:
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    base_world.add(
        Ticket(
            short_code="TKT-1",
            source_code="ksm",
            source_ticket_id="ksm-1",
            type="Raw",
            status="received",
            received_at=now - timedelta(hours=5),  # over 4h threshold
            assigned_user_id=1,
        )
    )
    base_world.commit()

    res = SLAWatcher(base_world).scan(now=now)
    assert res.notifications_written == 1
    assert res.overdue_ticket_ids == [1]

    notif = base_world.query(NotificationLog).first()
    assert notif is not None
    assert notif.recipient_user_id == 1
    assert notif.notify_type == "sla_overdue"
    assert notif.related_entity_type == "ticket"


def test_ticket_within_threshold_not_overdue(base_world: Session) -> None:
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    base_world.add(
        Ticket(
            short_code="TKT-1",
            source_code="ksm",
            source_ticket_id="ksm-1",
            type="Raw",
            status="received",
            received_at=now - timedelta(hours=2),
            assigned_user_id=1,
        )
    )
    base_world.commit()
    res = SLAWatcher(base_world).scan(now=now)
    assert res.notifications_written == 0


def test_ticket_with_reply_skipped(base_world: Session) -> None:
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    base_world.add(
        Ticket(
            short_code="TKT-1",
            source_code="ksm",
            source_ticket_id="ksm-1",
            type="Raw",
            status="replied",
            received_at=now - timedelta(hours=10),
            customer_replied_at=now - timedelta(hours=1),  # has reply
            assigned_user_id=1,
        )
    )
    base_world.commit()
    res = SLAWatcher(base_world).scan(now=now)
    assert res.notifications_written == 0


def test_ticket_done_status_skipped(base_world: Session) -> None:
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    base_world.add(
        Ticket(
            short_code="TKT-1",
            source_code="ksm",
            source_ticket_id="ksm-1",
            type="Raw",
            status="done",
            received_at=now - timedelta(hours=24),
            assigned_user_id=1,
        )
    )
    base_world.commit()
    res = SLAWatcher(base_world).scan(now=now)
    assert res.notifications_written == 0


def test_unassigned_ticket_falls_back_to_pool(base_world: Session) -> None:
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    base_world.add(
        Ticket(
            short_code="TKT-1",
            source_code="ksm",
            source_ticket_id="ksm-1",
            type="Raw",
            status="received",
            received_at=now - timedelta(hours=5),
            assigned_user_id=None,  # unassigned
        )
    )
    base_world.commit()
    res = SLAWatcher(base_world, fallback_recipient_id=99).scan(now=now)
    assert res.notifications_written == 1
    notif = base_world.query(NotificationLog).first()
    assert notif is not None
    assert notif.recipient_user_id == 99


def test_unassigned_ticket_without_pool_skipped(base_world: Session) -> None:
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    base_world.add(
        Ticket(
            short_code="TKT-1",
            source_code="ksm",
            source_ticket_id="ksm-1",
            type="Raw",
            status="received",
            received_at=now - timedelta(hours=5),
            assigned_user_id=None,
        )
    )
    base_world.commit()
    res = SLAWatcher(base_world).scan(now=now)
    assert res.notifications_written == 0
    assert res.skipped_unassigned == 1


def test_soft_deleted_ticket_skipped(base_world: Session) -> None:
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    base_world.add(
        Ticket(
            short_code="TKT-1",
            source_code="ksm",
            source_ticket_id="ksm-1",
            type="Raw",
            status="received",
            received_at=now - timedelta(hours=5),
            assigned_user_id=1,
            deleted_at=now,
        )
    )
    base_world.commit()
    res = SLAWatcher(base_world).scan(now=now)
    assert res.notifications_written == 0


# ---- hub_issue SLA --------------------------------------------------------


def test_hub_issue_per_type_threshold(base_world: Session) -> None:
    """Operation = 4h, Bug_fix = 8h. A 6h-old Operation overdues; 6h Bug_fix not."""
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    base_world.add_all(
        [
            HubIssue(
                short_code="HUB-1",
                type="Operation",
                title="op overdue",
                status="waiting_reply",
                first_seen_at=now - timedelta(hours=6),
                assigned_user_id=1,
            ),
            HubIssue(
                short_code="HUB-2",
                type="Bug_fix",
                title="bug not yet",
                status="waiting_schedule",
                first_seen_at=now - timedelta(hours=6),
                assigned_user_id=2,
            ),
        ]
    )
    base_world.commit()
    res = SLAWatcher(base_world).scan(now=now)
    assert res.notifications_written == 1
    assert res.overdue_hub_issue_ids == [1]

    notif = base_world.query(NotificationLog).first()
    assert notif is not None
    assert notif.recipient_user_id == 1
    assert notif.related_entity_id == 1


def test_hub_issue_resolved_skipped(base_world: Session) -> None:
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    base_world.add(
        HubIssue(
            short_code="HUB-1",
            type="Operation",
            title="resolved",
            status="done",
            first_seen_at=now - timedelta(hours=24),
            actual_resolved_at=now - timedelta(hours=1),
            assigned_user_id=1,
        )
    )
    base_world.commit()
    res = SLAWatcher(base_world).scan(now=now)
    assert res.notifications_written == 0


# ---- D2-C: per-product-line threshold overrides ---------------------------


def test_per_line_override_makes_ticket_overdue_earlier(base_world: Session) -> None:
    """cloud-fapiao has sla_reply_hours=2; a 3h-old ticket is overdue even though
    builtin default is 4h."""
    from app.models import ProductLine

    base_world.add(
        ProductLine(code="cloud-erp", name="Cloud ERP", sla_reply_hours=2)
    )
    base_world.commit()

    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    base_world.add(
        Ticket(
            short_code="TKT-fast",
            source_code="ksm",
            source_ticket_id="fast-1",
            type="Raw",
            status="received",
            product_line_code="cloud-erp",
            received_at=now - timedelta(hours=3),  # 3h, builtin 4h says NOT overdue, override 2h says overdue
            assigned_user_id=1,
        )
    )
    base_world.commit()
    res = SLAWatcher(base_world).scan(now=now)
    assert res.notifications_written == 1
    notif = base_world.query(NotificationLog).first()
    assert notif is not None
    assert notif.payload["threshold_hours"] == 2.0
    assert notif.payload["product_line_code"] == "cloud-erp"


def test_per_line_override_makes_ticket_NOT_overdue_longer_window(
    base_world: Session,
) -> None:
    """cloud-erp has sla_reply_hours=8; a 5h-old ticket is NOT overdue even
    though builtin default is 4h."""
    from app.models import ProductLine

    base_world.add(
        ProductLine(code="cloud-erp", name="Cloud ERP", sla_reply_hours=8)
    )
    base_world.commit()

    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    base_world.add(
        Ticket(
            short_code="TKT-slow",
            source_code="ksm",
            source_ticket_id="slow-1",
            type="Raw",
            status="received",
            product_line_code="cloud-erp",
            received_at=now - timedelta(hours=5),
            assigned_user_id=1,
        )
    )
    base_world.commit()
    res = SLAWatcher(base_world).scan(now=now)
    assert res.notifications_written == 0


def test_per_line_resolve_override_for_hub_issue(base_world: Session) -> None:
    """sla_resolve_hours overrides per-type defaults for that line."""
    from app.models import HubIssue, ProductLine

    base_world.add(
        ProductLine(code="cloud-erp", name="Cloud ERP", sla_resolve_hours=2)
    )
    base_world.commit()

    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    # Bug_fix builtin default is 8h; with override 2h, a 3h-old hub_issue is overdue
    base_world.add(
        HubIssue(
            short_code="HUB-fast",
            type="Bug_fix",
            title="bug w/ tight SLA",
            status="created",
            product_line_code="cloud-erp",
            first_seen_at=now - timedelta(hours=3),
            assigned_user_id=1,
        )
    )
    base_world.commit()
    res = SLAWatcher(base_world).scan(now=now)
    assert res.notifications_written == 1


def test_no_override_falls_back_to_builtin_default(base_world: Session) -> None:
    """sla_reply_hours stays NULL → SLAWatcher uses 4h default (already covered
    by existing tests; this is the 'control' case for explicitness)."""
    from app.models import ProductLine

    base_world.add(ProductLine(code="cloud-erp", name="Cloud ERP"))  # no overrides
    base_world.commit()
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)
    base_world.add(
        Ticket(
            short_code="TKT-default",
            source_code="ksm",
            source_ticket_id="default-1",
            type="Raw",
            status="received",
            product_line_code="cloud-erp",  # no override
            received_at=now - timedelta(hours=5),  # > 4h default
            assigned_user_id=1,
        )
    )
    base_world.commit()
    res = SLAWatcher(base_world).scan(now=now)
    assert res.notifications_written == 1
    notif = base_world.query(NotificationLog).first()
    assert notif is not None
    assert notif.payload["threshold_hours"] == 4.0
