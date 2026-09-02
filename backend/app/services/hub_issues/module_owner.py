"""模块研发负责人查询：产品线+模块 → modules.dev_owners 轮询选人。

数据源是 modules.dev_owners（目录管理页维护，顿号/逗号分隔的多个姓名），不是
assignment_scopes_module（那张表服务 Router 的入库处理人路由，是另一套独立机制，
本模块不读它）。

两个入口：
- peek_module_owner：只读预览「下一次会选中的人」，不推进游标。用于展示建议人选
  （如「待推 Linear」队列的 default_assignee），或仅判断「责任人是否确定」而不
  产生真实推送动作的场景。
- consume_module_owner：选定并推进游标。只在真正执行推送/确认动作时调用一次。

多人时按 dev_owners 里出现的顺序轮询：游标 % 人数 决定这次选谁，consume 后游标
+1 取模。取模运算天然容错 dev_owners 编辑（人员增减）导致的游标越界，不需要显式
重置逻辑。
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Module, User


def _split_dev_owner_names(raw: str | None) -> list[str]:
    """按顿号/中英文逗号拆分 dev_owners，去空白去空项，保持原始出现顺序。"""
    if not raw:
        return []
    parts = re.split(r"[、,，]", raw)
    return [p.strip() for p in parts if p.strip()]


def _lookup_module_and_names(
    db: Session,
    product_line_code: str | None,
    module: str | None,
    *,
    for_update: bool = False,
) -> tuple[Module | None, list[str]]:
    if not product_line_code or not module:
        return None, []
    stmt = select(Module).where(
        Module.product_line_code == product_line_code,
        Module.name == module,
        Module.status == "enabled",
    )
    if for_update:
        stmt = stmt.with_for_update()
    row = db.execute(stmt).scalar_one_or_none()
    if row is None:
        return None, []
    return row, _split_dev_owner_names(row.dev_owners)


def _resolve_user_by_name(db: Session, name: str) -> User | None:
    u = (
        db.execute(select(User).where(User.name == name).order_by(User.id))
        .scalars()
        .first()
    )
    if u is None or u.deleted_at is not None or not u.is_active:
        return None
    return u


def peek_module_owner(
    db: Session, product_line_code: str | None, module: str | None
) -> User | None:
    """只读预览：不推进游标、不写库。查不到或该模块无 dev_owners 返回 None。"""
    mod_row, names = _lookup_module_and_names(db, product_line_code, module)
    if mod_row is None or not names:
        return None
    idx = mod_row.dev_owner_rotation_cursor % len(names)
    return _resolve_user_by_name(db, names[idx])


def consume_module_owner(
    db: Session, product_line_code: str | None, module: str | None
) -> User | None:
    """选定并推进游标（并发安全：Postgres 下用行锁；sqlite 单测环境
    with_for_update() 被静默忽略，不影响功能正确性）。不自行 commit——锁随调用方
    事务边界释放。

    游标始终前进，即使这一位对应的 User 当前不可用（inactive/已删）——该轮询位
    「应该」被消费，只是这次刚好人不可用，不做二次查找跳过，保持轮询顺序稳定。
    """
    mod_row, names = _lookup_module_and_names(
        db, product_line_code, module, for_update=True
    )
    if mod_row is None or not names:
        return None
    idx = mod_row.dev_owner_rotation_cursor % len(names)
    owner = _resolve_user_by_name(db, names[idx])
    mod_row.dev_owner_rotation_cursor = (idx + 1) % len(names)
    return owner
