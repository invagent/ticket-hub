"""Zammad adapter — HTTP client + webhook payload DTOs."""

from .client import ZammadClient
from .exceptions import ZammadAuthError, ZammadBusinessError, ZammadError, ZammadNetworkError
from .types import ZammadArticle, ZammadConfig, ZammadCustomer, ZammadTicket

__all__ = [
    "ZammadClient",
    "ZammadConfig",
    "ZammadTicket",
    "ZammadCustomer",
    "ZammadArticle",
    "ZammadError",
    "ZammadAuthError",
    "ZammadBusinessError",
    "ZammadNetworkError",
]
