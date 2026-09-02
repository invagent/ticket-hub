"""模块负责人查询单测：peek_module_owner（只读预览）/ consume_module_owner
（选定并推进轮询游标）。数据源是 modules.dev_owners，不是
assignment_scopes_module（那张表服务 Router 的入库处理人路由，另一套机制）。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Module, ProductLine, User
from app.services.hub_issues.module_owner import consume_module_owner, peek_module_owner


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
    dev_owners: str | None,
    cursor: int = 0,
    status: str = "enabled",
) -> Module:
    if db.query(ProductLine).filter_by(code=pl).first() is None:
        db.add(ProductLine(code=pl, name=pl))
        db.flush()
    m = Module(
        product_line_code=pl,
        name=mod,
        dev_owners=dev_owners,
        dev_owner_rotation_cursor=cursor,
        status=status,
    )
    db.add(m)
    db.flush()
    return m


def test_peek_none_inputs(db_session: Session) -> None:
    assert peek_module_owner(db_session, None, None) is None
    assert consume_module_owner(db_session, None, None) is None


def test_peek_module_not_found_returns_none(db_session: Session) -> None:
    assert peek_module_owner(db_session, "发票云", "不存在模块") is None


def test_peek_empty_dev_owners_returns_none(db_session: Session) -> None:
    _seed_module(db_session, mod="空模块", dev_owners=None)
    db_session.commit()
    assert peek_module_owner(db_session, "发票云", "空模块") is None


def test_module_disabled_returns_none_for_peek_and_consume(db_session: Session) -> None:
    u = _seed_user(db_session, "owner1")
    _seed_module(db_session, mod="停用模块", dev_owners=u.name, status="disabled")
    db_session.commit()
    assert peek_module_owner(db_session, "发票云", "停用模块") is None
    assert consume_module_owner(db_session, "发票云", "停用模块") is None


def test_peek_single_owner_hit(db_session: Session) -> None:
    u = _seed_user(db_session, "owner1")
    _seed_module(db_session, mod="开票", dev_owners=u.name)
    db_session.commit()
    got = peek_module_owner(db_session, "发票云", "开票")
    assert got is not None and got.id == u.id


def test_peek_does_not_advance_cursor(db_session: Session) -> None:
    u1 = _seed_user(db_session, "汪意")
    _seed_user(db_session, "魏文浩")
    _seed_module(db_session, mod="开票", dev_owners="汪意、魏文浩")
    db_session.commit()
    got1 = peek_module_owner(db_session, "发票云", "开票")
    got2 = peek_module_owner(db_session, "发票云", "开票")
    assert got1 is not None and got1.id == u1.id
    assert got2 is not None and got2.id == u1.id  # 同一位，游标没被推进
    mod = db_session.query(Module).filter_by(product_line_code="发票云", name="开票").one()
    assert mod.dev_owner_rotation_cursor == 0


def test_consume_advances_cursor_round_robin(db_session: Session) -> None:
    u1 = _seed_user(db_session, "汪意")
    u2 = _seed_user(db_session, "魏文浩")
    u3 = _seed_user(db_session, "杨吉")
    _seed_module(db_session, mod="开票", dev_owners="汪意、魏文浩、杨吉")
    db_session.commit()
    picks = [consume_module_owner(db_session, "发票云", "开票").id for _ in range(4)]
    assert picks == [u1.id, u2.id, u3.id, u1.id]  # 第4次回到人1，轮询循环


def test_consume_wraps_after_dev_owners_shrink(db_session: Session) -> None:
    u1 = _seed_user(db_session, "汪意")
    mod = _seed_module(db_session, mod="开票", dev_owners="汪意", cursor=5)
    db_session.commit()
    got = consume_module_owner(db_session, "发票云", "开票")
    assert got is not None and got.id == u1.id
    db_session.flush()  # session autoflush=False：refresh() 前必须显式 flush 才能看到刚写的游标
    db_session.refresh(mod)
    assert mod.dev_owner_rotation_cursor == 0  # 取模折算，不抛异常


def test_consume_inactive_owner_returns_none_but_cursor_advances(db_session: Session) -> None:
    _seed_user(db_session, "杨吉", active=False)
    u_active = _seed_user(db_session, "汪意")
    mod = _seed_module(db_session, mod="开票", dev_owners="杨吉、汪意")
    db_session.commit()
    got1 = consume_module_owner(db_session, "发票云", "开票")
    assert got1 is None  # 轮询位对应的人 inactive，不做二次查找跳过
    db_session.flush()  # session autoflush=False：refresh() 前必须显式 flush 才能看到刚写的游标
    db_session.refresh(mod)
    assert mod.dev_owner_rotation_cursor == 1  # 但游标仍前进
    got2 = consume_module_owner(db_session, "发票云", "开票")
    assert got2 is not None and got2.id == u_active.id


def test_split_dev_owner_names_separator_compat(db_session: Session) -> None:
    u1 = _seed_user(db_session, "汪意")
    u2 = _seed_user(db_session, "魏文浩")
    _seed_module(db_session, mod="逗号分隔", dev_owners="汪意,魏文浩")
    _seed_module(db_session, mod="中文逗号分隔", dev_owners="汪意，魏文浩")
    db_session.commit()
    picks_comma = [
        consume_module_owner(db_session, "发票云", "逗号分隔").id for _ in range(2)
    ]
    picks_zh_comma = [
        consume_module_owner(db_session, "发票云", "中文逗号分隔").id for _ in range(2)
    ]
    assert picks_comma == [u1.id, u2.id]
    assert picks_zh_comma == [u1.id, u2.id]
