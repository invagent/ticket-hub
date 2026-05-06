"""Router — assign incoming ticket to user / partner-group / default_pool.

Algorithm (decision 20, upgrade_plan.md §4.12.2):

    Step 1  module match: query assignment_scopes_module by (product_line, module)
            - 0 hits   → fall through to step 2
            - 1 group  → assigned (single_assignee or partner-group)
            - >1 group → multi_match  (Conflict Detect Agent triggers split — D3)

    Step 2  feature fallback: query assignment_scopes_feature by feature
            - 0 hits   → fall through to step 3
            - 1 group  → assigned
            - >1 group → multi_match

    Step 3  default_pool: assign to the configured fallback user (configurable
            per product_line; D1 uses settings.default_pool_user_id).

Two users in the same `user_partners` row count as a single routing unit
(group_by_partner). Routing decisions are persisted via agent_decisions in D3;
for D1 we return RouteDecision and the caller writes audit entries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.repositories.assignment_scope import (
    AssignmentScopeRepository,
    UserPartnerRepository,
)

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class RouteRequest:
    ticket_id: int
    source_code: str
    raw_module: str | None = None
    raw_feature: str | None = None
    customer_id: int | None = None
    product_line_code: str | None = None


Decision = Literal["assigned", "multi_match", "default_pool"]
MatchedScope = Literal["module", "feature", "none"]


@dataclass(slots=True, frozen=True)
class RouteDecision:
    """Outcome of a route() call.

    `assigned_user_ids` semantics:
      - `assigned`     → one or more user IDs (>1 means partner group; load-balance among them)
      - `multi_match`  → all candidate user IDs across multiple groups
                         (caller routes through Conflict Detect Agent for split)
      - `default_pool` → 0 or 1 user IDs (0 if default_pool not configured)
    """

    ticket_id: int
    decision: Decision
    assigned_user_ids: list[int] = field(default_factory=list)
    matched_scope: MatchedScope = "none"
    matched_scope_id: int | None = None
    rationale: str = ""
    confidence: float = 0.0


class Router:
    def __init__(
        self,
        db: Session,
        *,
        default_pool_user_id: int | None = None,
    ) -> None:
        self._db = db
        self._scope_repo = AssignmentScopeRepository(db)
        self._partner_repo = UserPartnerRepository(db)
        self._default_pool_user_id = default_pool_user_id

    def route(self, req: RouteRequest) -> RouteDecision:
        # Step 1: module
        if req.product_line_code and req.raw_module:
            module_uids = self._scope_repo.find_user_ids_by_module(
                req.product_line_code, req.raw_module
            )
            if module_uids:
                groups = self._partner_repo.group_by_partner(module_uids)
                if len(groups) == 1:
                    return self._assigned(
                        req,
                        list(groups[0]),
                        scope="module",
                        rationale=(
                            f"module match: product_line={req.product_line_code}"
                            f" module={req.raw_module}"
                        ),
                        confidence=0.95,
                    )
                # multi-group hit — caller will trigger Conflict Detect Agent
                return self._multi_match(
                    req,
                    [u for g in groups for u in g],
                    scope="module",
                    rationale=(
                        f"module hit {len(groups)} partner groups "
                        f"(product_line={req.product_line_code} module={req.raw_module})"
                    ),
                )

        # Step 2: feature
        if req.raw_feature:
            feature_uids = self._scope_repo.find_user_ids_by_feature(req.raw_feature)
            if feature_uids:
                groups = self._partner_repo.group_by_partner(feature_uids)
                if len(groups) == 1:
                    return self._assigned(
                        req,
                        list(groups[0]),
                        scope="feature",
                        rationale=f"feature fallback: {req.raw_feature}",
                        confidence=0.75,
                    )
                return self._multi_match(
                    req,
                    [u for g in groups for u in g],
                    scope="feature",
                    rationale=(
                        f"feature hit {len(groups)} partner groups (feature={req.raw_feature})"
                    ),
                )

        # Step 3: default_pool
        if self._default_pool_user_id is not None:
            return RouteDecision(
                ticket_id=req.ticket_id,
                decision="default_pool",
                assigned_user_ids=[self._default_pool_user_id],
                matched_scope="none",
                rationale="no module / feature scope hit; sent to default pool",
                confidence=0.0,
            )

        return RouteDecision(
            ticket_id=req.ticket_id,
            decision="default_pool",
            assigned_user_ids=[],
            matched_scope="none",
            rationale="no scope hit AND no default_pool configured",
            confidence=0.0,
        )

    # ---- internal ------------------------------------------------------

    def _assigned(
        self,
        req: RouteRequest,
        user_ids: list[int],
        *,
        scope: MatchedScope,
        rationale: str,
        confidence: float,
    ) -> RouteDecision:
        return RouteDecision(
            ticket_id=req.ticket_id,
            decision="assigned",
            assigned_user_ids=sorted(user_ids),
            matched_scope=scope,
            rationale=rationale,
            confidence=confidence,
        )

    def _multi_match(
        self,
        req: RouteRequest,
        user_ids: list[int],
        *,
        scope: MatchedScope,
        rationale: str,
    ) -> RouteDecision:
        return RouteDecision(
            ticket_id=req.ticket_id,
            decision="multi_match",
            assigned_user_ids=sorted(set(user_ids)),
            matched_scope=scope,
            rationale=rationale,
            confidence=0.5,
        )
