"""KSM 出站回写 sender 测试（D4 第②段）.

Fake KSM client 记录调用并可注入异常；不触网。覆盖：
开关/身份门控、dry_run、reply→lock+handle、status in_progress→lock、
status released→handle(默认/hub回复)、已接管容错、refresh 刷新节点、
失败 deferred/failed 重试、idempotency、batch 上限、ticket/billId 缺失跳过。
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from adapters.ksm import (
    HandleOrderRequest,
    KSMBusinessError,
    LockOrderRequest,
    SupplyOrderRequest,
)
from app.config import Settings
from app.models import HubIssue, Source, SyncOutbox, Ticket
from app.repositories.status_history import StatusHistoryRepository
from app.services.ksm.notice_store import FakeNoticeStore, NoticeInfo
from app.services.ksm.writeback import drain_ksm_outbox

# ---- fakes ------------------------------------------------------------------


class FakeKSMClient:
    """Records ops; raises configured errors; returns a fixed refresh detail."""

    def __init__(self, *, detail: dict | None = None) -> None:  # type: ignore[type-arg]
        self.locks: list[LockOrderRequest] = []
        self.handles: list[HandleOrderRequest] = []
        self.supplies: list[SupplyOrderRequest] = []
        self.detail_calls: list[str] = []
        self._detail = detail
        self.lock_error: Exception | None = None
        self.handle_error: Exception | None = None
        self.closed = False

    def lock_order(self, req: LockOrderRequest) -> dict:  # type: ignore[type-arg]
        self.locks.append(req)
        if self.lock_error is not None:
            raise self.lock_error
        return {"status": True}

    def handle_order(self, req: HandleOrderRequest) -> dict:  # type: ignore[type-arg]
        self.handles.append(req)
        if self.handle_error is not None:
            raise self.handle_error
        return {"status": True}

    def supply_order(self, req: SupplyOrderRequest) -> dict:  # type: ignore[type-arg]
        self.supplies.append(req)
        return {"status": True}

    def get_order_detail(self, *, bill_id: str, notice_num: str, subscribe_num: str) -> dict:  # type: ignore[type-arg]
        self.detail_calls.append(bill_id)
        if self._detail is None:
            raise KSMBusinessError(op="subscribeCallback", message="no data")
        return self._detail

    def close(self) -> None:
        self.closed = True


def _settings(**ov: object) -> Settings:
    base: dict[str, object] = {
        "ksm_writeback_enabled": True,
        "ksm_writeback_dry_run": False,
        "ksm_handler_name": "李志坚",
        "ksm_handler_number": "10086",
        "ksm_writeback_batch": 20,
        "ksm_writeback_max_attempts": 5,
    }
    base.update(ov)
    return Settings(**base)  # type: ignore[arg-type]


_SUBSCRIBE = {
    "billId": "BILL-1",
    "feedbackType": 3,
    "node": {"id": "NODE-OLD", "name": "受理"},
    "product": {"id": "PROD-1"},
    "version": {"id": "VER-1"},
    "module": {"id": "MOD-1"},
    "customerInfo": {"linkman": "王五", "email": "w@x.com", "mobile": "13800000000"},
}


# ---- fixtures ---------------------------------------------------------------


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(Source(code="zhichi", name="智齿"))
    db_session.commit()
    return db_session


def _hub(db: Session, **ov: object) -> HubIssue:
    base: dict[str, object] = {
        "short_code": "HUB-WB-1",
        "type": "Operation",
        "title": "回写问题",
        "status": "created",
    }
    base.update(ov)
    h = HubIssue(**base)  # type: ignore[arg-type]
    db.add(h)
    db.commit()
    db.refresh(h)
    return h


def _ticket(db: Session, hub: HubIssue, **ov: object) -> Ticket:
    base: dict[str, object] = {
        "short_code": "TKT-WB-1",
        "source_code": "ksm",
        "source_ticket_id": "BILL-1",
        "type": "Raw",
        "status": "received",
        "title": "工单",
        "hub_issue_id": hub.id,
        "source_payload": {"billId": "BILL-1", "_subscribe_callback": _SUBSCRIBE},
    }
    base.update(ov)
    t = Ticket(**base)  # type: ignore[arg-type]
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _outbox(db: Session, ticket: Ticket, hub: HubIssue, *, kind: str, payload: dict) -> SyncOutbox:  # type: ignore[type-arg]
    row = SyncOutbox(
        kind=kind,
        target_source_code="ksm",
        ticket_id=ticket.id,
        source_ticket_id=ticket.source_ticket_id,
        hub_issue_id=hub.id,
        payload=payload,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ---- gating -----------------------------------------------------------------


def test_disabled_touches_nothing(world: Session) -> None:
    hub = _hub(world)
    t = _ticket(world, hub)
    row = _outbox(world, t, hub, kind="reply", payload={"reply_content": "ok"})
    client = FakeKSMClient()
    report = drain_ksm_outbox(world, client=client, settings=_settings(ksm_writeback_enabled=False))
    assert report.scanned == 0 and not client.locks
    world.refresh(row)
    assert row.status == "pending"


def test_no_handler_identity_touches_nothing(world: Session) -> None:
    hub = _hub(world)
    t = _ticket(world, hub)
    _outbox(world, t, hub, kind="reply", payload={"reply_content": "ok"})
    client = FakeKSMClient()
    report = drain_ksm_outbox(world, client=client, settings=_settings(ksm_handler_number=""))
    assert report.scanned == 0 and not client.locks


def test_dry_run_assembles_but_skips(world: Session) -> None:
    hub = _hub(world)
    t = _ticket(world, hub)
    row = _outbox(world, t, hub, kind="reply", payload={"reply_content": "请重启"})
    client = FakeKSMClient()
    report = drain_ksm_outbox(world, client=client, settings=_settings(ksm_writeback_dry_run=True))
    assert report.skipped == 1 and report.sent == 0
    assert not client.locks and not client.handles
    world.refresh(row)
    assert row.status == "skipped" and "dry_run" in (row.last_error or "")


# ---- reply → lock + handle(is_deal) -----------------------------------------


def test_reply_locks_then_handles_close(world: Session) -> None:
    hub = _hub(world)
    t = _ticket(world, hub)
    row = _outbox(world, t, hub, kind="reply", payload={"reply_content": "请按步骤操作"})
    client = FakeKSMClient(detail=_SUBSCRIBE)
    store = FakeNoticeStore()
    store.put("BILL-1", NoticeInfo(notice_num="N1", subscribe_num="ksm_feedback_change"))

    report = drain_ksm_outbox(world, client=client, notice_store=store, settings=_settings())

    assert report.sent == 1
    assert len(client.locks) == 1 and client.locks[0].bill_id == "BILL-1"
    assert client.locks[0].account_name == "李志坚" and client.locks[0].account_number == "10086"
    assert len(client.handles) == 1
    h = client.handles[0]
    assert h.is_deal is True
    assert h.deal_opinion == "请按步骤操作"
    assert h.product_id == "PROD-1" and h.module_id == "MOD-1" and h.back_type == "3"
    assert h.linkman == "王五" and h.customer_email == "w@x.com"
    world.refresh(row)
    assert row.status == "sent" and row.sent_at is not None and row.attempts == 1
    # 关单回写成功 → 本地 ticket→closed、hub→resolved
    world.refresh(t)
    world.refresh(hub)
    assert t.status == "closed"
    assert hub.status == "resolved"


# ---- Task 6: 关单回写衔接 op_status=closed -----------------------------------


def test_reply_close_advances_op_status_to_closed_when_answered(world: Session) -> None:
    hub = _hub(world, op_status="answered", op_handler="agent:auto_answer")
    t = _ticket(world, hub)
    _outbox(world, t, hub, kind="reply", payload={"reply_content": "请按步骤操作"})
    client = FakeKSMClient(detail=_SUBSCRIBE)
    store = FakeNoticeStore()
    store.put("BILL-1", NoticeInfo(notice_num="N1", subscribe_num="ksm_feedback_change"))

    drain_ksm_outbox(world, client=client, notice_store=store, settings=_settings())

    world.refresh(hub)
    assert hub.status == "resolved"
    assert hub.op_status == "closed"
    # handler 保持原值，关单不改处理人
    assert hub.op_handler == "agent:auto_answer"
    history = StatusHistoryRepository(world).find_for_entity(
        entity_type="hub_issue", entity_id=hub.id
    )
    assert any(h.to_status == "closed" and h.from_status == "answered" for h in history)


def test_close_local_ignores_non_operation_hub_op_status(world: Session) -> None:
    hub = _hub(world, type="Bug_fix", op_status=None)
    t = _ticket(world, hub)
    _outbox(world, t, hub, kind="reply", payload={"reply_content": "ok"})
    client = FakeKSMClient(detail=_SUBSCRIBE)
    drain_ksm_outbox(world, client=client, settings=_settings())
    world.refresh(hub)
    assert hub.status == "resolved"
    assert hub.op_status is None


def test_close_local_does_not_touch_op_status_when_not_answered(world: Session) -> None:
    hub = _hub(world, op_status="processing", op_handler="agent:auto_answer")
    t = _ticket(world, hub)
    _outbox(world, t, hub, kind="reply", payload={"reply_content": "ok"})
    client = FakeKSMClient(detail=_SUBSCRIBE)
    drain_ksm_outbox(world, client=client, settings=_settings())
    world.refresh(hub)
    assert hub.status == "resolved"
    # 尚未 answered（比如客户还没到自动答复阶段）→ 关单回写不越权推进 op_status
    assert hub.op_status == "processing"


def test_close_local_op_status_closed_is_idempotent(world: Session) -> None:
    """op_status 已是 closed（比如 T+7 beat 先到）→ 关单回写不重复写 history。"""
    hub = _hub(world, op_status="closed", op_handler="主管")
    t = _ticket(world, hub)
    _outbox(world, t, hub, kind="reply", payload={"reply_content": "ok"})
    client = FakeKSMClient(detail=_SUBSCRIBE)
    drain_ksm_outbox(world, client=client, settings=_settings())
    world.refresh(hub)
    assert hub.op_status == "closed"
    assert hub.op_handler == "主管"
    history = StatusHistoryRepository(world).find_for_entity(
        entity_type="hub_issue", entity_id=hub.id
    )
    op_status_entries = [h for h in history if h.to_status == "closed"]
    assert len(op_status_entries) == 0  # apply_op_status no-op：状态和处理人都没变


# ---- progress_note → lock + handle(is_deal=False) 不关单（ADR-0016 P4）------


def test_progress_note_replies_without_closing(world: Session) -> None:
    hub = _hub(world)
    t = _ticket(world, hub)
    row = _outbox(
        world,
        t,
        hub,
        kind="progress_note",
        payload={"note": "3 个子任务已完成第 1 个", "progress": {"x": 1, "n": 3}},
    )
    client = FakeKSMClient(detail=_SUBSCRIBE)
    report = drain_ksm_outbox(world, client=client, settings=_settings())
    assert report.sent == 1
    assert len(client.handles) == 1
    h = client.handles[0]
    assert h.is_deal is False  # 只回复不关单——第 1 条通知就关掉客户单是 review 抓出的坑
    assert h.deal_opinion == "3 个子任务已完成第 1 个"
    world.refresh(row)
    assert row.status == "sent"
    # 进度通知不关单 → 本地状态不动
    world.refresh(t)
    assert t.status != "closed"


# ---- status in_progress → lock only -----------------------------------------


def test_supply_locks_then_supplies(world: Session) -> None:
    hub = _hub(world)
    t = _ticket(world, hub)
    row = _outbox(world, t, hub, kind="supply", payload={"supply_note": "请提供错误截图"})
    client = FakeKSMClient(detail=_SUBSCRIBE)
    report = drain_ksm_outbox(world, client=client, settings=_settings())
    assert report.sent == 1
    assert len(client.locks) == 1 and len(client.supplies) == 1
    assert client.supplies[0].deal_opinion == "请提供错误截图"
    assert client.supplies[0].bill_id == "BILL-1" and client.supplies[0].node_id == "NODE-OLD"
    world.refresh(row)
    assert row.status == "sent"


# ---- supply 真发成功/dry_run/失败 → ticket.status 不动（补料识别已改走 -----
# hub.op_status==supplementing，由 auto_answer request_supply 入队时置，与
# writeback 无关；此处只需确认 supply action 本身不误改 ticket.status）------


def test_supply_send_does_not_change_ticket_status(world: Session) -> None:
    hub = _hub(world)
    t = _ticket(world, hub)
    row = _outbox(world, t, hub, kind="supply", payload={"supply_note": "请提供错误截图"})
    client = FakeKSMClient(detail=_SUBSCRIBE)
    report = drain_ksm_outbox(world, client=client, settings=_settings())
    assert report.sent == 1
    world.refresh(row)
    assert row.status == "sent"
    world.refresh(t)
    assert t.status == "received"


def test_supply_dry_run_does_not_change_ticket_status(world: Session) -> None:
    hub = _hub(world)
    t = _ticket(world, hub)
    row = _outbox(world, t, hub, kind="supply", payload={"supply_note": "请提供错误截图"})
    client = FakeKSMClient()
    report = drain_ksm_outbox(world, client=client, settings=_settings(ksm_writeback_dry_run=True))
    assert report.skipped == 1 and report.sent == 0
    assert not client.locks and not client.supplies
    world.refresh(row)
    assert row.status == "skipped"
    world.refresh(t)
    assert t.status == "received"


def test_supply_send_failure_does_not_change_ticket_status(world: Session) -> None:
    hub = _hub(world)
    t = _ticket(world, hub)
    row = _outbox(world, t, hub, kind="supply", payload={"supply_note": "请提供错误截图"})
    client = FakeKSMClient(detail=_SUBSCRIBE)
    client.lock_error = KSMBusinessError(op="lockKsmOrder", message="单据不存在")
    report = drain_ksm_outbox(
        world, client=client, settings=_settings(ksm_writeback_max_attempts=1)
    )
    assert report.failed == 1 and not client.supplies
    world.refresh(row)
    assert row.status == "failed"
    world.refresh(t)
    assert t.status == "received"


# ---- status in_progress → lock only -----------------------------------------


def test_status_in_progress_locks_only(world: Session) -> None:
    hub = _hub(world)
    t = _ticket(world, hub)
    row = _outbox(world, t, hub, kind="status", payload={"to_status": "in_progress"})
    client = FakeKSMClient(detail=_SUBSCRIBE)
    report = drain_ksm_outbox(world, client=client, settings=_settings())
    assert report.sent == 1
    assert len(client.locks) == 1 and not client.handles
    world.refresh(row)
    assert row.status == "sent"


# ---- status released → handle(close) ----------------------------------------


def test_status_released_uses_hub_reply(world: Session) -> None:
    hub = _hub(world, reply_content="问题已在 7.5 修复")
    t = _ticket(world, hub)
    _outbox(world, t, hub, kind="status", payload={"to_status": "released"})
    client = FakeKSMClient(detail=_SUBSCRIBE)
    drain_ksm_outbox(world, client=client, settings=_settings())
    assert len(client.handles) == 1
    assert client.handles[0].deal_opinion == "问题已在 7.5 修复"
    assert client.handles[0].is_deal is True


def test_status_released_default_note_when_no_reply(world: Session) -> None:
    hub = _hub(world, type="Bug_fix", reply_content=None)
    t = _ticket(world, hub)
    _outbox(world, t, hub, kind="status", payload={"to_status": "released"})
    client = FakeKSMClient(detail=_SUBSCRIBE)
    drain_ksm_outbox(world, client=client, settings=_settings())
    assert len(client.handles) == 1
    assert "已处理完成" in client.handles[0].deal_opinion


# ---- already-locked tolerated -----------------------------------------------


def test_already_locked_is_tolerated(world: Session) -> None:
    hub = _hub(world)
    t = _ticket(world, hub)
    row = _outbox(world, t, hub, kind="reply", payload={"reply_content": "ok"})
    client = FakeKSMClient(detail=_SUBSCRIBE)
    client.lock_error = KSMBusinessError(op="lockKsmOrder", message="工单已被接管")
    report = drain_ksm_outbox(world, client=client, settings=_settings())
    assert report.sent == 1 and len(client.handles) == 1
    world.refresh(row)
    assert row.status == "sent"


def test_lock_real_error_fails(world: Session) -> None:
    hub = _hub(world)
    t = _ticket(world, hub)
    row = _outbox(world, t, hub, kind="reply", payload={"reply_content": "ok"})
    client = FakeKSMClient(detail=_SUBSCRIBE)
    client.lock_error = KSMBusinessError(op="lockKsmOrder", message="单据不存在")
    report = drain_ksm_outbox(
        world, client=client, settings=_settings(ksm_writeback_max_attempts=1)
    )
    assert report.failed == 1 and not client.handles
    world.refresh(row)
    assert row.status == "failed" and "单据不存在" in (row.last_error or "")


# ---- refresh ----------------------------------------------------------------


def test_refresh_updates_node_id(world: Session) -> None:
    hub = _hub(world)
    t = _ticket(world, hub)
    _outbox(world, t, hub, kind="reply", payload={"reply_content": "ok"})
    fresh = {**_SUBSCRIBE, "node": {"id": "NODE-NEW", "name": "处理"}}
    client = FakeKSMClient(detail=fresh)
    store = FakeNoticeStore()
    store.put("BILL-1", NoticeInfo(notice_num="N1", subscribe_num="ksm_feedback_change"))
    drain_ksm_outbox(world, client=client, notice_store=store, settings=_settings())
    assert client.detail_calls == ["BILL-1"]
    assert client.handles[0].node_id == "NODE-NEW"


def test_no_notice_falls_back_to_stored_node(world: Session) -> None:
    hub = _hub(world)
    t = _ticket(world, hub)
    _outbox(world, t, hub, kind="reply", payload={"reply_content": "ok"})
    client = FakeKSMClient(detail=_SUBSCRIBE)
    # no notice store → no refresh, stored NODE-OLD used
    drain_ksm_outbox(world, client=client, notice_store=None, settings=_settings())
    assert not client.detail_calls
    assert client.handles[0].node_id == "NODE-OLD"


def test_refresh_failure_falls_back(world: Session) -> None:
    hub = _hub(world)
    t = _ticket(world, hub)
    _outbox(world, t, hub, kind="reply", payload={"reply_content": "ok"})
    client = FakeKSMClient(detail=None)  # get_order_detail raises
    store = FakeNoticeStore()
    store.put("BILL-1", NoticeInfo(notice_num="N1", subscribe_num="ksm_feedback_change"))
    report = drain_ksm_outbox(world, client=client, notice_store=store, settings=_settings())
    assert report.sent == 1
    assert client.handles[0].node_id == "NODE-OLD"


# ---- retry / idempotency ----------------------------------------------------


def test_handle_error_deferred_then_failed(world: Session) -> None:
    hub = _hub(world)
    t = _ticket(world, hub)
    row = _outbox(world, t, hub, kind="reply", payload={"reply_content": "ok"})
    client = FakeKSMClient(detail=_SUBSCRIBE)
    client.handle_error = KSMBusinessError(op="handleKsmOrder", message="节点已流转")
    s = _settings(ksm_writeback_max_attempts=2)

    r1 = drain_ksm_outbox(world, client=client, settings=s)
    assert r1.deferred == 1 and r1.failed == 0
    world.refresh(row)
    assert row.status == "pending" and row.attempts == 1

    r2 = drain_ksm_outbox(world, client=client, settings=s)
    assert r2.failed == 1
    world.refresh(row)
    assert row.status == "failed" and row.attempts == 2


def test_sent_rows_not_redrained(world: Session) -> None:
    hub = _hub(world)
    t = _ticket(world, hub)
    _outbox(world, t, hub, kind="reply", payload={"reply_content": "ok"})
    client = FakeKSMClient(detail=_SUBSCRIBE)
    drain_ksm_outbox(world, client=client, settings=_settings())
    # second pass: nothing pending
    client2 = FakeKSMClient(detail=_SUBSCRIBE)
    report = drain_ksm_outbox(world, client=client2, settings=_settings())
    assert report.scanned == 0 and not client2.locks


# ---- skip conditions --------------------------------------------------------


def test_ticket_missing_skipped(world: Session) -> None:
    hub = _hub(world)
    t = _ticket(world, hub)
    row = _outbox(world, t, hub, kind="reply", payload={"reply_content": "ok"})
    world.delete(t)
    world.commit()
    client = FakeKSMClient()
    report = drain_ksm_outbox(world, client=client, settings=_settings())
    assert report.skipped == 1
    world.refresh(row)
    assert row.status == "skipped"


def test_billid_falls_back_to_source_ticket_id(world: Session) -> None:
    # thin source_payload (no billId) → bill_id resolves from source_ticket_id
    hub = _hub(world)
    t = _ticket(world, hub, source_ticket_id="BILL-FB", source_payload={"_subscribe_callback": {}})
    _outbox(world, t, hub, kind="reply", payload={"reply_content": "ok"})
    client = FakeKSMClient(detail=_SUBSCRIBE)
    report = drain_ksm_outbox(world, client=client, settings=_settings())
    assert report.sent == 1
    assert client.locks[0].bill_id == "BILL-FB"


def test_batch_limit_respected(world: Session) -> None:
    hub = _hub(world)
    for i in range(5):
        t = _ticket(world, hub, short_code=f"TKT-B{i}", source_ticket_id=f"BILL-B{i}")
        _outbox(world, t, hub, kind="status", payload={"to_status": "in_progress"})
    client = FakeKSMClient(detail=_SUBSCRIBE)
    report = drain_ksm_outbox(world, client=client, settings=_settings(ksm_writeback_batch=3))
    assert report.scanned == 3 and report.sent == 3
