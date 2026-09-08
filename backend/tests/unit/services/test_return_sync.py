"""Return request (退回 KSM) tests."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models import Source, StatusHistory, SyncOutbox, Ticket
from app.services.cascade.return_sync import ReturnSyncError, request_return


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add_all([Source(code="ksm", name="KSM"), Source(code="zhichi", name="智齿")])
    db_session.commit()
    return db_session


def _ticket(db: Session, **ov: object) -> Ticket:
    base: dict[str, object] = {
        "short_code": "TKT-RET-1",
        "source_code": "ksm",
        "source_ticket_id": "BILL-RET-1",
        "type": "Raw",
        "status": "received",
        "title": "工单",
        # 默认已受理（takeover 写入 locked/handled）；退回目标节点改为退回执行
        # 时实时计算，不再依赖持久化的 opercacheId/node_id 快照。
        "ksm_takeover_status": "handled",
    }
    base.update(ov)
    t = Ticket(**base)  # type: ignore[arg-type]
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def test_request_return_enqueues_return_outbox(world: Session) -> None:
    t = _ticket(world)
    res = request_return(
        world, t.id, deal_opinion="转错模块，退回重分派", requested_by="user:carol"
    )
    assert res.ticket_id == t.id
    row = world.query(SyncOutbox).filter_by(kind="return", ticket_id=t.id).one()
    assert row.target_source_code == "ksm"
    assert row.source_ticket_id == "BILL-RET-1"
    assert row.status == "pending"
    assert row.payload["deal_opinion"] == "转错模块，退回重分派"
    assert row.payload["requested_by"] == "user:carol"
    # audit line written
    hist = world.query(StatusHistory).filter_by(entity_type="ticket", entity_id=t.id).all()
    assert any("退回 KSM" in (h.reason or "") for h in hist)


def test_request_return_rejects_empty_opinion(world: Session) -> None:
    t = _ticket(world)
    with pytest.raises(ReturnSyncError):
        request_return(world, t.id, deal_opinion="   ", requested_by="user:carol")


def test_request_return_rejects_non_ksm(world: Session) -> None:
    t = _ticket(world, source_code="zhichi", source_ticket_id="z-1")
    with pytest.raises(ReturnSyncError):
        request_return(world, t.id, deal_opinion="退回", requested_by="user:carol")


def test_request_return_rejects_child_no_source(world: Session) -> None:
    parent = _ticket(world)
    child = Ticket(
        short_code="TKT-RET-C1",
        type="Child",
        status="received",
        internal_split_id="TKT-RET-1-C1",
        parent_ticket_id=parent.id,
        title="子单",
    )
    world.add(child)
    world.commit()
    world.refresh(child)
    with pytest.raises(ReturnSyncError):
        request_return(world, child.id, deal_opinion="退回", requested_by="user:carol")


def test_request_return_rejects_missing_ticket(world: Session) -> None:
    with pytest.raises(ReturnSyncError):
        request_return(world, 99999, deal_opinion="退回", requested_by="user:carol")


def test_request_return_rejects_not_accepted(world: Session) -> None:
    """工单从未被接管受理 → 退回不可行，入队前拦截。"""
    t = _ticket(world, ksm_takeover_status=None)
    with pytest.raises(ReturnSyncError):
        request_return(world, t.id, deal_opinion="退回", requested_by="user:carol")


def test_request_return_rejects_failed_takeover(world: Session) -> None:
    """接管失败（未成功 lock/handle）→ 退回不可行。"""
    t = _ticket(world, ksm_takeover_status="failed")
    with pytest.raises(ReturnSyncError):
        request_return(world, t.id, deal_opinion="退回", requested_by="user:carol")


def test_request_return_rejects_duplicate_pending(world: Session) -> None:
    """同一工单已有一条 pending 退回请求未被 drain 消化 → 拒绝再入队第二条
    （防止 drain 延迟窗口内连点几次，堆出多条 pending 一次性连续执行导致目标
    节点来回弹，偶数次刚好抵消，见 TKT-006797/R20260904-0374）。"""
    t = _ticket(world)
    request_return(world, t.id, deal_opinion="第一次退回", requested_by="user:carol")
    with pytest.raises(ReturnSyncError, match="正在处理中"):
        request_return(world, t.id, deal_opinion="第二次退回", requested_by="user:carol")


def test_request_return_allows_after_previous_sent(world: Session) -> None:
    """前一条已经被 drain 消化（status 不再是 pending）→ 允许再入队新的一条。"""
    t = _ticket(world)
    first = request_return(world, t.id, deal_opinion="第一次退回", requested_by="user:carol")
    row = world.get(SyncOutbox, first.outbox_id)
    row.status = "sent"
    world.commit()

    second = request_return(world, t.id, deal_opinion="第二次退回", requested_by="user:carol")
    assert second.outbox_id != first.outbox_id
