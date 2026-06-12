"""Linear user sync (D4) — map ticket-hub users to Linear identities by email.

Populates users.linear_user_id + users.linear_team_id so the Linear push can
route a Bug_fix/Demand issue to its assignee's team (and set the assignee on
the Linear issue).

Matching:
    ticket-hub user.email  ==  Linear user.email   (case-insensitive)

Team resolution per matched user (denormalized into linear_team_id):
    - exactly one Linear team        → that team
    - multiple teams, default among  → the default team (settings.linear_team_id)
    - multiple teams, default absent → NULL  (push falls back to default team)
    - zero teams                     → NULL

Group accounts (数电开票组 …) have no email → never match → both fields stay
NULL → push falls back to the default LINEAR_TEAM_ID with no assignee. That
graceful degradation is intentional: as routing targets individuals and their
linear_user_id fills in, more issues land on the right team automatically.

Failure model mirrors FeishuUserSyncService: per-user errors are collected so a
partial batch still returns a useful report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from adapters.linear import LinearClient, LinearConfig, LinearUser
from app.config import get_settings
from app.core.logging import get_logger
from app.models import User

logger = get_logger(__name__)


@dataclass(slots=True)
class LinearSyncReport:
    matched_count: int = 0  # local users newly linked or refreshed
    cleared_count: int = 0  # previously-linked locals no longer in Linear
    skipped_no_email: int = 0  # local users without an email (groups, pools)
    unmatched_local: int = 0  # local users with email but no Linear match
    unmatched_linear: list[str] = field(default_factory=list)  # Linear emails w/o local user
    errors: list[dict[str, Any]] = field(default_factory=list)
    touched_user_ids: list[int] = field(default_factory=list)


def _resolve_team_id(lu: LinearUser, default_team_id: str) -> str | None:
    """Pick the team to route this user's issues to. See module docstring."""
    teams = lu.teams
    if len(teams) == 1:
        return teams[0].id
    if len(teams) > 1:
        if default_team_id and any(t.id == default_team_id for t in teams):
            return default_team_id
        return None  # ambiguous → fall back to default at push time
    return None


def sync_linear_users(
    db: Session,
    *,
    client: LinearClient | None = None,
) -> LinearSyncReport:
    """Match active ticket-hub users to Linear members by email and populate
    linear_user_id / linear_team_id. Commits on success."""
    settings = get_settings()
    if not settings.linear_api_key:
        raise RuntimeError("LINEAR_API_KEY not configured")

    owns_client = client is None
    if client is None:
        client = LinearClient(LinearConfig.from_settings(settings))
    try:
        linear_users = [u for u in client.list_users() if u.active and u.email]
    finally:
        if owns_client:
            client.close()

    by_email: dict[str, LinearUser] = {u.email.strip().lower(): u for u in linear_users}
    report = LinearSyncReport()

    locals_ = list(db.execute(select(User).where(User.deleted_at.is_(None))).scalars().all())
    matched_emails: set[str] = set()

    for user in locals_:
        try:
            email = (user.email or "").strip().lower()
            if not email:
                report.skipped_no_email += 1
                continue
            lu = by_email.get(email)
            if lu is None:
                # No Linear match. Clear any stale mapping so a removed member
                # doesn't keep mis-routing.
                if user.linear_user_id or user.linear_team_id:
                    user.linear_user_id = None
                    user.linear_team_id = None
                    report.cleared_count += 1
                    report.touched_user_ids.append(user.id)
                else:
                    report.unmatched_local += 1
                continue
            matched_emails.add(email)
            user.linear_user_id = lu.id
            user.linear_team_id = _resolve_team_id(lu, settings.linear_team_id)
            report.matched_count += 1
            report.touched_user_ids.append(user.id)
        except Exception as e:  # one bad row shouldn't sink the batch
            report.errors.append({"user_id": user.id, "error": str(e)})

    report.unmatched_linear = sorted(e for e in by_email if e not in matched_emails)
    db.commit()
    logger.info(
        "linear_user_sync_done",
        matched=report.matched_count,
        cleared=report.cleared_count,
        skipped_no_email=report.skipped_no_email,
        unmatched_local=report.unmatched_local,
        unmatched_linear=len(report.unmatched_linear),
        errors=len(report.errors),
    )
    return report
