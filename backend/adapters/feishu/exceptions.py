"""Feishu adapter error hierarchy."""

from __future__ import annotations


class FeishuError(Exception):
    """Base class for any Feishu OpenAPI failure."""


class FeishuAuthError(FeishuError):
    """tenant_access_token issuance failure (or 99991663 after one retry)."""


class FeishuBusinessError(FeishuError):
    """Non-zero `code` in JSON body (Feishu's domain-level failure)."""

    def __init__(self, op: str, code: int, message: str = "") -> None:
        super().__init__(f"Feishu {op} failed: code={code} msg={message}")
        self.op = op
        self.code = code
        self.message = message
