"""SupervisorRerouteService — 手动重新触发路由分配。

对每个 ticket 执行：
  1. 校验 ticket 存在且未软删除
  2. 调用 Router.route() 获取 RouteDecision
  3. 根据 decision 决定写入 assigned_user_id
  4. 写 status_history 审计记录
  5. 返回每条 ticket 的 RerouteItemResult

调用方（API 端点）负责 db.commit()。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import get_settings  # noqa: F401 kept for other potential uses
from app.core.logging import get_logger
from app.models import Ticket
from app.repositories.status_history import StatusHistoryRepository
from app.repositories.ticket import TicketRepository
from app.services.routing.router import Router, RouteRequest
from app.services.system_settings import get_default_pool_user_id

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class RerouteRequest:
    ticket_ids: list[int]
    operator_user_id: int


@dataclass(slots=True, frozen=True)
class RerouteItemResult:
    ticket_id: int
    short_code: str
    success: bool
    decision: str  # 'assigned'|'default_pool'|'multi_match'|'no_match'|'not_found'
    assigned_user_ids: list[int] = field(default_factory=list)
    message: str = ""


@dataclass(slots=True, frozen=True)
class RerouteResult:
    results: list[RerouteItemResult]
    assigned_count: int
    no_match_count: int


class RerouteService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def reroute(self, req: RerouteRequest) -> RerouteResult:
        router = Router(self._db, default_pool_user_id=get_default_pool_user_id(self._db))
        ticket_repo = TicketRepository(self._db)
        history_repo = StatusHistoryRepository(self._db)

        tickets = ticket_repo.list_by_ids(req.ticket_ids)
        found = {t.id: t for t in tickets}
        results: list[RerouteItemResult] = []

        for tid in req.ticket_ids:
            if tid not in found:
                results.append(
                    RerouteItemResult(
                        ticket_id=tid,
                        short_code="",
                        success=False,
                        decision="not_found",
                        assigned_user_ids=[],
                        message=f"工单 {tid} 不存在或已删除",
                    )
                )
                continue

            ticket = found[tid]
            decision = router.route(
                RouteRequest(
                    ticket_id=ticket.id,
                    source_code=ticket.source_code or "",
                    product_line_code=ticket.product_line_code,
                    raw_module=ticket.module,
                    raw_feature=ticket.feature,
                    customer_id=ticket.customer_identity_id,
                )
            )

            new_assigned_id: int | None = None
            result_decision: str = decision.decision
            success = False

            if (decision.decision == "assigned" and decision.assigned_user_ids) or (
                decision.decision == "default_pool" and decision.assigned_user_ids
            ):
                new_assigned_id = decision.assigned_user_ids[0]
                success = True
            elif decision.decision == "default_pool" and not decision.assigned_user_ids:
                result_decision = "no_match"
            # multi_match → success=False，不自动写入，需人工介入

            if new_assigned_id is not None:
                self._db.execute(
                    update(Ticket)
                    .where(Ticket.id == ticket.id)
                    .values(assigned_user_id=new_assigned_id)
                )
                logger.info(
                    "supervisor_reroute_assigned",
                    ticket_id=ticket.id,
                    assigned_user_id=new_assigned_id,
                    decision=result_decision,
                    operator_user_id=req.operator_user_id,
                )

            history_repo.record(
                entity_type="ticket",
                entity_id=ticket.id,
                from_status=ticket.status,
                to_status=ticket.status,
                changed_by="system:reroute",
                reason=f"supervisor reroute by user_id={req.operator_user_id}",
                metadata={
                    "decision": result_decision,
                    "assigned_user_ids": decision.assigned_user_ids,
                    "rationale": decision.rationale,
                    "operator_user_id": req.operator_user_id,
                },
            )

            results.append(
                RerouteItemResult(
                    ticket_id=ticket.id,
                    short_code=ticket.short_code,
                    success=success,
                    decision=result_decision,
                    assigned_user_ids=decision.assigned_user_ids,
                    message=_build_message(result_decision, decision.assigned_user_ids),
                )
            )

        self._db.flush()

        assigned_count = sum(1 for r in results if r.success)
        no_match_count = len(results) - assigned_count
        return RerouteResult(
            results=results,
            assigned_count=assigned_count,
            no_match_count=no_match_count,
        )


def _build_message(decision: str, assigned_user_ids: list[int]) -> str:
    if decision == "assigned":
        return f"已分配给用户 {assigned_user_ids[0]}（模块/功能匹配）"
    if decision == "default_pool":
        return f"已分配到默认处理池（用户 {assigned_user_ids[0]}）"
    if decision == "multi_match":
        return f"多组匹配，需人工确认（候选用户：{assigned_user_ids}）"
    if decision == "no_match":
        return "未匹配到处理人，且未配置默认处理池"
    return "工单不存在或已删除"
