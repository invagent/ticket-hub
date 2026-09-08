"""Return request (退回 KSM) — 把转错模块的 KSM 工单打回重新分派。

处理人在工单详情页点「退回 KSM」，入一条 kind='return' 的 sync_outbox 行；KSM
sender 消费成 returnKsmOrder（退回，不关单）。退回是工单级动作（一个 ticket 对应
一个 KSM billId），不是 hub 级 fan-out——语义上"这个工单退回去重新分派"。

仅 KSM 来源工单可退回；其他来源无对应源系统退回接口。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
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
    # 退回目标节点改为退回执行时实时计算（2026-09 改判，见 writeback._refresh_for_return），
    # 这里只校验工单是否被我们系统接管过——未接管过的工单退回没有意义（KSM 侧
    # 归属未变，谈不上"打回重新分派"）。实时数据是否够用留给执行时判断，拉取
    # 失败会让该行 deferred/failed 转人工，不在入队前假设成功。
    if ticket.ksm_takeover_status not in {"locked", "handled"}:
        raise ReturnSyncError("工单尚未受理，无法退回")
    # 锁定防重复提交：同一工单已有一条 pending 的退回请求还没被 drain 消化时，
    # 拒绝再入队第二条——drain 是定时批处理（beat 每 2min 一轮，遇到延迟/积压
    # 可能几十分钟才真正执行），窗口内短时间连点几次会堆出多条 pending 行，
    # 一次性连续执行时目标节点在两个节点间来回弹，偶数次刚好弹回起点等于没退
    # （2026-09-07 TKT-006797/R20260904-0374 复现：4 次连续退回互相抵消，本地
    # 记成功但 KSM 侧节点原地不动）。同一工单一次只允许有一条在途退回。
    pending = db.execute(
        select(SyncOutbox.id).where(
            SyncOutbox.ticket_id == ticket.id,
            SyncOutbox.kind == "return",
            SyncOutbox.status == "pending",
        )
    ).first()
    if pending is not None:
        raise ReturnSyncError("已有一条退回请求正在处理中，请等待其完成后再操作")

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
