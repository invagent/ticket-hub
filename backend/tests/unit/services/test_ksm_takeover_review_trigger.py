"""trigger_ksm_takeover_after_review：审核确认后触发接管的入口函数.

不触网：monkeypatch 模块内的 KSMClient/NoticeStore/takeover_ksm_ticket，只验证
分支选择（notice 命中/过期退化快照/无快照跳过/非 KSM 跳过/已接管跳过/总开关关）
和传给 takeover_ksm_ticket 的参数是否正确。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Source, Ticket
from app.services.ksm import takeover as mod
from app.services.ksm.notice_store import NoticeInfo


def _settings(**ov: object) -> Settings:
    base: dict[str, object] = {"ksm_auto_takeover_enabled": True}
    base.update(ov)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.commit()
    return db_session


def _ticket(db: Session, **ov: object) -> Ticket:
    base: dict[str, object] = {
        "short_code": "TKT-RV-1",
        "source_code": "ksm",
        "source_ticket_id": "BILL-RV-1",
        "type": "Raw",
        "status": "received",
        "title": "t",
        "source_payload": {"billId": "BILL-RV-1"},
    }
    base.update(ov)
    t = Ticket(**base)  # type: ignore[arg-type]
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


class _FakeNoticeStore:
    def __init__(self, notice: NoticeInfo | None) -> None:
        self._notice = notice

    def get(self, bill_id: str) -> NoticeInfo | None:
        return self._notice

    def put(self, bill_id: str, notice: NoticeInfo) -> None:  # pragma: no cover
        pass


class _FakeClient:
    def __init__(
        self, *, detail: dict | None = None, raise_on_detail: Exception | None = None
    ) -> None:  # type: ignore[type-arg]
        self.closed = False
        self._detail = detail
        self._raise = raise_on_detail

    def get_order_detail(self, *, bill_id: str, notice_num: str, subscribe_num: str) -> dict:  # type: ignore[type-arg]
        if self._raise is not None:
            raise self._raise
        return self._detail or {}

    def close(self) -> None:
        self.closed = True


@dataclass
class _TakeoverCall:
    detail: dict  # type: ignore[type-arg]
    is_new: bool


def _patch_common(
    monkeypatch: pytest.MonkeyPatch,
    *,
    world: Session,
    notice: NoticeInfo | None,
    client: _FakeClient,
    settings: Settings,
    calls: list[_TakeoverCall],
) -> None:
    # trigger_ksm_takeover_after_review 自开 session，用 monkeypatch 让它复用测试
    # session（同 test_hub_issue_creator_dispatch.py 的既有套路）；close 置空防止
    # 内部 db.close() 把测试 fixture 的 session 关掉影响断言/后续复用。
    monkeypatch.setattr(mod, "make_session", lambda: world)
    monkeypatch.setattr(world, "close", lambda: None)
    monkeypatch.setattr(mod, "get_settings", lambda: settings)
    monkeypatch.setattr(mod, "NoticeStore", lambda redis_url: _FakeNoticeStore(notice))
    monkeypatch.setattr(mod, "KSMClient", lambda cfg: client)

    def fake_takeover(db, ticket, *, detail, is_new, client, notice_store, settings):  # type: ignore[no-untyped-def]
        calls.append(_TakeoverCall(detail=detail, is_new=is_new))

    monkeypatch.setattr(mod, "takeover_ksm_ticket", fake_takeover)


def test_disabled_skips(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    t = _ticket(world)
    calls: list[_TakeoverCall] = []
    _patch_common(
        monkeypatch,
        world=world,
        notice=None,
        client=_FakeClient(),
        settings=_settings(ksm_auto_takeover_enabled=False),
        calls=calls,
    )
    mod.trigger_ksm_takeover_after_review(t.id)
    assert calls == []


def test_non_ksm_source_skips(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    t = _ticket(world, source_code="zhichi", short_code="TKT-RV-2", source_ticket_id="X-2")
    calls: list[_TakeoverCall] = []
    _patch_common(monkeypatch, world=world, notice=None, client=_FakeClient(), settings=_settings(), calls=calls)
    mod.trigger_ksm_takeover_after_review(t.id)
    assert calls == []


def test_already_handled_skips(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    t = _ticket(world, ksm_takeover_status="handled")
    calls: list[_TakeoverCall] = []
    _patch_common(monkeypatch, world=world, notice=None, client=_FakeClient(), settings=_settings(), calls=calls)
    mod.trigger_ksm_takeover_after_review(t.id)
    assert calls == []


def test_notice_valid_refetches_detail(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    t = _ticket(world)
    fresh_detail = {"billId": "BILL-RV-1", "status": "2"}
    client = _FakeClient(detail=fresh_detail)
    calls: list[_TakeoverCall] = []
    _patch_common(
        monkeypatch,
        world=world,
        notice=NoticeInfo(notice_num="N1", subscribe_num="S1"),
        client=client,
        settings=_settings(),
        calls=calls,
    )
    mod.trigger_ksm_takeover_after_review(t.id)
    assert len(calls) == 1
    assert calls[0].detail == fresh_detail
    assert calls[0].is_new is True  # ksm_takeover_status 为 None → 视为新
    assert client.closed is True


def test_notice_expired_falls_back_to_snapshot(
    world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """notice 已过期（store 返回 None）→ 退化用入库快照 _subscribe_callback。"""
    snapshot = {"billId": "BILL-RV-1", "status": "1", "node": {"id": "N-OLD"}}
    t = _ticket(world, source_payload={"billId": "BILL-RV-1", "_subscribe_callback": snapshot})
    client = _FakeClient()
    calls: list[_TakeoverCall] = []
    _patch_common(monkeypatch, world=world, notice=None, client=client, settings=_settings(), calls=calls)
    mod.trigger_ksm_takeover_after_review(t.id)
    assert len(calls) == 1
    assert calls[0].detail == snapshot


def test_no_notice_no_snapshot_skips(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """notice 过期 + 入库时也没存快照 → 无法组装 detail，跳过不接管。"""
    t = _ticket(world, source_payload={"billId": "BILL-RV-1"})  # 无 _subscribe_callback
    client = _FakeClient()
    calls: list[_TakeoverCall] = []
    _patch_common(monkeypatch, world=world, notice=None, client=client, settings=_settings(), calls=calls)
    mod.trigger_ksm_takeover_after_review(t.id)
    assert calls == []
    assert client.closed is True  # client 仍要关闭


def test_refetch_fails_falls_back_to_snapshot(
    world: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """notice 存在但重拉抛 KSMError → 同样退化用快照，不是直接失败。"""
    from adapters.ksm import KSMBusinessError

    snapshot = {"billId": "BILL-RV-1", "status": "1"}
    t = _ticket(world, source_payload={"billId": "BILL-RV-1", "_subscribe_callback": snapshot})
    client = _FakeClient(
        raise_on_detail=KSMBusinessError(op="subscribeCallback", message="no data")
    )
    calls: list[_TakeoverCall] = []
    _patch_common(
        monkeypatch,
        world=world,
        notice=NoticeInfo(notice_num="N1", subscribe_num="S1"),
        client=client,
        settings=_settings(),
        calls=calls,
    )
    mod.trigger_ksm_takeover_after_review(t.id)
    assert len(calls) == 1
    assert calls[0].detail == snapshot


def test_already_locked_skips(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """ksm_takeover_status='locked' 已在 _TAKEN_OVER_STATUSES 里，与 'handled' 同样
    在函数入口就短路跳过（不重新尝试 handle）。"""
    t = _ticket(world, ksm_takeover_status="locked")
    client = _FakeClient()
    calls: list[_TakeoverCall] = []
    _patch_common(monkeypatch, world=world, notice=None, client=client, settings=_settings(), calls=calls)
    mod.trigger_ksm_takeover_after_review(t.id)
    assert calls == []
