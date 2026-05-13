"""config_warnings.py — detect system configuration gaps for the supervisor UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.models import AssignmentScopeModule, Module
from app.services.system_settings import get_default_pool_user_id

WarningCode = Literal["module_no_assignee", "no_default_pool"]


@dataclass(frozen=True)
class ConfigWarning:
    code: WarningCode
    product_line_code: str | None
    module: str | None
    detail: str


def get_config_warnings(db: Session) -> list[ConfigWarning]:
    warnings: list[ConfigWarning] = []

    # Check 1: active modules with no assignment scope
    scope_exists = exists().where(
        AssignmentScopeModule.product_line_code == Module.product_line_code,
        AssignmentScopeModule.module == Module.name,
    )
    rows = db.execute(
        select(Module.product_line_code, Module.name)
        .where(Module.is_active.is_(True))
        .where(~scope_exists)
        .order_by(Module.product_line_code, Module.name)
    ).all()
    for pl_code, mod_name in rows:
        warnings.append(
            ConfigWarning(
                code="module_no_assignee",
                product_line_code=pl_code,
                module=mod_name,
                detail=f"模块「{pl_code} / {mod_name}」未配置处理人，该模块的工单将落入兜底池。请前往「管理后台 → 分工配置 → Module 分工」为该模块指定处理人。",
            )
        )

    # Check 2: no default_pool configured
    if get_default_pool_user_id(db) is None:
        warnings.append(
            ConfigWarning(
                code="no_default_pool",
                product_line_code=None,
                module=None,
                detail="系统未配置兜底处理人，无分工匹配的工单将无人处理。请在主管工作台配置警告处直接选择兜底处理人。",
            )
        )

    return warnings
