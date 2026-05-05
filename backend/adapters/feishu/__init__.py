"""Feishu OpenAPI adapter — full migration from feishu-python/app/feishu_client.py.

Subscopes:
  * Auth (tenant_access_token + 99991663 retry)
  * Bitable records (CRUD + search + parent linking)
  * Attachments (upload_media + download)
  * Directory (search_employee — used by D1 IdentityResolver)
  * Duty roster (legacy; will retire in D6 alongside Bitable storage)
"""

from .client import FeishuClient
from .exceptions import FeishuAuthError, FeishuBusinessError, FeishuError
from .types import BitableFilterCondition, Employee, FeishuConfig

__all__ = [
    "BitableFilterCondition",
    "Employee",
    "FeishuAuthError",
    "FeishuBusinessError",
    "FeishuClient",
    "FeishuConfig",
    "FeishuError",
]
