"""Zammad adapter exception hierarchy."""

from __future__ import annotations


class ZammadError(Exception):
    """Base class for all Zammad adapter errors."""


class ZammadAuthError(ZammadError):
    """Authentication / authorisation failure (401 / 403)."""


class ZammadBusinessError(ZammadError):
    """Zammad returned a business-logic error (4xx other than auth)."""


class ZammadNetworkError(ZammadError):
    """Network-level failure (timeout, connection refused)."""
