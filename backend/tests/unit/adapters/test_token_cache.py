"""Tests for the shared TokenCache."""

from __future__ import annotations

import threading
from itertools import count

import pytest

from adapters._token_cache import TokenCache


def test_first_call_invokes_refresh() -> None:
    calls = []

    def refresh() -> tuple[str, float]:
        calls.append(1)
        return "tok-1", 3600.0

    c = TokenCache()
    assert c.get(refresh) == "tok-1"
    assert len(calls) == 1


def test_subsequent_calls_use_cache() -> None:
    n = count()

    def refresh() -> tuple[str, float]:
        return f"tok-{next(n)}", 3600.0

    c = TokenCache()
    assert c.get(refresh) == "tok-0"
    assert c.get(refresh) == "tok-0"


def test_force_bypasses_cache() -> None:
    n = count()

    def refresh() -> tuple[str, float]:
        return f"tok-{next(n)}", 3600.0

    c = TokenCache()
    assert c.get(refresh) == "tok-0"
    assert c.get(refresh, force=True) == "tok-1"


def test_expired_token_triggers_refresh() -> None:
    n = count()
    fake_time = [0.0]

    def refresh() -> tuple[str, float]:
        return f"tok-{next(n)}", 100.0  # 100s TTL

    c = TokenCache()
    assert c.get(refresh, now=lambda: fake_time[0]) == "tok-0"
    fake_time[0] = 50.0  # still valid (within 100s minus 60s margin = 40s; just past)
    # Safety margin = 60s, so expires_at = 0 + 100 - 60 = 40. At t=50, expired.
    assert c.get(refresh, now=lambda: fake_time[0]) == "tok-1"


def test_safety_margin_prevents_late_returns() -> None:
    """Token with TTL=70s should expire at t=10 (70 - 60 safety margin)."""
    n = count()
    fake_time = [0.0]

    def refresh() -> tuple[str, float]:
        return f"tok-{next(n)}", 70.0

    c = TokenCache()
    assert c.get(refresh, now=lambda: fake_time[0]) == "tok-0"
    fake_time[0] = 9.99
    assert c.get(refresh, now=lambda: fake_time[0]) == "tok-0"
    fake_time[0] = 10.0
    assert c.get(refresh, now=lambda: fake_time[0]) == "tok-1"


def test_empty_token_raises() -> None:
    c = TokenCache(name="ksm")
    with pytest.raises(ValueError, match="empty token for ksm"):
        c.get(lambda: ("", 60.0))


def test_invalidate_forces_next_refresh() -> None:
    n = count()

    def refresh() -> tuple[str, float]:
        return f"tok-{next(n)}", 3600.0

    c = TokenCache()
    assert c.get(refresh) == "tok-0"
    c.invalidate()
    assert c.get(refresh) == "tok-1"


def test_concurrent_callers_only_refresh_once() -> None:
    """Double-checked locking: 50 threads → exactly 1 refresh call."""
    refresh_calls = []
    barrier = threading.Barrier(50)

    def refresh() -> tuple[str, float]:
        refresh_calls.append(1)
        return "shared-tok", 3600.0

    c = TokenCache()
    results: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        tok = c.get(refresh)
        with lock:
            results.append(tok)

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == ["shared-tok"] * 50
    assert len(refresh_calls) == 1
