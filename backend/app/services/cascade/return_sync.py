"""Return request (退回 KSM) — 把转错模块的 KSM 工单打回重新分派。

处理人在工单详情页点「退回 KSM」，入一条 kind='return' 的 sync_outbox 行；KSM
sender 消费成 returnKsmOrder（退回，不关单）。退回是工单级动作（一个 ticket 对应
一个 KSM billId），不是 hub 级 fan-out——语义上"这个工单退回去重新分派"。

仅 KSM 来源工单可退回；其他来源无对应源系统退回接口。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import SyncOutbox, Ticket
from app.repositories.status_history import StatusHistoryRepository

logger = get_logger(__name__)


class ReturnSyncError(Exception):
    """Return can't be requested; message is operator-facing."""


@dataclass(slots=True, frozen=True)
class ReturnResult:
    ticket_id: int
    outbox_id: int


def request_return(
    db: Session,
    ticket_id: int,
    *,
    deal_opinion: str,
    requested_by: str,
) -> ReturnResult:
    """入一条 return outbox 行，退回 KSM 重新分派。Commits。"""
    deal_opinion = (deal_opinion or "").strip()
    if not deal_opinion:
        raise ReturnSyncError("退回意见（处理说明）为空")

    ticket = db.get(Ticket, ticket_id)
    if ticket is None or ticket.deleted_at is not None:
        raise ReturnSyncError(f"工单 {ticket_id} 不存在或已删除")
    if ticket.source_code != "ksm" or not ticket.source_ticket_id:
        raise ReturnSyncError("仅 KSM 来源工单可退回")
    # 退回依赖持久化的「受理节点 opercacheId」（takeover 时写入，迁移 0038）。
    # 缺失 = 工单未受理 或 notice 已过期拿不到受理信息 → 退回必然失败，入队前拦截。
    if not (ticket.ksm_accept_opercache_id and ticket.ksm_current_node_id):
        raise ReturnSyncError("工单尚未受理（受理信息缺失），无法退回")

    row = SyncOutbox(
        kind="return",
        target_source_code=ticket.source_code,
        ticket_id=ticket.id,
        source_ticket_id=ticket.source_ticket_id,
        hub_issue_id=ticket.hub_issue_id,
        payload={
            "deal_opinion": deal_opinion,
            "requested_by": requested_by,
        },
    )
    db.add(row)
    db.flush()

    StatusHistoryRepository(db).record(
        entity_type="ticket",
        entity_id=ticket.id,
        from_status=ticket.status,
        to_status=ticket.status,
        changed_by=requested_by,
        reason=f"退回 KSM 重新分派: {deal_opinion[:120]}",
    )

    db.commit()
    logger.info(
        "return_requested",
        ticket_id=ticket.id,
        outbox_id=row.id,
        requested_by=requested_by,
    )
    return ReturnResult(ticket_id=ticket.id, outbox_id=row.id)
