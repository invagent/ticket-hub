"""模块负责人查询单测。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import AssignmentScopeModule, ProductLine, User
from app.services.hub_issues.module_owner import resolve_module_owner


def _seed_user(db: Session, name: str, active: bool = True) -> User:
    u = User(
        name=name,
        email=f"{name}@x.com",
        feishu_uid=f"ou_{name}",
        role="assignee",
        is_active=active,
    )
    db.add(u)
    db.flush()
    return u


def _seed_scope(db: Session, uid: int, pl: str = "发票云", mod: str = "开票") -> None:
    if db.query(ProductLine).filter_by(code=pl).first() is None:
        db.add(ProductLine(code=pl, name=pl))
        db.flush()
    db.add(AssignmentScopeModule(user_id=uid, product_line_code=pl, module=mod))
    db.flush()


def test_resolve_module_owner_hit(db_session: Session) -> None:
    u = _seed_user(db_session, "owner1")
    _seed_scope(db_session, u.id)
    db_session.commit()
    got = resolve_module_owner(db_session, "发票云", "开票")
    assert got is not None and got.id == u.id


def test_resolve_module_owner_miss_returns_none(db_session: Session) -> None:
    assert resolve_module_owner(db_session, "发票云", "不存在模块") is None


def test_resolve_module_owner_none_inputs(db_session: Session) -> None:
    assert resolve_module_owner(db_session, None, None) is None


def test_resolve_module_owner_skips_inactive(db_session: Session) -> None:
    u = _seed_user(db_session, "owner_inactive", active=False)
    _seed_scope(db_session, u.id, mod="收票")
    db_session.commit()
    assert resolve_module_owner(db_session, "发票云", "收票") is None
