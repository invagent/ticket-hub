"""KSM 入库即接管受理测试。

覆盖 takeover_ksm_ticket 的分支：新工单完整 lock+refresh+handle、已存在工单只 lock、
已接管跳过、dry_run 只组装、enabled=false 跳过、补偿 a/b/c、lock 失败中止。
复用 FakeKSMClient / FakeNoticeStore；Settings 用真实构造。
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from adapters.ksm import HandleOrderRequest, KSMBusinessError, LockOrderRequest, SupplyOrderRequest
from app.config import Settings
from app.models import Source, Ticket
from app.services.ksm.notice_store import NoticeInfo
from app.services.ksm.takeover import takeover_ksm_ticket


class FakeKSMClient:
    def __init__(self, *, detail: dict | None = None) -> None:  # type: ignore[type-arg]
        self.locks: list[LockOrderRequest] = []
        self.handles: list[HandleOrderRequest] = []
        self.supplies: list[SupplyOrderRequest] = []
        self.detail_calls: list[str] = []
        self._detail = detail
        self.lock_error: Exception | None = None
        self.handle_error: Exception | None = None
        # 每次 handle 抛不同错时用队列（补偿测试）
        self.handle_errors: list[Exception | None] | None = None

    def lock_order(self, req: LockOrderRequest) -> dict:  # type: ignore[type-arg]
        self.locks.append(req)
        if self.lock_error is not None:
            raise self.lock_error
        return {"status": True}

    def handle_order(self, req: HandleOrderRequest) -> dict:  # type: ignore[type-arg]
        self.handles.append(req)
        if self.handle_errors is not None:
            err = self.handle_errors.pop(0) if self.handle_errors else None
            if err is not None:
                raise err
            return {"status": True}
        if self.handle_error is not None:
            raise self.handle_error
        return {"status": True}

    def get_order_detail(self, *, bill_id: str, notice_num: str, subscribe_num: str) -> dict:  # type: ignore[type-arg]
        self.detail_calls.append(bill_id)
        if self._detail is None:
            raise KSMBusinessError(op="subscribeCallback", message="no data")
        return self._detail

    def close(self) -> None:
        pass


class FakeNotice:
    def get(self, bill_id: str) -> NoticeInfo | None:
        return NoticeInfo(notice_num="N1", subscribe_num="S1")

    def put(self, bill_id: str, notice: NoticeInfo) -> None:  # pragma: no cover
        pass


_SUBSCRIBE = {
    "billId": "BILL-1",
    "status": "1",
    "feedbackType": 3,
    "node": {"id": "NODE-OLD", "name": "受理"},
    "product": {"id": "PROD-1"},
    "version": {"id": "VER-1"},
    "module": {"id": "MOD-1"},
    "customerInfo": {"linkman": "王五", "email": "w@x.com", "mobile": "13800000000"},
}
# 接管后重拉的最新详情：node 已流转到新节点，handleSteps 含「受理」节点（退回目标 opercacheId 来源）
_SUBSCRIBE_FRESH = {
    **_SUBSCRIBE,
    "node": {"id": "NODE-NEW", "name": "处理"},
    "handleSteps": [
        {
            "nodeName": "受理",
            "opercacheId": "OPCACHE-ACCEPT",
            "handleDateTime": "2026-08-29 13:00:00",
        },
        {
            "nodeName": "协同处理",
            "opercacheId": "OPCACHE-COOP",
            "handleDateTime": "2026-08-29 13:00:01",
        },
    ],
}


def _settings(**ov: object) -> Settings:
    base: dict[str, object] = {
        "ksm_auto_takeover_enabled": True,
        "ksm_writeback_dry_run": False,
        "ksm_handler_name": "李志坚",
        "ksm_handler_number": "10086",
    }
    base.update(ov)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.commit()
    return db_session


def _ticket(db: Session, **ov: object) -> Ticket:
    base: dict[str, object] = {
        "short_code": "TKT-TK-1",
        "source_code": "ksm",
        "source_ticket_id": "BILL-1",
        "type": "Raw",
        "status": "received",
        "title": "工单",
        "source_payload": {"billId": "BILL-1", "_subscribe_callback": _SUBSCRIBE},
    }
    base.update(ov)
    t = Ticket(**base)  # type: ignore[arg-type]
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _detail(status: str = "1") -> dict:  # type: ignore[type-arg]
    return {**_SUBSCRIBE, "status": status}


# ---- gating -----------------------------------------------------------------


def test_disabled_skips(world: Session) -> None:
    t = _ticket(world)
    client = FakeKSMClient()
    takeover_ksm_ticket(
        world,
        t,
        detail=_detail(),
        is_new=True,
        client=client,  # type: ignore[arg-type]
        notice_store=FakeNotice(),
        settings=_settings(ksm_auto_takeover_enabled=False),
    )
    assert client.locks == []
    assert t.ksm_takeover_status is None


def test_dry_run_assembles_only(world: Session) -> None:
    t = _ticket(world)
    client = FakeKSMClient()
    takeover_ksm_ticket(
        world,
        t,
        detail=_detail(),
        is_new=True,
        client=client,  # type: ignore[arg-type]
        notice_store=FakeNotice(),
        settings=_settings(ksm_writeback_dry_run=True),
    )
    assert client.locks == []  # 只组装打日志，不真发
    assert client.handles == []
    assert t.ksm_takeover_status is None


def test_already_locked_skips(world: Session) -> None:
    t = _ticket(world, ksm_takeover_status="handled")
    client = FakeKSMClient()
    takeover_ksm_ticket(
        world,
        t,
        detail=_detail(),
        is_new=True,
        client=client,  # type: ignore[arg-type]
        notice_store=FakeNotice(),
        settings=_settings(),
    )
    assert client.locks == []


# ---- 新工单：完整受理 -------------------------------------------------------


def test_new_ticket_full_takeover(world: Session) -> None:
    t = _ticket(world)
    client = FakeKSMClient(detail=_SUBSCRIBE_FRESH)
    takeover_ksm_ticket(
        world,
        t,
        detail=_detail("1"),
        is_new=True,
        client=client,  # type: ignore[arg-type]
        notice_store=FakeNotice(),
        settings=_settings(),
    )
    assert len(client.locks) == 1
    assert len(client.handles) == 1
    # handle 用的是接管后重拉的新 node
    assert client.handles[0].node_id == "NODE-NEW"
    assert client.handles[0].is_deal is False
    # customerInfo 取客户联系人
    assert client.handles[0].linkman == "王五"
    assert client.handles[0].customer_email == "w@x.com"
    # 接管人固定配置
    assert client.locks[0].account_number == "10086"
    assert t.ksm_takeover_status == "handled"
    # 持久化退回信息（迁移 0038）：受理节点 opercacheId + 当前节点
    assert t.ksm_accept_opercache_id == "OPCACHE-ACCEPT"
    assert t.ksm_current_node_id == "NODE-NEW"


def test_new_ticket_status2_also_full(world: Session) -> None:
    """status=2 但仍是新工单 → 完整受理（是否 handle 看 is_new，不看 status）。"""
    t = _ticket(world)
    client = FakeKSMClient(detail=_SUBSCRIBE_FRESH)
    takeover_ksm_ticket(
        world,
        t,
        detail=_detail("2"),
        is_new=True,
        client=client,  # type: ignore[arg-type]
        notice_store=FakeNotice(),
        settings=_settings(),
    )
    assert len(client.handles) == 1
    assert t.ksm_takeover_status == "handled"


# ---- 已存在工单：只接管不处理 ----------------------------------------------


def test_existing_ticket_lock_only(world: Session) -> None:
    t = _ticket(world)
    client = FakeKSMClient(detail=_SUBSCRIBE_FRESH)
    takeover_ksm_ticket(
        world,
        t,
        detail=_detail("2"),
        is_new=False,
        client=client,  # type: ignore[arg-type]
        notice_store=FakeNotice(),
        settings=_settings(),
    )
    assert len(client.locks) == 1
    assert client.handles == []  # 不 handle
    assert t.ksm_takeover_status == "locked"


# ---- 失败与补偿 -------------------------------------------------------------


def test_lock_failure_aborts(world: Session) -> None:
    t = _ticket(world)
    client = FakeKSMClient(detail=_SUBSCRIBE_FRESH)
    client.lock_error = KSMBusinessError(op="lockKsmOrder", message="接管异常")
    takeover_ksm_ticket(
        world,
        t,
        detail=_detail(),
        is_new=True,
        client=client,  # type: ignore[arg-type]
        notice_store=FakeNotice(),
        settings=_settings(),
    )
    assert client.handles == []  # lock 失败不 handle
    assert t.ksm_takeover_status == "failed"
    assert "接管异常" in (t.ksm_takeover_error or "")


def test_already_locked_hint_proceeds(world: Session) -> None:
    """lock 报'已接管'关键字 → 视为已接管，继续 handle。"""
    t = _ticket(world)
    client = FakeKSMClient(detail=_SUBSCRIBE_FRESH)
    client.lock_error = KSMBusinessError(op="lockKsmOrder", message="工单已被接管")
    takeover_ksm_ticket(
        world,
        t,
        detail=_detail(),
        is_new=True,
        client=client,  # type: ignore[arg-type]
        notice_store=FakeNotice(),
        settings=_settings(),
    )
    assert len(client.handles) == 1  # 继续 handle
    assert t.ksm_takeover_status == "handled"


def test_compensate_stale_node(world: Session) -> None:
    """handle 报'已流转至其他节点' → 重拉重试一次 handle 成功。"""
    t = _ticket(world)
    client = FakeKSMClient(detail=_SUBSCRIBE_FRESH)
    client.handle_errors = [
        KSMBusinessError(op="handleKsmOrder", message="工单已流转至其他节点"),
        None,  # 第二次成功
    ]
    takeover_ksm_ticket(
        world,
        t,
        detail=_detail(),
        is_new=True,
        client=client,  # type: ignore[arg-type]
        notice_store=FakeNotice(),
        settings=_settings(),
    )
    assert len(client.handles) == 2  # 首次失败 + 补偿一次
    assert t.ksm_takeover_status == "handled"


def test_compensate_not_locked(world: Session) -> None:
    """handle 报'未锁定,不能直接处理' → 补 lock + 重拉 + 再 handle 成功。"""
    t = _ticket(world)
    client = FakeKSMClient(detail=_SUBSCRIBE_FRESH)
    client.handle_errors = [
        KSMBusinessError(op="handleKsmOrder", message="工单未锁定,不能直接处理"),
        None,
    ]
    takeover_ksm_ticket(
        world,
        t,
        detail=_detail(),
        is_new=True,
        client=client,  # type: ignore[arg-type]
        notice_store=FakeNotice(),
        settings=_settings(),
    )
    assert len(client.locks) == 2  # 初次 + 补偿 relock
    assert len(client.handles) == 2
    assert t.ksm_takeover_status == "handled"


def test_compensate_other_error_fails(world: Session) -> None:
    """handle 报其他错误 → 不补偿，标 failed。"""
    t = _ticket(world)
    client = FakeKSMClient(detail=_SUBSCRIBE_FRESH)
    client.handle_error = KSMBusinessError(op="handleKsmOrder", message="参数校验失败")
    takeover_ksm_ticket(
        world,
        t,
        detail=_detail(),
        is_new=True,
        client=client,  # type: ignore[arg-type]
        notice_store=FakeNotice(),
        settings=_settings(),
    )
    assert t.ksm_takeover_status == "failed"
    assert "参数校验失败" in (t.ksm_takeover_error or "")
