"""hub_issue creation from a classified ticket (D4).

A ticket graduates into a hub_issue once its type is known (LLM classify or
supervisor judgment). The hub_issue carries the 出口-type semantics
(Operation/Bug_fix/Demand/Internal_task) and is what downstream exits
consume (Linear push for Bug_fix/Demand, reply flow for Operation, ...).

Trigger model (mirrors split's 灰度 playbook):
    - auto path: after classify, when hub_issue_auto_enabled AND
      predicted_confidence >= hub_issue_auto_confidence
    - manual path: POST /api/supervisor/create-hub-issue (no confidence
      gate — supervisor judgment overrides), optional explicit type

Both call ensure_hub_issue_for_ticket(). Idempotent: a ticket already
linked to a hub_issue is never re-created (returns the existing link).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.logging import get_logger
from app.db import make_session
from app.models import HubIssue, Ticket, TicketHubIssueHistory
from app.repositories.status_history import StatusHistoryRepository
from app.services.hub_issues.hub_dedup import maybe_supersede_duplicate

logger = get_logger(__name__)

_VALID_TYPES = frozenset({"Operation", "Bug_fix", "Demand", "Internal_task"})


class HubIssueCreateError(Exception):
    """Ticket can't graduate to a hub_issue; message is operator-facing."""


@dataclass(slots=True, frozen=True)
class HubIssueResult:
    hub_issue_id: int
    hub_issue_short_code: str
    ticket_id: int
    type: str
    created: bool  # False when the ticket was already linked


def _next_hub_short_code(db: Session) -> str:
    n: int | None = db.execute(select(func.count(HubIssue.id))).scalar()
    return f"HUB-{(n or 0) + 1:06d}"


def ensure_hub_issue_for_ticket(
    ticket_id: int,
    *,
    created_by: str,
    type_override: str | None = None,
    db: Session,
) -> HubIssueResult:
    """Create a hub_issue from a ticket and link them. Commits on success.

    Type comes from `type_override` (supervisor) or ticket.predicted_type
    (auto path — caller enforces the confidence gate). Raises
    HubIssueCreateError when neither yields a valid type.
    """
    ticket = db.get(Ticket, ticket_id)
    if ticket is None or ticket.deleted_at is not None:
        raise HubIssueCreateError(f"ticket {ticket_id} not found")
    if ticket.hub_issue_id is not None:
        hub = db.get(HubIssue, ticket.hub_issue_id)
        return HubIssueResult(
            hub_issue_id=ticket.hub_issue_id,
            hub_issue_short_code=hub.short_code if hub else "",
            ticket_id=ticket.id,
            type=hub.type if hub else "",
            created=False,
        )
    if ticket.type == "Parent":
        # A split parent is a container; its children graduate individually.
        raise HubIssueCreateError(f"ticket {ticket_id} is a split Parent — graduate its children")

    issue_type = type_override or ticket.predicted_type
    # ADR-0016 P2a：投诉不毕业 hub_issue（停 ticket 层转人工）。type_override
    # 允许主管把投诉转成 Op/Bug/Demand 后毕业，故只在无 override 时挡。
    if type_override is None and issue_type == "Complaint":
        raise HubIssueCreateError(
            f"ticket {ticket_id} is Complaint — 投诉停 ticket 层转人工，不自动毕业 hub_issue"
        )
    if issue_type not in _VALID_TYPES:
        raise HubIssueCreateError(
            f"ticket {ticket_id} has no valid type (predicted={ticket.predicted_type!r}, "
            f"override={type_override!r})"
        )
    if not (ticket.title or "").strip():
        raise HubIssueCreateError(f"ticket {ticket_id} has no title")

    hub = HubIssue(
        short_code=_next_hub_short_code(db),
        type=issue_type,
        title=(ticket.title or "").strip(),
        canonical_body=ticket.body,
        product_line_code=ticket.product_line_code,
        module=ticket.module,
        status="created",
        assigned_user_id=ticket.assigned_user_id,
        occurrence_count=1,
    )
    db.add(hub)
    db.flush()  # need hub.id for the link

    # ADR-0016 §2.1：所有类型毕业时 hub_dedup 查重（不只 Bug/Demand 推 Linear 前）。
    # 命中则当前 hub supersede 到原 hub，ticket 挂原 hub，占用复用不重复毕业。
    if get_settings().hub_dedup_enabled:
        dup_id = maybe_supersede_duplicate(db, hub)
        if dup_id is not None:
            ticket.hub_issue_id = dup_id
            db.add(
                TicketHubIssueHistory(
                    ticket_id=ticket.id,
                    hub_issue_id=dup_id,
                    change_reason=f"hub-dedup 合并到 #{dup_id}（{created_by}）",
                    human_confirmed=created_by.startswith("user:"),
                )
            )
            db.commit()
            dup = db.get(HubIssue, dup_id)
            logger.info("hub_issue_dedup_merged", ticket_id=ticket.id, dup_hub_id=dup_id)
            return HubIssueResult(
                hub_issue_id=dup_id,
                hub_issue_short_code=dup.short_code if dup else "",
                ticket_id=ticket.id,
                type=dup.type if dup else issue_type,
                created=False,
            )

    ticket.hub_issue_id = hub.id
    db.add(
        TicketHubIssueHistory(
            ticket_id=ticket.id,
            hub_issue_id=hub.id,
            change_reason=f"created by {created_by}",
            human_confirmed=created_by.startswith("user:"),
        )
    )
    StatusHistoryRepository(db).record(
        entity_type="hub_issue",
        entity_id=hub.id,
        from_status=None,
        to_status="created",
        changed_by=created_by,
        reason=f"graduated from ticket {ticket.short_code}",
        metadata={"ticket_id": ticket.id, "type": issue_type},
    )
    db.commit()
    logger.info(
        "hub_issue_created",
        hub_issue_id=hub.id,
        hub_short_code=hub.short_code,
        ticket_id=ticket.id,
        type=issue_type,
        created_by=created_by,
    )
    return HubIssueResult(
        hub_issue_id=hub.id,
        hub_issue_short_code=hub.short_code,
        ticket_id=ticket.id,
        type=issue_type,
        created=True,
    )


def create_hub_issue_for_ticket_auto(ticket_id: int) -> HubIssueResult | None:
    """Auto-path convenience (post-ingest chain): own session, swallows
    errors, then chains the Linear push for Bug_fix/Demand. The caller has
    already verified the confidence gate."""
    from app.services.hub_issues.linear_push import push_hub_issue_to_linear

    db = make_session()
    try:
        result = ensure_hub_issue_for_ticket(ticket_id, created_by="agent:hub_issue_auto", db=db)
    except HubIssueCreateError as e:
        db.rollback()
        logger.warning("hub_issue_auto_skipped", ticket_id=ticket_id, error=str(e))
        return None
    except Exception:
        db.rollback()
        logger.exception("hub_issue_auto_unexpected_failure", ticket_id=ticket_id)
        return None
    finally:
        db.close()

    if result.created and result.type in ("Bug_fix", "Demand"):
        push_hub_issue_to_linear(result.hub_issue_id)
    return result
