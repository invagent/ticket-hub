"""Linear status back-sync (D4 第①段) — poll Linear, write back hub_issues.

Celery beat task (every 5 min): scan hub_issues that were pushed to Linear
(linear_uuid NOT NULL) and refresh their dev-side state.

Write-back is two-layered:

    linear_status (display)   — ALWAYS mirrored: the Linear column name
                                ("In Progress", "Done", …) + synced_at
    hub_issue.status (cascade) — CONSERVATIVE mapping, only the
                                unambiguous transitions:
        state_type 'started'   → status 'in_progress'
        state_type 'completed' → status 'released' (+ actual_released_at)
        state_type 'canceled'  → NO status change — a dev cancelling an
                                 issue needs a supervisor's judgment (re-push?
                                 reject? reply to customer?), so it only
                                 surfaces via linear_status.
        triage/backlog/unstarted → record only.

    Reopens are honored: released → in_progress when Linear moves an issue
    back to started (Linear is the source of truth for dev state).

Issues missing from the Linear response (deleted over there) are counted in
the report but left untouched — same supervisor-judgment reasoning.

Every status transition writes status_history (changed_by
'agent:linear_status_sync'). Poll-only by design; a /webhook/linear upgrade
can land later without changing the write-back layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.linear import (
    LinearAuthError,
    LinearBusinessError,
    LinearClient,
    LinearConfig,
    LinearNetworkError,
)
from app.config import get_settings
from app.core.logging import get_logger
from app.db import make_session
from app.models import HubIssue
from app.services.cascade.status_cascade import apply_hub_status

logger = get_logger(__name__)

# state_type → hub_issue.status (only unambiguous transitions; see docstring)
_CASCADE_MAP = {
    "started": "in_progress",
    "completed": "released",
}

_SCAN_LIMIT = 200  # most-recently-updated first; plenty at current volume


@dataclass(slots=True)
class StatusSyncReport:
    scanned: int = 0
    status_changed: int = 0  # hub_issue.status transitions
    linear_status_refreshed: int = 0  # display-layer updates (incl. transitions)
    missing_in_linear: int = 0  # pushed but Linear no longer returns them
    failed: bool = False


def sync_linear_statuses(
    db: Session,
    *,
    client: LinearClient | None = None,
) -> StatusSyncReport:
    """One polling pass. Commits on success; never raises on Linear errors
    (report.failed=True instead) — beat must keep ticking."""
    report = StatusSyncReport()
    settings = get_settings()
    if not settings.linear_api_key:
        logger.info("linear_status_sync_disabled")
        return report

    hubs = list(
        db.execute(
            select(HubIssue)
            .where(HubIssue.deleted_at.is_(None), HubIssue.linear_uuid.isnot(None))
            .order_by(HubIssue.updated_at.desc())
            .limit(_SCAN_LIMIT)
        )
        .scalars()
        .all()
    )
    report.scanned = len(hubs)
    if not hubs:
        return report

    owns_client = client is None
    if client is None:
        client = LinearClient(LinearConfig.from_settings(settings))
    try:
        states = client.get_issue_states([h.linear_uuid for h in hubs if h.linear_uuid])
    except (LinearAuthError, LinearBusinessError, LinearNetworkError) as e:
        logger.warning("linear_status_sync_failed", error=str(e))
        report.failed = True
        return report
    finally:
        if owns_client:
            client.close()

    by_uuid = {s.id: s for s in states}
    now = datetime.now(UTC)

    for hub in hubs:
        state = by_uuid.get(hub.linear_uuid or "")
        if state is None:
            report.missing_in_linear += 1
            continue

        if hub.linear_status != state.state_name:
            hub.linear_status = state.state_name
            hub.linear_status_synced_at = now
            report.linear_status_refreshed += 1

        mapped = _CASCADE_MAP.get(state.state_type)
        if mapped is None or hub.status == mapped:
            continue
        # 决策 14: hub 状态变更统一走 status_cascade —— hub history +
        # 级联源工单 + sync_outbox 入队，一处语义。
        cascade = apply_hub_status(
            db,
            hub,
            to_status=mapped,
            changed_by="agent:linear_status_sync",
            reason=f"Linear {state.identifier} → {state.state_name} ({state.state_type})",
        )
        if cascade.changed:
            report.status_changed += 1

    db.commit()
    logger.info(
        "linear_status_sync_done",
        scanned=report.scanned,
        status_changed=report.status_changed,
        linear_status_refreshed=report.linear_status_refreshed,
        missing_in_linear=report.missing_in_linear,
    )
    return report


@shared_task(name="app.services.hub_issues.linear_status_sync.poll_linear_statuses")  # type: ignore[untyped-decorator]  # celery decorator is untyped
def poll_linear_statuses() -> dict[str, int | bool]:
    """Celery beat entrypoint. Own session; swallows everything."""
    db = make_session()
    try:
        report = sync_linear_statuses(db)
        return {
            "scanned": report.scanned,
            "status_changed": report.status_changed,
            "linear_status_refreshed": report.linear_status_refreshed,
            "missing_in_linear": report.missing_in_linear,
            "failed": report.failed,
        }
    except Exception:
        db.rollback()
        logger.exception("linear_status_sync_unexpected_failure")
        return {"failed": True}
    finally:
        db.close()
