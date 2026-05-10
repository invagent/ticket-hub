"""GLM adapter exception hierarchy."""

from __future__ import annotations


class GLMError(Exception):
    """Base class for all GLM adapter errors."""


class GLMAuthError(GLMError):
    """401/403 — invalid api_key or insufficient privileges."""


class GLMBusinessError(GLMError):
    """4xx other than auth, or 200 with `error` block."""

    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code


class GLMNetworkError(GLMError):
    """Network-level failure (timeout, connection refused, DNS)."""
