"""Tests for the dashboard materializer (D2-B).

Doesn't exercise the Celery scheduler; just calls upsert_metrics + the
fallback logic in get_dashboard_metrics directly.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import MaterializedMetrics, Source, User
from app.services.metrics.dashboard import (
    compute_dashboard_metrics,
    get_dashboard_metrics,
)
from app.services.metrics.materializer import (
    LATEST_SLOT,
    refresh_dashboard_metrics,
    upsert_metrics,
)


def test_upsert_creates_row_first_time(db_session: Session) -> None:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(User(feishu_uid="u", name="x", role="member"))
    db_session.commit()
    m = compute_dashboard_metrics(db_session)
    upsert_metrics(db_session, m)
    db_session.commit()

    rows = db_session.query(MaterializedMetrics).all()
    assert len(rows) == 1
    assert rows[0].slot_key == LATEST_SLOT
    assert rows[0].metrics_json["counts"]["users_total"] == 1


def test_upsert_overwrites_existing(db_session: Session) -> None:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(User(feishu_uid="u", name="x", role="member"))
    db_session.commit()
    upsert_metrics(db_session, compute_dashboard_metrics(db_session))
    db_session.commit()

    db_session.add(User(feishu_uid="u2", name="y", role="member"))
    db_session.commit()
    upsert_metrics(db_session, compute_dashboard_metrics(db_session))
    db_session.commit()

    rows = db_session.query(MaterializedMetrics).all()
    assert len(rows) == 1  # still one row (UPSERT, not INSERT)
    assert rows[0].metrics_json["counts"]["users_total"] == 2


def test_get_dashboard_metrics_uses_materialized(db_session: Session) -> None:
    """Real metrics are 0 (empty DB), but a materialized row claims 999 —
    the API must return the materialized values."""
    db_session.add(
        MaterializedMetrics(
            slot_key=LATEST_SLOT,
            metrics_json={
                "counts": {
                    "tickets_total": 999,
                    "tickets_active": 999,
                    "hub_issues_total": 0,
                    "customers_total": 0,
                    "users_total": 0,
                    "notifications_pending": 0,
                },
                "routing": {
                    "tickets_total": 999,
                    "auto_assigned": 999,
                    "auto_hit_rate": 1.0,
                    "target": "≥ 0.95",
                },
                "supervisor": {
                    "linked_tickets": 0,
                    "relink_count": 0,
                    "relink_rate": 0.0,
                    "target": "< 0.10",
                },
                "customer_dedup": {
                    "identities_total": 0,
                    "identities_matched": 0,
                    "match_rate": 0.0,
                    "target": "≥ 0.90",
                },
                "sla": {
                    "notifications_total": 0,
                    "pending": 0,
                    "acknowledged": 0,
                    "escalated": 0,
                    "acknowledgement_rate": 0.0,
                    "target": "≥ 0.90",
                },
                "webhook_intake": {
                    "window_hours": 24,
                    "by_source": {"ksm": 5},
                    "total": 5,
                    "deduped_total": 0,
                },
            },
        )
    )
    db_session.commit()

    m = get_dashboard_metrics(db_session)
    assert m.counts.tickets_total == 999  # from materialized, not live compute
    assert m.routing.auto_hit_rate == 1.0


def test_get_dashboard_metrics_fallback_when_no_row(db_session: Session) -> None:
    """No materialized row → fall back to on-the-fly compute."""
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(User(feishu_uid="u", name="x", role="member"))
    db_session.commit()

    m = get_dashboard_metrics(db_session)
    assert m.counts.users_total == 1


def test_get_dashboard_metrics_fallback_on_corrupted_payload(
    db_session: Session,
) -> None:
    """Schema drift between writer/reader → fall through to live compute."""
    db_session.add(
        MaterializedMetrics(
            slot_key=LATEST_SLOT,
            metrics_json={"counts": "totally wrong"},  # malformed
        )
    )
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(User(feishu_uid="u", name="x", role="member"))
    db_session.commit()

    m = get_dashboard_metrics(db_session)
    # Should fall back to live compute
    assert m.counts.users_total == 1


def test_refresh_task_updates_row(db_session: Session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Celery task body exercises make_session → live compute → upsert.

    We monkeypatch make_session to return the test session so we don't
    need real Celery / Redis."""
    from app.services.metrics import materializer

    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(User(feishu_uid="u", name="x", role="member"))
    db_session.commit()

    class _Stub:
        def __call__(self):
            return db_session

    # The task closes the session at the end; wrap to keep test session alive.
    class _NoCloseSession:
        def __init__(self, real: Session) -> None:
            self._real = real

        def __getattr__(self, name: str):
            return getattr(self._real, name)

        def close(self) -> None:
            pass

    monkeypatch.setattr(materializer, "make_session", lambda: _NoCloseSession(db_session))

    payload = refresh_dashboard_metrics()
    assert payload["counts"]["users_total"] == 1
    rows = db_session.query(MaterializedMetrics).all()
    assert len(rows) == 1
