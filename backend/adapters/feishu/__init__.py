"""Feishu OpenAPI adapter — full migration from feishu-python/app/feishu_client.py.

Subscopes:
  * Auth (tenant_access_token + 99991663 retry)
  * Bitable records (CRUD + search + parent linking)
  * Attachments (upload_media + download)
  * Directory (search_employee — used by D1 IdentityResolver)
  * Duty roster (legacy; will retire in D6 alongside Bitable storage)
  * Contact v3 (D2-E user sync — list_users_by_department, get_user_by_open_id)
"""

from .client import FeishuClient
from .exceptions import FeishuAuthError, FeishuBusinessError, FeishuError
from .types import BitableFilterCondition, ContactUser, Department, Employee, FeishuConfig

__all__ = [
    "BitableFilterCondition",
    "ContactUser",
    "Department",
    "Employee",
    "FeishuAuthError",
    "FeishuBusinessError",
    "FeishuClient",
    "FeishuConfig",
    "FeishuError",
]
