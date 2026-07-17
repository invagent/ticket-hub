"""Push a Bug_fix / Demand hub_issue to Linear (D4).

BackgroundTask body — never raises. The hub_issue stays linear_uuid=NULL on
any non-success so a later retry can push again (idempotent on linear_uuid).

Gates (all must hold, else skip with a log line):
    linear_push_enabled AND linear_api_key AND linear_team_id
    hub.type in (Bug_fix, Demand)        — ck_hub_issues_linear_fields
    hub.linear_uuid is NULL              — idempotency

Pending (待人工处理) write-back — instead of silently degrading:
    * assignee is an INDIVIDUAL (has email) but unknown to Linear
      (linear_user_id NULL — e.g. not in the workspace yet) → no push,
      hub.status='pending' + status_history with the reason
    * Linear API rejects/errors → hub.status='pending' + the error
    Group assignees (数电开票组 …, no email) keep the graceful fallback:
    default team, no assignee — that degradation is configured intent.
    A later successful push flips status back pending→created.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from adapters.linear import (
    CreateIssueRequest,
    LinearAuthError,
    LinearBusinessError,
    LinearClient,
    LinearConfig,
    LinearNetworkError,
)
from app.config import get_settings
from app.core.logging import get_logger
from app.db import make_session
from app.models import HubIssue, Ticket, User
from app.repositories.status_history import StatusHistoryRepository
from app.services.hub_issues.hub_dedup import maybe_supersede_duplicate

logger = get_logger(__name__)

# hub_issues.priority → Linear priority (0=None 1=Urgent 2=High 3=Medium 4=Low)
_PRIORITY_MAP = {"critical": 1, "high": 2, "medium": 3, "low": 4, "lowest": 4}


@dataclass(slots=True, frozen=True)
class LinearPushResult:
    hub_issue_id: int
    linear_uuid: str
    linear_identifier: str
    linear_url: str


def _mark_pending(db: Session, hub: HubIssue, *, reason: str) -> None:
    """Flip the hub_issue to 'pending' (待人工处理) with an audit trail.
    Commits — pending must survive even though the push itself failed."""
    prev = hub.status
    if prev == "pending":
        return  # already pending; don't spam history on every retry
    hub.status = "pending"
    StatusHistoryRepository(db).record(
        entity_type="hub_issue",
        entity_id=hub.id,
        from_status=prev,
        to_status="pending",
        changed_by="agent:linear_push",
        reason=reason,
    )
    db.commit()
    logger.warning("linear_push_pending", hub_issue_id=hub.id, reason=reason)


def _build_description(db: Session, hub: HubIssue) -> str:
    parts = [hub.canonical_body or ""]
    sources = (
        db.query(Ticket)
        .filter(Ticket.hub_issue_id == hub.id, Ticket.deleted_at.is_(None))
        .order_by(Ticket.id)
        .all()
    )
    if sources:
        refs = ", ".join(f"{t.short_code} ({t.source_code or 'internal'})" for t in sources)
        parts.append(f"\n---\nticket-hub: {hub.short_code} · source tickets: {refs}")
    return "\n".join(p for p in parts if p).strip()


def push_hub_issue_to_linear(
    hub_issue_id: int,
    db: Session | None = None,
    *,
    client: LinearClient | None = None,
) -> LinearPushResult | None:
    """Returns None when skipped or failed (logged); never raises."""
    settings = get_settings()
    own_session = db is None
    if own_session:
        db = make_session()
    assert db is not None

    try:
        if not (
            settings.linear_push_enabled and settings.linear_api_key and settings.linear_team_id
        ):
            logger.info("linear_push_disabled", hub_issue_id=hub_issue_id)
            return None
        hub = db.get(HubIssue, hub_issue_id)
        if hub is None or hub.deleted_at is not None:
            logger.warning("linear_push_hub_not_found", hub_issue_id=hub_issue_id)
            return None
        if hub.type not in ("Bug_fix", "Demand"):
            logger.info("linear_push_skip_type", hub_issue_id=hub_issue_id, type=hub.type)
            return None
        if hub.linear_uuid is not None:
            logger.info(
                "linear_push_already_pushed",
                hub_issue_id=hub_issue_id,
                linear_identifier=hub.linear_identifier,
            )
            return None
        # creator 毕业时已 hub-dedup 合并 → 不重复查/推
        if hub.superseded_by_hub_issue_id is not None:
            logger.info("linear_push_skip_superseded", hub_issue_id=hub_issue_id)
            return None

        # hub 级语义去重：与已推 Linear 的同产品线 hub 重复 → supersede，不重复建 issue
        # （creator 毕业时通常已查过；此处作 Bug/Demand 推前兜底，幂等）
        if settings.hub_dedup_enabled:
            dup_id = maybe_supersede_duplicate(db, hub)
            if dup_id is not None:
                return None

        # Per-assignee team routing: land the issue on the assignee's Linear
        # team (and set them as assignee). Group assignees (no email) fall
        # back to the default team; INDIVIDUALS unknown to Linear stop here
        # as 'pending' instead of silently losing their assignee.
        assignee_linear_id: str | None = None
        team_id = settings.linear_team_id
        if hub.assigned_user_id is not None:
            assignee = db.get(User, hub.assigned_user_id)
            if assignee is not None:
                if assignee.email and not assignee.linear_user_id:
                    _mark_pending(
                        db,
                        hub,
                        reason=(
                            f"处理人 {assignee.name}（{assignee.email}）在 Linear 工作区"
                            "查无此人，推送暂停待人工处理（加入 Linear 后执行"
                            " sync-from-linear 再重推）"
                        ),
                    )
                    return None
                assignee_linear_id = assignee.linear_user_id
                if assignee.linear_team_id:
                    team_id = assignee.linear_team_id

        req = CreateIssueRequest(
            title=f"[{hub.short_code}] {hub.title}",
            team_id=team_id,
            description=_build_description(db, hub),
            assignee_id=assignee_linear_id,
            priority=_PRIORITY_MAP.get(hub.priority or "", 0),
        )

        owns_client = client is None
        if client is None:
            client = LinearClient(LinearConfig.from_settings(settings))
        try:
            created = client.create_issue(req)
        except (LinearAuthError, LinearBusinessError, LinearNetworkError) as e:
            logger.warning("linear_push_failed", hub_issue_id=hub_issue_id, error=str(e))
            _mark_pending(db, hub, reason=f"Linear 推送失败：{e}")
            return None
        finally:
            if owns_client:
                client.close()

        hub.linear_uuid = created.id
        hub.linear_identifier = created.identifier
        hub.linear_status_synced_at = datetime.now(UTC)
        if hub.status == "pending":
            # A previously-stuck push now went through — back to normal flow.
            hub.status = "created"
            StatusHistoryRepository(db).record(
                entity_type="hub_issue",
                entity_id=hub.id,
                from_status="pending",
                to_status="created",
                changed_by="agent:linear_push",
                reason=f"Linear 重推成功（{created.identifier}），pending 解除",
            )
        db.commit()
        logger.info(
            "linear_push_ok",
            hub_issue_id=hub.id,
            linear_uuid=created.id,
            linear_identifier=created.identifier,
            url=created.url,
        )
        return LinearPushResult(
            hub_issue_id=hub.id,
            linear_uuid=created.id,
            linear_identifier=created.identifier,
            linear_url=created.url,
        )
    except Exception:  # defensive: BG task must not propagate
        if own_session:
            db.rollback()
        logger.exception("linear_push_unexpected_failure", hub_issue_id=hub_issue_id)
        return None
    finally:
        if own_session:
            db.close()
