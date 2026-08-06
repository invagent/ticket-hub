"""集成点：毕业预分配、resolve_op_handler 回落、drain 口径不变。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import HubIssue, User
from app.services.hub_issues.op_status import resolve_op_handler


def _user(db: Session, uid: int, name: str, active: bool = True) -> None:
    db.add(User(id=uid, feishu_uid=f"ou_{uid}", name=name, role="assignee", is_active=active))


def test_resolve_prefers_preassigned(db_session: Session) -> None:
    _user(db_session, 3, "运营小美")
    h = HubIssue(
        short_code="HUB-1", type="Operation", status="created", title="t", op_handler_user_id=3
    )
    db_session.add(h)
    db_session.commit()
    assert resolve_op_handler(db_session, h, get_settings()) == "运营小美"


def test_resolve_falls_back_when_no_preassign(db_session: Session) -> None:
    h = HubIssue(
        short_code="HUB-2", type="Operation", status="created", title="t", op_handler_user_id=None
    )
    db_session.add(h)
    db_session.commit()
    # 无 default_pool 配置时 resolve_supervisor_name 返回 "主管"
    assert resolve_op_handler(db_session, h, get_settings()) == "主管"


def test_resolve_falls_back_when_preassigned_inactive(db_session: Session) -> None:
    _user(db_session, 4, "已离职", active=False)
    h = HubIssue(
        short_code="HUB-3", type="Operation", status="created", title="t", op_handler_user_id=4
    )
    db_session.add(h)
    db_session.commit()
    assert resolve_op_handler(db_session, h, get_settings()) == "主管"
