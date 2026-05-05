"""KSM adapter error hierarchy."""

from __future__ import annotations


class KSMError(Exception):
    """Base class. Wraps any KSM-side failure (HTTP, business, parse)."""


class KSMAuthError(KSMError):
    """401 / token issuance failure. Triggers a single force-refresh retry upstream."""


class KSMBusinessError(KSMError):
    """`status=false` from KSM business endpoint (functional error, NOT a network issue)."""

    def __init__(self, op: str, message: str, error_code: str | None = None) -> None:
        super().__init__(f"KSM {op} failed: {message}")
        self.op = op
        self.message = message
        self.error_code = error_code
