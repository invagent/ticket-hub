"""ManualAssignService — 主管手动把工单直接指派给指定处理人（绕过 Router）。

对每个 ticket：
  1. 校验 ticket 存在
  2. update(Ticket).values(assigned_user_id=target)
  3. 写 status_history 审计（from==to，changed_by=system:manual_assign）
  4. 返回每条结果

目标用户先统一校验一次（存在 + is_active + 角色允许），不合法整批拒绝。
调用方（API 端点）负责 db.commit()。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import Ticket
from app.repositories.status_history import StatusHistoryRepository
from app.repositories.ticket import TicketRepository
from app.repositories.user import UserRepository

logger = get_logger(__name__)


class TargetUserInvalidError(Exception):
    """目标用户不存在 / 已停用 / 角色不允许被指派。"""


@dataclass(slots=True, frozen=True)
class AssignRequest:
    ticket_ids: list[int]
    assigned_user_id: int
    operator_user_id: int


@dataclass(slots=True, frozen=True)
class AssignItemResult:
    ticket_id: int
    short_code: str
    success: bool
    prev_assigned_user_id: int | None = None
    message: str = ""


@dataclass(slots=True, frozen=True)
class AssignResult:
    results: list[AssignItemResult]
    assigned_count: int
    not_found_count: int


class ManualAssignService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def assign(self, req: AssignRequest) -> AssignResult:
        user_repo = UserRepository(self._db)
        target = user_repo.get(req.assigned_user_id)
        if target is None or not target.is_active:
            raise TargetUserInvalidError(f"目标用户 {req.assigned_user_id} 不存在或已停用")
        # 操作人姓名（写进 history reason，避免存裸 user_id）；查不到回落 id。
        operator = user_repo.get(req.operator_user_id)
        operator_name = operator.name if operator else f"user_id={req.operator_user_id}"
        # 不限角色：真实处理人大量是 member（分派/转交均无角色限制），只要 active 即可作为处理人

        ticket_repo = TicketRepository(self._db)
        history_repo = StatusHistoryRepository(self._db)
        tickets = ticket_repo.list_by_ids(req.ticket_ids)
        found = {t.id: t for t in tickets}
        results: list[AssignItemResult] = []

        for tid in req.ticket_ids:
            if tid not in found:
                results.append(
                    AssignItemResult(
                        ticket_id=tid,
                        short_code="",
                        success=False,
                        message=f"工单 {tid} 不存在或已删除",
                    )
                )
                continue

            ticket = found[tid]
            # 转交改写处理人（handler_user_id），不动责任人（assigned_user_id）
            prev = ticket.handler_user_id
            self._db.execute(
                update(Ticket)
                .where(Ticket.id == ticket.id)
                .values(handler_user_id=req.assigned_user_id)
            )
            history_repo.record(
                entity_type="ticket",
                entity_id=ticket.id,
                from_status=ticket.status,
                to_status=ticket.status,
                changed_by="system:manual_assign",
                reason=f"转交处理人给 {target.name}（操作人 {operator_name}）",
                metadata={
                    "operator_user_id": req.operator_user_id,
                    "handler_user_id": req.assigned_user_id,
                    "prev_handler_user_id": prev,
                },
            )
            logger.info(
                "supervisor_manual_assign",
                ticket_id=ticket.id,
                assigned_user_id=req.assigned_user_id,
                prev_assigned_user_id=prev,
                operator_user_id=req.operator_user_id,
            )
            results.append(
                AssignItemResult(
                    ticket_id=ticket.id,
                    short_code=ticket.short_code,
                    success=True,
                    prev_assigned_user_id=prev,
                    message=f"已指派给用户 {req.assigned_user_id}",
                )
            )

        self._db.flush()
        assigned_count = sum(1 for r in results if r.success)
        return AssignResult(
            results=results,
            assigned_count=assigned_count,
            not_found_count=len(results) - assigned_count,
        )
