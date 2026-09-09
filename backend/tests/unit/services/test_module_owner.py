"""模块研发责任人（ADR-0017 D3：一个模块绑死一个人）。

数据源是 modules.dev_owner_user_id；为空时过渡期回落 legacy dev_owners 首名。
peek_module_owner / consume_module_owner 是 resolve_module_owner 的别名，结果恒等，
consume 不再推进游标。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Module, ProductLine, User
from app.services.hub_issues.module_owner import (
    consume_module_owner,
    peek_module_owner,
    resolve_module_owner,
)


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


def _seed_module(
    db: Session,
    *,
    pl: str = "发票云",
    mod: str = "开票",
    dev_owner_user_id: int | None = None,
    dev_owners: str | None = None,
    cursor: int = 0,
    status: str = "enabled",
) -> Module:
    if db.query(ProductLine).filter_by(code=pl).first() is None:
        db.add(ProductLine(code=pl, name=pl))
        db.flush()
    m = Module(
        product_line_code=pl,
        name=mod,
        dev_owner_user_id=dev_owner_user_id,
        dev_owners=dev_owners,
        dev_owner_rotation_cursor=cursor,
        status=status,
    )
    db.add(m)
    db.flush()
    return m


def test_none_inputs(db_session: Session) -> None:
    assert resolve_module_owner(db_session, None, None) is None
    assert peek_module_owner(db_session, None, None) is None
    assert consume_module_owner(db_session, None, None) is None


def test_module_not_found_or_unconfigured(db_session: Session) -> None:
    _seed_module(db_session, mod="空模块")
    db_session.commit()
    assert resolve_module_owner(db_session, "发票云", "不存在模块") is None
    assert resolve_module_owner(db_session, "发票云", "空模块") is None


def test_module_disabled_returns_none(db_session: Session) -> None:
    u = _seed_user(db_session, "owner1")
    _seed_module(db_session, mod="停用模块", dev_owner_user_id=u.id, status="disabled")
    db_session.commit()
    assert peek_module_owner(db_session, "发票云", "停用模块") is None
    assert consume_module_owner(db_session, "发票云", "停用模块") is None


def test_dev_owner_user_id_is_authoritative(db_session: Session) -> None:
    bound = _seed_user(db_session, "绑定人")
    _seed_user(db_session, "字串首名")
    _seed_module(db_session, mod="开票", dev_owner_user_id=bound.id, dev_owners="字串首名、绑定人")
    db_session.commit()
    got = resolve_module_owner(db_session, "发票云", "开票")
    assert got is not None and got.id == bound.id  # id 绑死优先于 legacy 姓名字串


def test_inactive_bound_owner_returns_none_no_fallback(db_session: Session) -> None:
    inactive = _seed_user(db_session, "已离职", active=False)
    _seed_user(db_session, "字串首名")
    _seed_module(db_session, mod="开票", dev_owner_user_id=inactive.id, dev_owners="字串首名")
    db_session.commit()
    # 绑定人已停用 → None 停人工闸门，不静默换人（责任一致性）
    assert resolve_module_owner(db_session, "发票云", "开票") is None


def test_legacy_dev_owners_first_name_fallback(db_session: Session) -> None:
    u1 = _seed_user(db_session, "汪意")
    _seed_user(db_session, "魏文浩")
    _seed_module(db_session, mod="开票", dev_owners="汪意、魏文浩")
    _seed_module(db_session, mod="逗号", dev_owners="汪意,魏文浩")
    _seed_module(db_session, mod="中文逗号", dev_owners="汪意，魏文浩")
    db_session.commit()
    for mod in ("开票", "逗号", "中文逗号"):
        got = resolve_module_owner(db_session, "发票云", mod)
        assert got is not None and got.id == u1.id, mod


def test_consume_equals_peek_and_never_advances_cursor(db_session: Session) -> None:
    u1 = _seed_user(db_session, "汪意")
    _seed_user(db_session, "魏文浩")
    mod = _seed_module(db_session, mod="开票", dev_owners="汪意、魏文浩", cursor=0)
    db_session.commit()
    picks = [consume_module_owner(db_session, "发票云", "开票").id for _ in range(4)]  # type: ignore[union-attr]
    assert picks == [u1.id] * 4  # 不再轮询
    db_session.flush()
    db_session.refresh(mod)
    assert mod.dev_owner_rotation_cursor == 0
    assert peek_module_owner(db_session, "发票云", "开票").id == u1.id  # type: ignore[union-attr]
