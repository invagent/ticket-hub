"""处理人手工重试失败 sync_outbox 行 —— 覆盖 KSM/智齿两个 sender 的适配层。

不触网：fake KSM/智齿 client 记录调用，可注入异常模拟仍然失败的重试。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy.orm import Session

from adapters.ksm import (
    HandleOrderRequest,
    KSMBusinessError,
    LockOrderRequest,
    ReturnOrderRequest,
    SupplyOrderRequest,
)
from adapters.zhichi import ZhichiBusinessError
from adapters.zhichi.types import Agent
from app.config import Settings
from app.models import HubIssue, Source, SyncOutbox, Ticket
from app.services.cascade.outbox_retry import (
    OutboxRetryError,
    latest_failed_outbox_for_ticket,
    retry_outbox_row,
)
from app.services.ksm.notice_store import FakeNoticeStore, NoticeInfo

# ---- KSM fakes ---------------------------------------------------------------


class FakeKSMClient:
    def __init__(self, *, detail: dict | None = None) -> None:  # type: ignore[type-arg]
        self.locks: list[LockOrderRequest] = []
        self.handles: list[HandleOrderRequest] = []
        self.supplies: list[SupplyOrderRequest] = []
        self.returns: list[ReturnOrderRequest] = []
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

    def return_order(self, req: ReturnOrderRequest) -> dict:  # type: ignore[type-arg]
        self.returns.append(req)
        return {"status": True}

    def get_order_detail(self, *, bill_id: str, notice_num: str, subscribe_num: str) -> dict:  # type: ignore[type-arg]
        if self._detail is None:
            raise KSMBusinessError(op="subscribeCallback", message="no data")
        return self._detail

    def close(self) -> None:
        self.closed = True


def _ksm_settings(**ov: object) -> Settings:
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


# ---- 智齿 fakes ---------------------------------------------------------------


class FakeZhichiClient:
    def __init__(self) -> None:
        self.replies: list = []  # type: ignore[type-arg]
        self.reply_error: Exception | None = None

    def get_agent_by_name(self, name: str) -> Agent | None:
        if name == "查无此人":
            return None
        return Agent(agentid="agent-" + name, agent_name=name)

    def reply_ticket(self, req):  # type: ignore[no-untyped-def]
        if self.reply_error is not None:
            raise self.reply_error
        self.replies.append(req)
        return {"ret_code": "000000"}

    def close(self) -> None:
        pass


@dataclass
class _ZhichiSettings:
    zhichi_writeback_enabled: bool = True
    zhichi_writeback_dry_run: bool = False
    zhichi_writeback_batch: int = 20
    zhichi_writeback_max_attempts: int = 5
    zhichi_fallback_agent_name: str = "莉莉"
    zhichi_appid: str = "x"
    zhichi_app_key: str = "y"
    zhichi_base_url: str = "https://www.soboten.com"


# ---- shared fixtures ----------------------------------------------------------


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(Source(code="zhichi", name="智齿"))
    db_session.commit()
    return db_session


def _ksm_hub(db: Session, **ov: object) -> HubIssue:
    base: dict[str, object] = {
        "short_code": "HUB-RETRY-1",
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


def _ksm_ticket(db: Session, hub: HubIssue, **ov: object) -> Ticket:
    base: dict[str, object] = {
        "short_code": "TKT-RETRY-1",
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


def _failed_outbox(
    db: Session, ticket: Ticket, hub: HubIssue, *, kind: str, target: str, payload: dict, attempts: int = 5
) -> SyncOutbox:  # type: ignore[type-arg]
    row = SyncOutbox(
        kind=kind,
        target_source_code=target,
        ticket_id=ticket.id,
        source_ticket_id=ticket.source_ticket_id or "",
        hub_issue_id=hub.id,
        payload=payload,
        status="failed",
        attempts=attempts,
        last_error="节点已流转至其他节点",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _zhichi_ticket_and_hub(db: Session, **ov: object) -> tuple[Ticket, HubIssue]:
    hub = HubIssue(
        short_code="HUB-RETRY-Z1", type="Operation", title="标题", status="created",
        reply_content="hub级答复",
    )
    db.add(hub)
    db.flush()
    base: dict[str, object] = {
        "short_code": "TKT-RETRY-Z1",
        "source_code": "zhichi",
        "source_ticket_id": "ZT1",
        "type": "Raw",
        "status": "received",
        "title": "标题",
        "hub_issue_id": hub.id,
        "source_payload": {
            "raw": {
                "ticket_title": "标题",
                "ticket_content": "正文",
                "ticket_level": 2,
                "deal_agent_name": "莉莉",
            }
        },
    }
    base.update(ov)
    t = Ticket(**base)  # type: ignore[arg-type]
    db.add(t)
    db.commit()
    db.refresh(t)
    db.refresh(hub)
    return t, hub


# ---- latest_failed_outbox_for_ticket ----------------------------------------


def test_latest_failed_outbox_for_ticket_returns_newest(world: Session) -> None:
    hub = _ksm_hub(world)
    t = _ksm_ticket(world, hub)
    _failed_outbox(world, t, hub, kind="reply", target="ksm", payload={"reply_content": "旧"})
    newest = _failed_outbox(world, t, hub, kind="return", target="ksm", payload={"deal_opinion": "新"})
    found = latest_failed_outbox_for_ticket(world, t.id)
    assert found is not None and found.id == newest.id


def test_latest_failed_outbox_for_ticket_ignores_non_failed(world: Session) -> None:
    hub = _ksm_hub(world)
    t = _ksm_ticket(world, hub)
    row = SyncOutbox(
        kind="reply",
        target_source_code="ksm",
        ticket_id=t.id,
        source_ticket_id=t.source_ticket_id or "",
        hub_issue_id=hub.id,
        payload={"reply_content": "ok"},
        status="pending",
    )
    world.add(row)
    world.commit()
    assert latest_failed_outbox_for_ticket(world, t.id) is None


# ---- retry_outbox_row: guard rails -------------------------------------------


def test_retry_outbox_row_rejects_non_failed_status(world: Session) -> None:
    hub = _ksm_hub(world)
    t = _ksm_ticket(world, hub)
    row = SyncOutbox(
        kind="reply",
        target_source_code="ksm",
        ticket_id=t.id,
        source_ticket_id=t.source_ticket_id or "",
        hub_issue_id=hub.id,
        payload={"reply_content": "ok"},
        status="pending",
    )
    world.add(row)
    world.commit()
    with pytest.raises(OutboxRetryError, match="非 failed"):
        retry_outbox_row(world, row.id)


def test_retry_outbox_row_missing_id_raises(world: Session) -> None:
    with pytest.raises(OutboxRetryError, match="不存在"):
        retry_outbox_row(world, 999999)


def test_retry_unsupported_source_raises(world: Session) -> None:
    hub = _ksm_hub(world)
    t = _ksm_ticket(world, hub, source_code="zammad", source_ticket_id="Z-1")
    row = _failed_outbox(world, t, hub, kind="reply", target="zammad", payload={})
    with pytest.raises(OutboxRetryError, match="暂不支持"):
        retry_outbox_row(world, row.id)


def test_retry_ksm_disabled_switch_raises(world: Session) -> None:
    hub = _ksm_hub(world)
    t = _ksm_ticket(world, hub)
    row = _failed_outbox(world, t, hub, kind="reply", target="ksm", payload={"reply_content": "ok"})
    with pytest.raises(OutboxRetryError, match="ksm_writeback_enabled"):
        retry_outbox_row(world, row.id, settings=_ksm_settings(ksm_writeback_enabled=False))


# ---- retry_outbox_row: KSM success / still-fails -----------------------------


def test_retry_ksm_row_success(world: Session) -> None:
    hub = _ksm_hub(world)
    t = _ksm_ticket(world, hub)
    row = _failed_outbox(
        world, t, hub, kind="reply", target="ksm", payload={"reply_content": "请按步骤操作"}
    )
    client = FakeKSMClient(detail=_SUBSCRIBE)
    store = FakeNoticeStore()
    store.put("BILL-1", NoticeInfo(notice_num="N1", subscribe_num="ksm_feedback_change"))

    result = retry_outbox_row(
        world, row.id, ksm_client=client, notice_store=store, settings=_ksm_settings()
    )

    assert result.sent is True
    world.refresh(row)
    assert row.status == "sent"
    assert len(client.handles) == 1 and client.handles[0].deal_opinion == "请按步骤操作"


def test_retry_ksm_row_still_fails(world: Session) -> None:
    hub = _ksm_hub(world)
    t = _ksm_ticket(world, hub)
    row = _failed_outbox(
        world, t, hub, kind="reply", target="ksm", payload={"reply_content": "ok"}, attempts=5
    )
    client = FakeKSMClient(detail=_SUBSCRIBE)
    client.handle_error = KSMBusinessError(op="handleKsmOrder", message="节点已流转")
    store = FakeNoticeStore()
    store.put("BILL-1", NoticeInfo(notice_num="N1", subscribe_num="ksm_feedback_change"))

    result = retry_outbox_row(
        world, row.id, ksm_client=client, notice_store=store, settings=_ksm_settings(ksm_writeback_max_attempts=5)
    )

    assert result.sent is False
    assert result.error is not None and "节点已流转" in result.error
    world.refresh(row)
    # attempts 已达阈值前的 5，重试这次又 +1 → 6，仍 >= max_attempts → 维持 failed
    assert row.status == "failed"
    assert row.attempts == 6


# ---- retry_outbox_row: zhichi success / still-fails --------------------------


def test_retry_zhichi_row_success(world: Session) -> None:
    t, hub = _zhichi_ticket_and_hub(world)
    row = _failed_outbox(
        world, t, hub, kind="reply", target="zhichi", payload={"reply_content": "已处理"}
    )
    client = FakeZhichiClient()

    result = retry_outbox_row(world, row.id, zhichi_client=client, settings=_ZhichiSettings())

    assert result.sent is True
    world.refresh(row)
    assert row.status == "sent"
    assert len(client.replies) == 1


def test_retry_zhichi_row_still_fails(world: Session) -> None:
    t, hub = _zhichi_ticket_and_hub(world)
    row = _failed_outbox(
        world, t, hub, kind="reply", target="zhichi", payload={"reply_content": "ok"}, attempts=5
    )
    client = FakeZhichiClient()
    client.reply_error = ZhichiBusinessError(op="reply", ret_code="999999", ret_msg="系统繁忙")

    result = retry_outbox_row(
        world, row.id, zhichi_client=client, settings=_ZhichiSettings(zhichi_writeback_max_attempts=5)
    )

    assert result.sent is False
    assert result.error is not None and "系统繁忙" in result.error
    world.refresh(row)
    assert row.status == "failed"
    assert row.attempts == 6
