"""Operation op_status 状态机统一入口（仿 status_cascade.apply_hub_status）.

改 op_status/op_handler/op_status_changed_at + 写 status_history。不 commit
（调用方负责事务边界）。映射驱动的底层动作（answered→author_reply、closed→关单
回写）不在这里做——由调用方在状态转换前后自行触发，保持本函数纯状态维护。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import HubIssue
from app.repositories.status_history import StatusHistoryRepository

logger = get_logger(__name__)

OP_PROCESSING = "processing"
OP_ANSWERED = "answered"
OP_CLOSED = "closed"
OP_SUPPLEMENTING = "supplementing"
OP_RESUPPLIED = "resupplied"
OP_EXCEPTION = "exception"

_VALID = frozenset(
    {OP_PROCESSING, OP_ANSWERED, OP_CLOSED, OP_SUPPLEMENTING, OP_RESUPPLIED, OP_EXCEPTION}
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
