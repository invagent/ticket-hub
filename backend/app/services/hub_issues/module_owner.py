"""模块负责人查询：产品线+模块 → assignment_scopes_module 的负责人 User。

推 Linear 时的默认 assignee 来源。查不到或无有效用户返回 None，由调用方回落。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import User
from app.repositories.assignment_scope import AssignmentScopeRepository


def resolve_module_owner(
    db: Session, product_line_code: str | None, module: str | None
) -> User | None:
    if not product_line_code or not module:
        return None
    uids = AssignmentScopeRepository(db).find_user_ids_by_module(product_line_code, module)
    for uid in uids:
        u = db.get(User, uid)
        if u is not None and u.deleted_at is None and u.is_active:
            return u
    return None
