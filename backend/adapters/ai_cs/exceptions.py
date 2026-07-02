"""AI 客服 adapter exception hierarchy."""

from __future__ import annotations


class AiCsError(Exception):
    """Base class for all AI 客服 adapter errors."""


class AiCsAuthError(AiCsError):
    """Token acquisition failed / 401/403 — bad appid/app_key or expired sign."""


class AiCsBusinessError(AiCsError):
    """Envelope returned errcode != '0000' (HTTP 200 with business error)."""

    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code


class AiCsNetworkError(AiCsError):
    """Network-level failure (timeout, connection refused, DNS)."""
