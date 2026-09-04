"""KSM 操作员身份解析——接管/答复/补料/退回统一用处理人身份，而非固定配置.

之前所有 KSM 写操作（lock/handle/supply/return）都用 settings.ksm_handler_name/
ksm_handler_number 这一个全局固定身份，跟工单实际处理人是谁完全脱钩。现在改为
按 ticket.handler_user_id 查处理人的姓名 + KSM 账号（User.ksm_account，需要
运维在用户管理页面为每个可能处理 KSM 工单的人预先在 KSM 系统里开好账号并填入）。

尚未给处理人配置 ksm_account 时降级回落全局固定身份（settings.ksm_handler_name/
ksm_handler_number）——这是迁移期的容错，不是长期方案：运维数据补齐后自动切换
为处理人身份，不需要代码改动。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import Settings
from app.core.logging import get_logger
from app.models import Ticket, User

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class KsmIdentity:
    account: str
    account_name: str
    account_number: str
    source: str  # 'handler' | 'fallback_config'


def resolve_ksm_identity(db: Session, ticket: Ticket, settings: Settings) -> KsmIdentity | None:
    """处理人身份优先；缺失（未分配/无 ksm_account）时回落全局配置。全局配置也
    缺失则返回 None（调用方应跳过写操作，同现状 ksm_writeback_no_handler_identity）。
    """
    if ticket.handler_user_id is not None:
        user = db.get(User, ticket.handler_user_id)
        usable = (
            user is not None
            and user.deleted_at is None
            and user.is_active
            and user.name
            and user.ksm_account
        )
        if usable:
            assert user is not None
            return KsmIdentity(
                account=user.name,
                account_name=user.name,
                account_number=user.ksm_account,  # type: ignore[arg-type]
                source="handler",
            )
        logger.info(
            "ksm_identity_handler_unresolved",
            ticket_id=ticket.id,
            handler_user_id=ticket.handler_user_id,
            reason="user not found/inactive"
            if user is None or user.deleted_at is not None or not user.is_active
            else "missing name/ksm_account",
        )

    if settings.ksm_handler_name and settings.ksm_handler_number:
        return KsmIdentity(
            account=settings.ksm_handler_name,
            account_name=settings.ksm_handler_name,
            account_number=settings.ksm_handler_number,
            source="fallback_config",
        )
    return None
