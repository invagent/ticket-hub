"""KSM 操作员身份解析——接管/答复/补料/退回统一用处理人身份，而非固定配置.

之前所有 KSM 写操作（lock/handle/supply/return）都用 settings.ksm_handler_name/
ksm_handler_number 这一个全局固定身份，跟工单实际处理人是谁完全脱钩。现在改为
按 ticket.handler_user_id 查处理人的姓名 + KSM 账号。

KSM 账号取值：User.ksm_account 优先；为空时回落 User.employee_no——实测 KSM
工号与本系统飞书同步的 employee_no 是同一套编号（2026-09 SIT 环境验证：全局
兜底账号「杨慧莉/53690」与 employee_no=53690 的用户完全对应），飞书同步已经
免运维填好大多数人的 employee_no，不需要再手动逐个填 ksm_account。ksm_account
仍保留：极少数 KSM 账号与 employee_no 不一致的人，可在用户管理页手动覆盖。

两者都空时降级回落全局固定身份（settings.ksm_handler_name/ksm_handler_number）
——这是兜底容错，不是长期方案。
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
    """处理人身份优先；缺失（未分配/无 ksm_account 也无 employee_no）时回落全局
    配置。全局配置也缺失则返回 None（调用方应跳过写操作，同现状
    ksm_writeback_no_handler_identity）。
    """
    if ticket.handler_user_id is not None:
        user = db.get(User, ticket.handler_user_id)
        account_number = (user.ksm_account or user.employee_no) if user is not None else None
        usable = (
            user is not None
            and user.deleted_at is None
            and user.is_active
            and user.name
            and account_number
        )
        if usable:
            assert user is not None and account_number is not None
            return KsmIdentity(
                account=user.name,
                account_name=user.name,
                account_number=account_number,
                source="handler",
            )
        logger.info(
            "ksm_identity_handler_unresolved",
            ticket_id=ticket.id,
            handler_user_id=ticket.handler_user_id,
            reason="user not found/inactive"
            if user is None or user.deleted_at is not None or not user.is_active
            else "missing name/ksm_account/employee_no",
        )

    if settings.ksm_handler_name and settings.ksm_handler_number:
        return KsmIdentity(
            account=settings.ksm_handler_name,
            account_name=settings.ksm_handler_name,
            account_number=settings.ksm_handler_number,
            source="fallback_config",
        )
    return None
