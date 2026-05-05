"""Zhichi adapter error hierarchy."""

from __future__ import annotations


class ZhichiError(Exception):
    """Base class."""


class ZhichiAuthError(ZhichiError):
    """token issuance failure or HTTP 401 after one retry."""


class ZhichiBusinessError(ZhichiError):
    """Non-`000000` ret_code (Zhichi's domain-level failure)."""

    def __init__(self, op: str, ret_code: str, ret_msg: str = "") -> None:
        super().__init__(f"Zhichi {op} failed: ret_code={ret_code} ret_msg={ret_msg}")
        self.op = op
        self.ret_code = ret_code
        self.ret_msg = ret_msg
