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
