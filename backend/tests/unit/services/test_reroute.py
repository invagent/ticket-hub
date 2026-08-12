"""RerouteService — 重新分配同步 handler_user_id(修责任人可见性失明)。"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models import Source, SystemSetting, Ticket, User
from app.services.supervisor.reroute import RerouteRequest, RerouteService


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add_all(
        [
            User(id=1, feishu_uid="ou_pool", name="pool", role="assignee"),
            User(id=2, feishu_uid="ou_handler", name="handler", role="assignee"),
        ]
    )
    # default pool → reroute 无 scope 命中时回落到 user 1
    db_session.add(SystemSetting(key="default_pool_user_id", value="1"))
    db_session.commit()
    return db_session


def _mk_ticket(db: Session, **kw: object) -> Ticket:
    defaults: dict[str, object] = {
        "short_code": "TKT-1",
        "source_code": "ksm",
        "source_ticket_id": "ksm-1",
        "type": "Raw",
        "status": "received",
    }
    defaults.update(kw)
    t = Ticket(**defaults)  # type: ignore[arg-type]
    db.add(t)
    db.commit()
    return t


def test_reroute_unassigned_sets_both_assigned_and_handler(world: Session) -> None:
    """未分配工单(assigned/handler 均 NULL)reroute 后,两者都写成分配到的用户——
    否则行级可见性按 handler 过滤,责任人本人看不到自己的工单。"""
    t = _mk_ticket(world, assigned_user_id=None, handler_user_id=None)

    RerouteService(world).reroute(RerouteRequest(ticket_ids=[t.id], operator_user_id=99))
    world.commit()
    world.refresh(t)

    assert t.assigned_user_id == 1
    assert t.handler_user_id == 1  # 同步了,责任人可见


def test_reroute_does_not_clobber_existing_different_handler(world: Session) -> None:
    """处理人已独立于责任人(handler != 旧 assigned,说明答复/转交单独改过处理人)时,
    reroute 改责任人不覆盖处理人,避免抢走正在处理者的工单可见性。"""
    # 旧责任人=2,但处理人已被答复/转交单独改成 handler(user 2 的责任、handler 另有其人)。
    # 造出 handler != assigned 的真实分叉:assigned=2(旧责任人),handler=2 会被视为"未分叉"。
    # 这里让 handler 指向一个与旧 assigned 不同的用户以表达"已分叉"。
    db_handler_user = User(id=3, feishu_uid="ou_h3", name="h3", role="assignee")
    world.add(db_handler_user)
    world.commit()
    t = _mk_ticket(world, assigned_user_id=2, handler_user_id=3)

    RerouteService(world).reroute(RerouteRequest(ticket_ids=[t.id], operator_user_id=99))
    world.commit()
    world.refresh(t)

    assert t.assigned_user_id == 1  # 责任人重算到 pool
    assert t.handler_user_id == 3  # 处理人已分叉,保持不动
