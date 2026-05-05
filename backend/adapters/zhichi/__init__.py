"""Zhichi (智齿 / Soboten) adapter — full migration from feishu-python/app/zhichi_client.py."""

from .client import ZhichiClient
from .exceptions import ZhichiAuthError, ZhichiBusinessError, ZhichiError
from .types import Agent, ReplyTicketRequest, ZhichiConfig

__all__ = [
    "Agent",
    "ReplyTicketRequest",
    "ZhichiAuthError",
    "ZhichiBusinessError",
    "ZhichiClient",
    "ZhichiConfig",
    "ZhichiError",
]
