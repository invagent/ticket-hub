"""resolve_ksm_identity：处理人身份优先，缺失时回落全局固定配置。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Ticket, User
from app.services.ksm.identity import resolve_ksm_identity


def _settings(**ov: object) -> Settings:
    base: dict[str, object] = {"ksm_handler_name": "李志坚", "ksm_handler_number": "10086"}
    base.update(ov)
    return Settings(**base)  # type: ignore[arg-type]


def _ticket(db: Session, **ov: object) -> Ticket:
    base: dict[str, object] = {
        "short_code": "TKT-ID-1",
        "source_code": "ksm",
        "source_ticket_id": "BILL-ID-1",
        "type": "Raw",
        "status": "received",
        "title": "t",
    }
    base.update(ov)
    t = Ticket(**base)  # type: ignore[arg-type]
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def test_prefers_handler_with_ksm_account(db_session: Session) -> None:
    u = User(feishu_uid="ou_h1", name="张三", role="assignee", ksm_account="ZS001")
    db_session.add(u)
    db_session.flush()
    t = _ticket(db_session, handler_user_id=u.id)
    db_session.commit()

    identity = resolve_ksm_identity(db_session, t, _settings())
    assert identity is not None
    assert identity.source == "handler"
    assert identity.account == "张三"
    assert identity.account_name == "张三"
    assert identity.account_number == "ZS001"


def test_handler_without_ksm_account_falls_back_to_config(db_session: Session) -> None:
    u = User(feishu_uid="ou_h2", name="李四", role="assignee")  # 未配 ksm_account
    db_session.add(u)
    db_session.flush()
    t = _ticket(db_session, handler_user_id=u.id)
    db_session.commit()

    identity = resolve_ksm_identity(db_session, t, _settings())
    assert identity is not None
    assert identity.source == "fallback_config"
    assert identity.account == "李志坚"
    assert identity.account_number == "10086"


def test_no_handler_falls_back_to_config(db_session: Session) -> None:
    t = _ticket(db_session)  # handler_user_id 为空
    db_session.commit()

    identity = resolve_ksm_identity(db_session, t, _settings())
    assert identity is not None
    assert identity.source == "fallback_config"


def test_no_handler_and_no_fallback_returns_none(db_session: Session) -> None:
    t = _ticket(db_session)
    db_session.commit()

    identity = resolve_ksm_identity(
        db_session, t, _settings(ksm_handler_name="", ksm_handler_number="")
    )
    assert identity is None


def test_deleted_handler_falls_back_to_config(db_session: Session) -> None:
    """处理人已被软删（deleted_at 非空）→ 视为不可用，回落全局配置。"""
    from datetime import UTC, datetime

    u = User(
        feishu_uid="ou_h3",
        name="已离职",
        role="assignee",
        ksm_account="LEFT001",
        deleted_at=datetime.now(UTC),
    )
    db_session.add(u)
    db_session.flush()
    t = _ticket(db_session, handler_user_id=u.id)
    db_session.commit()

    identity = resolve_ksm_identity(db_session, t, _settings())
    assert identity is not None
    assert identity.source == "fallback_config"


def test_inactive_handler_falls_back_to_config(db_session: Session) -> None:
    """处理人 is_active=False（停用未软删）→ 同样视为不可用，回落全局配置。"""
    u = User(
        feishu_uid="ou_h4", name="已停用", role="assignee", ksm_account="OFF001", is_active=False
    )
    db_session.add(u)
    db_session.flush()
    t = _ticket(db_session, handler_user_id=u.id)
    db_session.commit()

    identity = resolve_ksm_identity(db_session, t, _settings())
    assert identity is not None
    assert identity.source == "fallback_config"
