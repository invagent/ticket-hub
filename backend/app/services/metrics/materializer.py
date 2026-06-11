"""Dashboard metrics materializer (D2-B).

Periodically (every 5 min via Celery beat) compute_dashboard_metrics() →
serialize to JSON → UPSERT into materialized_metrics(slot_key='latest').

`metrics/dashboard.py` reads the materialized row first; falls back to
on-the-fly aggregation when the table is empty (fresh DB / Celery down).

Why one row instead of N (history): the row is a cache, not a metric
time series. If we want history, we'd add a separate `metrics_snapshot`
table without TTL.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db import make_session
from app.models import MaterializedMetrics
from app.services.metrics.dashboard import (
    DashboardMetrics,
    compute_dashboard_metrics,
)

logger = get_logger(__name__)

LATEST_SLOT = "latest"


def _serialize(m: DashboardMetrics) -> dict[str, Any]:
    return {
        "counts": asdict(m.counts),
        "routing": asdict(m.routing),
        "supervisor": asdict(m.supervisor),
        "customer_dedup": asdict(m.customer_dedup),
        "sla": asdict(m.sla),
        "webhook_intake": asdict(m.webhook_intake),
    }


def upsert_metrics(db: Session, m: DashboardMetrics) -> MaterializedMetrics:
    """Insert or update the single 'latest' slot. Caller commits."""
    row = db.execute(
        select(MaterializedMetrics).where(MaterializedMetrics.slot_key == LATEST_SLOT)
    ).scalar_one_or_none()
    payload = _serialize(m)
    if row is None:
        row = MaterializedMetrics(
            slot_key=LATEST_SLOT,
            metrics_json=payload,
        )
        db.add(row)
        db.flush()
    else:
        row.metrics_json = payload
        row.refreshed_at = datetime.now(UTC)
        db.flush()
    return row


@shared_task(name="app.services.metrics.materializer.refresh_dashboard_metrics")
def refresh_dashboard_metrics() -> dict[str, Any]:
    """Celery task: compute + persist dashboard snapshot. Returns the
    serialized payload for observability (Celery result backend retains it
    briefly)."""
    db = make_session()
    try:
        metrics = compute_dashboard_metrics(db)
        upsert_metrics(db, metrics)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("materializer_refresh_failed")
        raise
    finally:
        db.close()

    payload = _serialize(metrics)
    logger.info(
        "materializer_refreshed",
        tickets_total=metrics.counts.tickets_total,
        auto_hit_rate=metrics.routing.auto_hit_rate,
        webhook_intake_24h=metrics.webhook_intake.total,
    )
    return payload
