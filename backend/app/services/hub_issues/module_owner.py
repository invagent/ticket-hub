"""模块研发责任人查询（ADR-0017 D3：一个模块绑死一个人）。

数据源是 `modules.dev_owner_user_id`（目录管理页用户下拉单选），不是
assignment_scopes_module（那张表服务旧 Router 的入库处理人路由，本模块不读它）。

责任人解析链（固定、可审计）：
    手工指定（确认推送时选人，调用方处理） > modules.dev_owner_user_id
    > 过渡期回落 modules.dev_owners 首名（迁移 0048 回填漏网的模块） > None（停人工闸门）

历史上的多人轮询（dev_owners 逗号分隔 + dev_owner_rotation_cursor）已退役：违背
「研发责任一致性」原则且按姓名匹配无法审计。`peek_module_owner` / `consume_module_owner`
两个旧入口保留为 `resolve_module_owner` 的别名，让 6 个调用点零改动；consume 不再推进游标。
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Module, User


def _split_dev_owner_names(raw: str | None) -> list[str]:
    """按顿号/中英文逗号拆分 legacy dev_owners，去空白去空项，保持原始出现顺序。"""
    if not raw:
        return []
    parts = re.split(r"[、,，]", raw)
    return [p.strip() for p in parts if p.strip()]


def _lookup_module(db: Session, product_line_code: str | None, module: str | None) -> Module | None:
    if not product_line_code or not module:
        return None
    stmt = select(Module).where(
        Module.product_line_code == product_line_code,
        Module.name == module,
        Module.status == "enabled",
    )
    return db.execute(stmt).scalar_one_or_none()


def _active_user(u: User | None) -> User | None:
    if u is None or u.deleted_at is not None or not u.is_active:
        return None
    return u


def _resolve_user_by_name(db: Session, name: str) -> User | None:
    u = db.execute(select(User).where(User.name == name).order_by(User.id)).scalars().first()
    return _active_user(u)


def resolve_module_owner(
    db: Session, product_line_code: str | None, module: str | None
) -> User | None:
    """模块唯一研发责任人。模块不存在/停用/未配责任人/责任人已停用 → None。不写库。"""
    mod_row = _lookup_module(db, product_line_code, module)
    if mod_row is None:
        return None
    if mod_row.dev_owner_user_id is not None:
        return _active_user(db.get(User, mod_row.dev_owner_user_id))
    # 过渡期回落：legacy 姓名字串首名（迁移 0048 回填漏网 / 新增模块仍走 Excel 导入姓名）
    names = _split_dev_owner_names(mod_row.dev_owners)
    if names:
        return _resolve_user_by_name(db, names[0])
    return None


def peek_module_owner(
    db: Session, product_line_code: str | None, module: str | None
) -> User | None:
    """只读预览（= resolve_module_owner；保留旧名给既有调用点）。"""
    return resolve_module_owner(db, product_line_code, module)


def consume_module_owner(
    db: Session, product_line_code: str | None, module: str | None
) -> User | None:
    """选定责任人（= resolve_module_owner）。ADR-0017 起不再推进轮询游标：
    同一模块任何时刻只有一个研发责任人，peek 与 consume 结果恒等。"""
    return resolve_module_owner(db, product_line_code, module)
