"""Operation op_status 状态机统一入口（仿 status_cascade.apply_hub_status）.

改 op_status/op_handler/op_status_changed_at + 写 status_history。不 commit
（调用方负责事务边界）。映射驱动的底层动作（answered→author_reply、closed→关单
回写）不在这里做——由调用方在状态转换前后自行触发，保持本函数纯状态维护。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.logging import get_logger
from app.models import HubIssue
from app.repositories.status_history import StatusHistoryRepository

logger = get_logger(__name__)

OP_PROCESSING = "processing"
OP_ANSWERED = "answered"
OP_CLOSED = "closed"
OP_SUPPLEMENTING = "supplementing"
OP_REVIEWING = "reviewing"
OP_EXCEPTION = "exception"

_VALID = frozenset(
    {OP_PROCESSING, OP_ANSWERED, OP_CLOSED, OP_SUPPLEMENTING, OP_REVIEWING, OP_EXCEPTION}
)


def apply_op_status(
    db: Session,
    hub: HubIssue,
    *,
    to_status: str,
    handler: str,
    reason: str | None = None,
) -> bool:
    """转 Operation hub 的 op_status + op_handler。幂等（状态与处理人都没变 → no-op）。
    写 status_history（entity_type='hub_issue'）。不 commit。返回 True=有变更。
    """
    if to_status not in _VALID:
        raise ValueError(f"invalid op_status: {to_status!r}")
    if hub.op_status == to_status and hub.op_handler == handler:
        return False

    prev = hub.op_status
    hub.op_status = to_status
    hub.op_handler = handler
    hub.op_status_changed_at = datetime.now(UTC)
    StatusHistoryRepository(db).record(
        entity_type="hub_issue",
        entity_id=hub.id,
        from_status=prev,
        to_status=to_status,
        changed_by=f"op:{handler}",
        reason=reason,
        metadata={"op_handler": handler},
    )
    logger.info(
        "op_status_changed",
        hub_issue_id=hub.id,
        from_status=prev,
        to_status=to_status,
        handler=handler,
    )
    return True


def record_ticket_action(
    db: Session,
    hub: HubIssue,
    *,
    action: str,
    changed_by: str,
    reason: str,
) -> int:
    """给 hub 的每条关联 ticket 补写一条操作审计（entity_type='ticket'）。

    纯操作事件：不改 ticket 状态（from==to==当前状态），操作说明放 reason，
    metadata.action 标操作类型。用于把 hub 维度操作（答复 / 分类动作）投影到
    工单详情页左侧「处理节点」时间轴（该时间轴只查 entity_type='ticket'）。
    不 commit（复用调用方事务）。返回写入条数。
    """
    from app.models import Ticket

    tickets = (
        db.query(Ticket)
        .filter(Ticket.hub_issue_id == hub.id, Ticket.deleted_at.is_(None))
        .all()
    )
    repo = StatusHistoryRepository(db)
    for t in tickets:
        repo.record(
            entity_type="ticket",
            entity_id=t.id,
            from_status=t.status,
            to_status=t.status,
            changed_by=changed_by,
            reason=reason,
            metadata={"action": action},
        )
    return len(tickets)


def set_hub_tickets_handler(db: Session, hub: HubIssue, user_id: int) -> int:
    """把 hub 下所有未删关联 ticket 的处理人（handler_user_id）置为 user_id。

    处理人随「毕业运营分派 / 答复 / 转交」流动到工单。不 commit（复用调用方事务）。
    返回写入条数。
    """
    from app.models import Ticket

    tickets = (
        db.query(Ticket)
        .filter(Ticket.hub_issue_id == hub.id, Ticket.deleted_at.is_(None))
        .all()
    )
    for t in tickets:
        t.handler_user_id = user_id
    return len(tickets)


def resolve_supervisor_name(db: Session, settings: Settings | None = None) -> str:
    """人工介入处理人名：default_pool 对应 user.name；未配则 '主管'。"""
    settings = settings or get_settings()
    uid = settings.default_pool_user_id
    if uid is not None:
        from app.models import User

        u = db.get(User, uid)
        if u is not None and u.name:
            return str(u.name)
    return "主管"


def resolve_op_handler(db: Session, hub: HubIssue, settings: Settings | None = None) -> str:
    """转人工处理人名：优先预分配运营（op_handler_user_id 且 user 有效），
    否则回落 resolve_supervisor_name（default_pool 或 '主管'）。"""
    settings = settings or get_settings()
    uid = hub.op_handler_user_id
    if uid is not None:
        from app.models import User

        u = db.get(User, uid)
        if u is not None and u.deleted_at is None and u.is_active and u.name:
            return str(u.name)
    return resolve_supervisor_name(db, settings)


def close_overdue_answered(db: Session, *, settings: Settings | None = None) -> int:
    """answered 停留超 `operation_auto_close_days` 自然日未被驳回 → 自动 closed。

    扫描口径：type='Operation' + op_status=answered + 未删除 +
    op_status_changed_at <= now-N天。驳回（ksm_ingester）会把 op_status 转回
    processing 并刷新 op_status_changed_at，天然不会被本函数扫到——不需要额外
    判断。受 `operation_auto_close_enabled` 开关（关则直接返回 0，不查库）。

    只维护 op_status 业务层，不动 hub.status/ticket.status 底层机制：T+7 是纯
    超时关闭，没有外部事件驱动关单回写（不像 KSM/智齿主动关单，那边有真实
    ticket_status 变化要镜像）。底层状态继续留给主管人工判断是否需要 resolved/
    走 status_cascade；两层状态本就设计为并存不互相替代（同 ksm/writeback.py
    的 _close_local 注释）。逐个 apply_op_status 后由调用方 commit（仿 drain
    模式，一单出错不影响其他单）。
    """
    settings = settings or get_settings()
    if not settings.operation_auto_close_enabled:
        return 0

    cutoff = datetime.now(UTC) - timedelta(days=settings.operation_auto_close_days)
    stmt = (
        select(HubIssue.id)
        .where(
            HubIssue.type == "Operation",
            HubIssue.deleted_at.is_(None),
            HubIssue.op_status == OP_ANSWERED,
            HubIssue.op_status_changed_at <= cutoff,
        )
        .order_by(HubIssue.id)
    )
    hub_ids = list(db.scalars(stmt).all())

    closed = 0
    for hub_id in hub_ids:
        hub = db.get(HubIssue, hub_id)
        if hub is None:
            continue
        changed = apply_op_status(
            db,
            hub,
            to_status=OP_CLOSED,
            handler=hub.op_handler or "agent",
            reason=f"T+{settings.operation_auto_close_days} 未驳回自动关闭",
        )
        if changed:
            closed += 1
    return closed
