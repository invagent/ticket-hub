"""失败 sync_outbox 行的处理人自助重试（跨 KSM/智齿两个 sender 通用）。

失败行永久卡在 status='failed'（两个 sender 的 drain() 只扫 pending，绝不
会自己再碰它）；这里提供「点一下立即重跑这一行」的同步入口，复用 sender 内部
已有的单行处理逻辑（_process_row），不重新实现发送。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import SyncOutbox

logger = get_logger(__name__)


class OutboxRetryError(Exception):
    """Retry can't be attempted; message is operator-facing."""


@dataclass(slots=True, frozen=True)
class OutboxRetryResult:
    outbox_id: int
    sent: bool  # True=本次重试成功（status→sent）
    error: str | None  # sent=False 时的失败原因（last_error）


def latest_failed_outbox_for_ticket(db: Session, ticket_id: int) -> SyncOutbox | None:
    """该工单最新一条 status='failed' 的 outbox 行（用于详情页横幅+重试目标）。"""
    return db.execute(
        select(SyncOutbox)
        .where(SyncOutbox.ticket_id == ticket_id, SyncOutbox.status == "failed")
        .order_by(SyncOutbox.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def retry_outbox_row(
    db: Session,
    outbox_id: int,
    *,
    ksm_client: Any | None = None,
    zhichi_client: Any | None = None,
    notice_store: Any | None = None,
    settings: Any | None = None,
) -> OutboxRetryResult:
    """立即重跑一条失败行。按 target_source_code 分派到对应 sender 的单行处理器。

    不重置 attempts/status 后甩给下一轮 beat——直接同步调用，处理人马上看到
    成败。失败时 last_error 已被 sender 内部的 _record_failure 更新（可能再次
    翻 failed，因为 attempts 早已 >= max_attempts），返回给前端最新错误文案。

    ksm_client/zhichi_client/notice_store/settings 均为测试注入点（镜像
    drain_ksm_outbox/drain_zhichi_outbox 的可选 client 参数）；生产路径不传，
    走真实 client + get_settings()。
    """
    row = db.get(SyncOutbox, outbox_id)
    if row is None:
        raise OutboxRetryError(f"outbox 行 {outbox_id} 不存在")
    if row.status != "failed":
        raise OutboxRetryError(f"该行当前状态是 {row.status}，非 failed，无需重试")

    if row.target_source_code == "ksm":
        result = _retry_ksm(db, row, client=ksm_client, notice_store=notice_store, settings=settings)
    elif row.target_source_code == "zhichi":
        result = _retry_zhichi(db, row, client=zhichi_client, settings=settings)
    else:
        raise OutboxRetryError(f"暂不支持来源 {row.target_source_code} 的手工重试")

    logger.info(
        "outbox_manual_retry",
        outbox_id=outbox_id,
        target_source_code=row.target_source_code,
        kind=row.kind,
        sent=result.sent,
    )
    return result


def _retry_ksm(
    db: Session,
    row: SyncOutbox,
    *,
    client: Any | None,
    notice_store: Any | None,
    settings: Any | None,
) -> OutboxRetryResult:
    from app.services.ksm.writeback import KSMWritebackSender, retry_single_row

    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    if not settings.ksm_writeback_enabled:
        raise OutboxRetryError("KSM 回写总开关未开启（ksm_writeback_enabled=false）")
    if not settings.ksm_handler_name or not settings.ksm_handler_number:
        raise OutboxRetryError("KSM 处理人身份未配置（KSM_HANDLER_NAME/NUMBER）")

    owns_client = client is None
    if client is None:
        from adapters.ksm import KSMClient, KSMConfig

        client = KSMClient(KSMConfig.from_settings(settings))
    try:
        if notice_store is None:
            from app.services.ksm.notice_store import NoticeStore

            try:
                notice_store = NoticeStore(redis_url=settings.redis_url)
            except Exception:
                logger.warning("outbox_retry_ksm_no_notice_store", outbox_id=row.id)
                notice_store = None
        sender = KSMWritebackSender(db, client=client, settings=settings, notice_store=notice_store)
        return retry_single_row(sender, row)
    finally:
        if owns_client:
            client.close()


def _retry_zhichi(
    db: Session,
    row: SyncOutbox,
    *,
    client: Any | None,
    settings: Any | None,
) -> OutboxRetryResult:
    from app.services.zhichi.writeback import ZhichiWritebackSender, retry_single_row

    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    if not settings.zhichi_writeback_enabled:
        raise OutboxRetryError("智齿回写总开关未开启（zhichi_writeback_enabled=false）")
    if not settings.zhichi_appid or not settings.zhichi_app_key:
        raise OutboxRetryError("智齿凭证未配置")

    owns_client = client is None
    if client is None:
        from adapters.zhichi import ZhichiClient, ZhichiConfig

        client = ZhichiClient(ZhichiConfig.from_settings(settings))
    try:
        sender = ZhichiWritebackSender(db, client=client, settings=settings)
        return retry_single_row(sender, row)
    finally:
        if owns_client:
            client.close()
