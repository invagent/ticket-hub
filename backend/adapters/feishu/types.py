"""Feishu adapter DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class FeishuConfig:
    app_id: str
    app_secret: str
    app_token: str = ""  # bitable app id
    table_id: str = ""  # ticket bitable table id
    duty_table_id: str = ""  # 值班表 table id (legacy)
    base_url: str = "https://open.feishu.cn"

    @classmethod
    def from_settings(cls, s: Any) -> FeishuConfig:
        return cls(
            app_id=s.feishu_app_id,
            app_secret=s.feishu_app_secret,
            app_token=s.feishu_app_token,
            table_id=s.feishu_table_id,
            duty_table_id=s.feishu_duty_table_id,
        )


@dataclass(slots=True, frozen=True)
class BitableFilterCondition:
    """One condition in a Bitable search filter."""

    field_name: str
    operator: str  # "is", "isNot", "contains", ...
    value: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"field_name": self.field_name, "operator": self.operator, "value": self.value}


@dataclass(slots=True, frozen=True)
class Employee:
    """Subset of飞书 directory employee result."""

    name: str
    job_number: str = ""
    email: str = ""
    mobile: str = ""  # +86 prefix stripped
    employee_id: str = ""


# ---- contact v3 (D2-E feishu user sync) -----------------------------------


@dataclass(slots=True, frozen=True)
class ContactUser:
    """A single user from /open-apis/contact/v3/users/* (subset).

    Field map (Feishu → ours):
      open_id          → feishu_uid
      name             → name
      employee_no      → employee_no
      email            → email
      mobile           → mobile (we strip +86 prefix)
      department_ids   → for org chart context (unused by sync; surfaced)
      status.is_activated → if false, sync should not bring user in (or mark inactive)
    """

    open_id: str
    name: str
    employee_no: str = ""
    email: str = ""
    mobile: str = ""
    department_ids: tuple[str, ...] = ()
    is_activated: bool = True

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ContactUser:
        mobile = str(d.get("mobile") or "")
        if mobile.startswith("+86"):
            mobile = mobile[3:]
        status = d.get("status") or {}
        return cls(
            open_id=str(d.get("open_id") or ""),
            name=str(d.get("name") or ""),
            employee_no=str(d.get("employee_no") or ""),
            email=str(d.get("email") or d.get("enterprise_email") or ""),
            mobile=mobile,
            department_ids=tuple(d.get("department_ids") or ()),
            is_activated=bool(status.get("is_activated", True)),
        )


@dataclass(slots=True, frozen=True)
class Department:
    """A single department from /open-apis/contact/v3/departments/*."""

    open_department_id: str
    department_id: str  # opaque internal id
    name: str
    parent_department_id: str = ""
    member_count: int = 0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Department:
        return cls(
            open_department_id=str(d.get("open_department_id") or ""),
            department_id=str(d.get("department_id") or ""),
            name=str(d.get("name") or ""),
            parent_department_id=str(d.get("parent_department_id") or ""),
            member_count=int(d.get("member_count") or 0),
        )
