"""Linear adapter exception hierarchy."""

from __future__ import annotations


class LinearError(Exception):
    """Base class for all Linear adapter errors."""


class LinearAuthError(LinearError):
    """401/403 — invalid api_key or insufficient privileges."""


class LinearBusinessError(LinearError):
    """GraphQL errors returned in the response body."""

    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code


class LinearNetworkError(LinearError):
    """Network-level failure (timeout, connection refused, DNS)."""
