"""Redis-backed cache for the latest (noticeNum, subscribeNum) per billId.

Why this exists (D2-F):
    KSM may push the same billId multiple times in quick succession (every
    state change). Each push carries its own (noticeNum, subscribeNum) pair
    that's required to fetch the *full* ticket via /subscribeCallback.

    If the BackgroundTask N+1 reads the noticeNum stamped at time T, but at
    time T+ε KSM has already advanced the workflow, the call returns stale
    data. Storing the **latest** pair per billId and having every async fetch
    read from the store guarantees we always pull the most up-to-date state
    (and concurrent fetches all converge on the same answer; ingester dedup
    handles writes).

Storage shape:
    Key:   ksm:webhook:notice:<billId>
    Value: {"notice_num": "...", "subscribe_num": "..."}  (JSON)
    TTL:   24h — KSM workflows complete well within a day; stale keys
           auto-clean even if a billId stops getting pushes.

Backed by `redis` (already a project dep). For tests, `FakeNoticeStore`
provides a drop-in in-memory replacement.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

import redis


@dataclass(slots=True, frozen=True)
class NoticeInfo:
    notice_num: str
    subscribe_num: str


_KEY_PREFIX = "ksm:webhook:notice:"
_TTL_SECONDS = 24 * 60 * 60


class NoticeStoreLike(Protocol):
    def put(self, bill_id: str, notice: NoticeInfo) -> None: ...
    def get(self, bill_id: str) -> NoticeInfo | None: ...


class NoticeStore:
    """Redis-backed implementation. One instance per process is fine."""

    def __init__(self, *, redis_url: str) -> None:
        self._r = redis.Redis.from_url(redis_url, decode_responses=True)

    def put(self, bill_id: str, notice: NoticeInfo) -> None:
        self._r.set(
            _KEY_PREFIX + bill_id,
            json.dumps(
                {"notice_num": notice.notice_num, "subscribe_num": notice.subscribe_num}
            ),
            ex=_TTL_SECONDS,
        )

    def get(self, bill_id: str) -> NoticeInfo | None:
        raw = self._r.get(_KEY_PREFIX + bill_id)
        if not raw:
            return None
        d = json.loads(raw)
        return NoticeInfo(notice_num=d["notice_num"], subscribe_num=d["subscribe_num"])


class FakeNoticeStore:
    """In-memory test double. Same `NoticeStoreLike` interface."""

    def __init__(self) -> None:
        self._data: dict[str, NoticeInfo] = {}

    def put(self, bill_id: str, notice: NoticeInfo) -> None:
        self._data[bill_id] = notice

    def get(self, bill_id: str) -> NoticeInfo | None:
        return self._data.get(bill_id)
