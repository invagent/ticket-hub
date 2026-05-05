"""Thread-safe TTL-bounded token cache shared by adapter implementations."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(slots=True)
class _CacheEntry:
    token: str = ""
    expires_at: float = 0.0


class TokenCache:
    """Per-instance token cache with double-checked locking.

    The cache stores one (token, expires_at) tuple. Callers provide a
    `refresh()` callable that returns `(token, ttl_seconds)`. The cache
    handles concurrency, force-refresh, and a small safety margin so we
    never return a token in its last 60 seconds.

    Why per-instance: enables clean unit tests (one cache per fixture
    instead of fighting a module-level global) and supports multi-tenant
    deployments later.
    """

    SAFETY_MARGIN_SECONDS = 60.0

    def __init__(self, name: str = "token") -> None:
        self._name = name
        self._lock = threading.Lock()
        self._entry = _CacheEntry()

    def get(
        self,
        refresh: Callable[[], tuple[str, float]],
        *,
        force: bool = False,
        now: Callable[[], float] = time.time,
    ) -> str:
        """Return a valid token, refreshing as needed.

        `refresh` is invoked under lock and must return (token, ttl_seconds).
        Pass `force=True` after a 401 to bypass the cache.
        Pass a custom `now` for deterministic tests.
        """
        ts = now()
        if not force and self._entry.token and ts < self._entry.expires_at:
            return self._entry.token

        with self._lock:
            ts = now()
            if not force and self._entry.token and ts < self._entry.expires_at:
                return self._entry.token

            token, ttl = refresh()
            if not token:
                raise ValueError(f"refresh returned empty token for {self._name}")
            self._entry = _CacheEntry(
                token=token, expires_at=ts + max(ttl - self.SAFETY_MARGIN_SECONDS, 0.0)
            )
            return token

    def invalidate(self) -> None:
        with self._lock:
            self._entry = _CacheEntry()
