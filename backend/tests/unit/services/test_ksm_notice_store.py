"""resolve_notice：Redis 命中优先，未命中回落 ticket 持久化列（迁移 0044）。"""

from __future__ import annotations

from app.models import Ticket
from app.services.ksm.notice_store import FakeNoticeStore, NoticeInfo, resolve_notice


def _ticket(**ov: object) -> Ticket:
    base: dict[str, object] = {
        "short_code": "TKT-NS-1",
        "source_code": "ksm",
        "source_ticket_id": "BILL-1",
        "type": "Raw",
        "status": "received",
        "title": "t",
    }
    base.update(ov)
    return Ticket(**base)  # type: ignore[arg-type]


def test_redis_hit_wins_over_db() -> None:
    store = FakeNoticeStore()
    store.put("BILL-1", NoticeInfo(notice_num="REDIS-N", subscribe_num="REDIS-S"))
    t = _ticket(ksm_notice_num="DB-N", ksm_subscribe_num="DB-S")
    got = resolve_notice(store, "BILL-1", t)
    assert got == NoticeInfo(notice_num="REDIS-N", subscribe_num="REDIS-S")


def test_redis_miss_falls_back_to_db_columns() -> None:
    store = FakeNoticeStore()
    t = _ticket(ksm_notice_num="DB-N", ksm_subscribe_num="DB-S")
    got = resolve_notice(store, "BILL-1", t)
    assert got == NoticeInfo(notice_num="DB-N", subscribe_num="DB-S")


def test_no_notice_store_falls_back_to_db_columns() -> None:
    t = _ticket(ksm_notice_num="DB-N", ksm_subscribe_num="DB-S")
    got = resolve_notice(None, "BILL-1", t)
    assert got == NoticeInfo(notice_num="DB-N", subscribe_num="DB-S")


def test_neither_redis_nor_db_returns_none() -> None:
    store = FakeNoticeStore()
    t = _ticket()
    assert resolve_notice(store, "BILL-1", t) is None
    assert resolve_notice(None, "BILL-1", t) is None
    assert resolve_notice(None, "BILL-1", None) is None


def test_partial_db_columns_treated_as_missing() -> None:
    """DB 列只有一半有值（数据异常/未完全写入）→ 不当作可用 notice。"""
    store = FakeNoticeStore()
    t = _ticket(ksm_notice_num="DB-N", ksm_subscribe_num=None)
    assert resolve_notice(store, "BILL-1", t) is None
