"""stage 自动维护：SQLAlchemy Session `before_flush` 监听器（ADR-0017 D2 方案 A）。

任何 session（请求、Celery、脚本、单测）flush 前，对本次变脏/新建的 Ticket 与
HubIssue 重算 `stage`；hub 变化自动传播到其挂载的全部 ticket。**旧 4 字段的 27
处裸赋值零改动即被覆盖**——这是「映射层保证新旧对账」的落点。

stage 发生变化时写一条 `status_history(entity_type='ticket_stage' | 'hub_stage')`，
actor 取 `session.info.get("stage_actor")`（调用方可在事务开头设置），默认
`system:stage_sync`。

注册方式：`app/models.py` 末尾 `import app.services.state.listeners`。本模块**不在
import 时**引用任何 ORM 类（在函数内延迟 import），避免 models ↔ services 循环。

约束：
- 监听器内查询关联 ticket 必须在 `session.no_autoflush` 下，防递归 flush。
- 永不抛：派生失败只记日志，不影响业务事务。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.services.state.stage import (
    derive_hub_stage,
    derive_ticket_stage,
    explain_stage_driver,
)

logger = get_logger(__name__)

DEFAULT_STAGE_ACTOR = "system:stage_sync"
STAGE_ACTOR_KEY = "stage_actor"

_TICKET_WATCH = ("status", "type", "predicted_type", "hub_issue_id", "stage")
_HUB_WATCH = ("status", "op_status", "linear_status", "type", "stage")

_registered = False


def _changed(obj: Any, attrs: tuple[str, ...]) -> bool:
    state = inspect(obj)
    if state.pending or state.transient:
        return True
    for name in attrs:
        hist = state.attrs[name].history
        if hist.has_changes():
            return True
    return False


def _record(
    session: Session,
    obj: Any,
    *,
    entity_type: str,
    entity_id: int | None,
    prev: str | None,
    new: str,
    reason: str,
) -> None:
    from app.models import StatusHistory

    state = inspect(obj)
    if entity_id is None or state.pending or state.transient:
        # 新建对象不写历史（autoincrement 下 before_flush 时还没有 id；预置 id 的也保持
        # 一致口径）——首个 stage 由 stage_changed_at 体现，时间轴只记「变迁」。
        return
    actor = session.info.get(STAGE_ACTOR_KEY) or DEFAULT_STAGE_ACTOR
    session.add(
        StatusHistory(
            entity_type=entity_type,
            entity_id=entity_id,
            from_status=prev,
            to_status=new,
            changed_by=str(actor),
            reason=reason,
            metadata_={"kind": "stage"},
        )
    )


def sync_hub_stage(session: Session, hub: Any, *, now: datetime | None = None) -> bool:
    """重算单个 hub 的 stage；变化返回 True。不 flush。"""
    now = now or datetime.now(UTC)
    new = derive_hub_stage(hub)
    if hub.stage == new:
        return False
    prev = hub.stage
    hub.stage = new
    hub.stage_changed_at = now
    _record(
        session,
        hub,
        entity_type="hub_stage",
        entity_id=hub.id,
        prev=prev,
        new=new,
        reason=explain_stage_driver(None, hub),
    )
    return True


def sync_ticket_stage(
    session: Session, ticket: Any, hub: Any | None, *, now: datetime | None = None
) -> bool:
    """重算单个 ticket 的 stage；变化返回 True。不 flush。"""
    now = now or datetime.now(UTC)
    new = derive_ticket_stage(ticket, hub)
    if ticket.stage == new:
        return False
    prev = ticket.stage
    ticket.stage = new
    ticket.stage_changed_at = now
    _record(
        session,
        ticket,
        entity_type="ticket_stage",
        entity_id=ticket.id,
        prev=prev,
        new=new,
        reason=explain_stage_driver(ticket, hub),
    )
    return True


def _before_flush(session: Session, _flush_context: Any, _instances: Any) -> None:
    from app.models import HubIssue, Ticket

    try:
        hubs: dict[int, Any] = {}
        pending_hubs: list[Any] = []  # 新建 hub（无 id）
        tickets: dict[int, Any] = {}
        pending_tickets: list[Any] = []

        for obj in list(session.new) + list(session.dirty):
            if isinstance(obj, HubIssue):
                if _changed(obj, _HUB_WATCH):
                    if obj.id is None:
                        pending_hubs.append(obj)
                    else:
                        hubs[obj.id] = obj
            elif isinstance(obj, Ticket) and _changed(obj, _TICKET_WATCH):
                if obj.id is None:
                    pending_tickets.append(obj)
                else:
                    tickets[obj.id] = obj

        if not (hubs or pending_hubs or tickets or pending_tickets):
            return

        now = datetime.now(UTC)

        for hub in pending_hubs:
            sync_hub_stage(session, hub, now=now)

        with session.no_autoflush:
            for hub in hubs.values():
                sync_hub_stage(session, hub, now=now)
                # hub 变化传播到挂载的全部 ticket（含未加载进 session 的）
                linked = (
                    session.query(Ticket)
                    .filter(Ticket.hub_issue_id == hub.id, Ticket.deleted_at.is_(None))
                    .all()
                )
                for t in linked:
                    tickets.setdefault(t.id, t)

            for t in list(tickets.values()) + pending_tickets:
                hub_obj: Any | None = None
                if t.hub_issue_id is not None:
                    hub_obj = hubs.get(t.hub_issue_id) or session.get(HubIssue, t.hub_issue_id)
                sync_ticket_stage(session, t, hub_obj, now=now)
    except Exception:  # 监听器永不打断业务事务
        logger.exception("stage_listener_failed")


def register() -> None:
    """幂等注册到 Session 类级事件（覆盖所有 sessionmaker）。"""
    global _registered
    if _registered:
        return
    event.listen(Session, "before_flush", _before_flush)
    _registered = True


register()
