"""KSM 入库即接管受理（抢占式锁定）。

工单入库拉详情后主动把工单 lock 到我们配置的处理人名下，防止 KSM 侧被他人
操作。与 outbox 反向回写（答复/关单阶段）互补——这是入库瞬间的第一阶段。

受理 = 接管(lockKsmOrder) → 重拉详情 → 处理(handleKsmOrder)。严格顺序，
lock 后 node.id 会流转，必须重拉拿新 node 才能 handle（否则报"已流转至其他
节点"）。

是否 handle 取决于「我们系统里是不是已有这条工单」，不单看 KSM status：
  * 新工单（首次入库）        → 完整 lock → 重拉 → handle（进处理中）
  * 已存在工单（重复推/退回后再进来）→ 只 lock 接管，不 handle（之前已受理过）

方案 X（用户确认）：takeover 在入库直后、工单毕业成 hub 之前运行，此刻无
op_status 可置。只管 KSM 锁定 + ticket.ksm_takeover_status，不碰 hub——
op_status=processing 交后续 Operation 毕业自然设置；处理人 Router 入库时已分配。

安全：
  * 灰度门 ksm_auto_takeover_enabled（默认关）+ 复用 ksm_writeback_dry_run。
  * 写操作（lock/handle）绝不自动重试（超时可能已成功，盲目重试会重复接管/处理）。
  * 业务成功判定由 client._post_business 负责（status=false 抛 KSMBusinessError 透传 message）。
  * 失败只记 ticket.ksm_takeover_status='failed' + error，不回滚 KSM（无法回滚）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from adapters.ksm import HandleOrderRequest, KSMClient, KSMError, LockOrderRequest
from app.config import Settings, get_settings
from app.core.logging import get_logger
from app.models import Ticket
from app.services.ksm.notice_store import NoticeStoreLike
from app.services.ksm.writeback import (
    _ALREADY_LOCKED_HINTS,
    _extract_ksm_fields,
    _KSMFields,
    _merge_refreshed,
)

logger = get_logger(__name__)

_LOCK_OPINION = "已受理，工单人员分析处理中"
_HANDLE_OPINION = "工单人员分析处理中"

# handleKsmOrder 补偿分支的 message 关键字
_STALE_NODE_HINTS = ("已流转至其他节点",)
_NOT_LOCKED_HINTS = ("未锁定", "不能直接处理")

# 已接管、无需再受理的终态；locked/handled 都算已接管
_TAKEN_OVER_STATUSES = frozenset({"locked", "handled"})


def takeover_ksm_ticket(
    db: Session,
    ticket: Ticket,
    *,
    detail: dict[str, Any],
    is_new: bool,
    client: KSMClient,
    notice_store: NoticeStoreLike | None,
    settings: Settings | None = None,
) -> None:
    """接管（并按 is_new 决定是否处理）一条 KSM 工单。不 commit —— 由调用方 commit。

    detail: 入库时首次拉取的 subscribeCallback data 块（用于读 status + 首次字段）。
    is_new: True=新工单（首次入库）→ 完整受理；False=已存在工单 → 只接管不处理。
    """
    settings = settings or get_settings()
    bill_id = ticket.source_ticket_id or ""

    if not settings.ksm_auto_takeover_enabled:
        logger.info("ksm_takeover_disabled", bill_id=bill_id)
        return

    if ticket.ksm_takeover_status in _TAKEN_OVER_STATUSES:
        logger.info("ksm_takeover_already", bill_id=bill_id, status=ticket.ksm_takeover_status)
        return

    fields = _extract_ksm_fields(ticket.source_payload, fallback_bill_id=bill_id)
    if not fields.bill_id:
        logger.warning("ksm_takeover_no_billid", ticket_id=ticket.id)
        return

    status = str(detail.get("status") or "")

    if settings.ksm_writeback_dry_run:
        logger.info(
            "ksm_takeover_dry_run",
            bill_id=fields.bill_id,
            ksm_status=status,
            is_new=is_new,
            would="lock+handle" if is_new else "lock",
            node_id=fields.node_id,
            linkman=fields.linkman,
        )
        return

    # ---- 1. 接管（lockKsmOrder），写操作不重试 ----
    try:
        _lock(client, fields, settings)
    except KSMError as e:
        ticket.ksm_takeover_status = "failed"
        ticket.ksm_takeover_error = str(e)[:1000]
        logger.warning("ksm_takeover_lock_failed", bill_id=fields.bill_id, error=str(e))
        return
    ticket.ksm_takeover_status = "locked"
    logger.info("ksm_takeover_locked", bill_id=fields.bill_id)

    # 已存在工单：只接管，不处理（之前已受理过）
    if not is_new:
        logger.info("ksm_takeover_lock_only", bill_id=fields.bill_id, reason="existing_ticket")
        return

    # ---- 2. 重拉详情刷新 node（lock 后节点已流转）----
    fresh = _refresh(client, fields, notice_store)

    # ---- 3. 处理（handleKsmOrder, is_deal=False → 只受理不关单）----
    try:
        _handle(client, fresh, settings)
    except KSMError as e:
        if not _handle_with_compensation(db, client, fresh, fields, settings, e, notice_store):
            ticket.ksm_takeover_status = "failed"
            ticket.ksm_takeover_error = str(e)[:1000]
            logger.warning("ksm_takeover_handle_failed", bill_id=fields.bill_id, error=str(e))
            return

    # 迁移 0038 加的 ticket.ksm_accept_opercache_id/ksm_current_node_id 曾在这里持久化
    # 「受理节点 opercacheId + 当前节点」供退回 sender 用；2026-09 改判后退回目标改为
    # 「实时拉取 + 取最新节点的上一个节点」，不再需要这份快照，停止写入
    # （writeback._refresh_for_return 里的 return 分支不读它们；列留存，待后续迁移删）。
    ticket.ksm_takeover_status = "handled"
    ticket.ksm_takeover_error = None
    logger.info("ksm_takeover_handled", bill_id=fields.bill_id)


def _handle_with_compensation(
    db: Session,
    client: KSMClient,
    fresh: _KSMFields,
    base: _KSMFields,
    settings: Settings,
    err: KSMError,
    notice_store: NoticeStoreLike | None,
) -> bool:
    """handle 抛错后按 message 关键字补偿。返回 True=补偿后成功，False=不可补偿。"""
    msg = str(err)
    # a) 详情过期（node 已流转）→ 重拉后重试一次 handle（不重新 lock）
    if any(h in msg for h in _STALE_NODE_HINTS):
        logger.info("ksm_takeover_compensate_stale_node", bill_id=base.bill_id)
        refreshed = _refresh(client, base, notice_store)
        try:
            _handle(client, refreshed, settings)
            return True
        except KSMError as e2:
            logger.warning(
                "ksm_takeover_compensate_stale_failed", bill_id=base.bill_id, error=str(e2)
            )
            return False
    # b) 未锁定 → 补 lock → 重拉 → 再 handle 一次
    if any(h in msg for h in _NOT_LOCKED_HINTS):
        logger.info("ksm_takeover_compensate_relock", bill_id=base.bill_id)
        try:
            _lock(client, base, settings)
            refreshed = _refresh(client, base, notice_store)
            _handle(client, refreshed, settings)
            return True
        except KSMError as e3:
            logger.warning(
                "ksm_takeover_compensate_relock_failed", bill_id=base.bill_id, error=str(e3)
            )
            return False
    # c) 其他错误不可补偿
    return False


def _lock(client: KSMClient, fields: _KSMFields, settings: Settings) -> None:
    try:
        client.lock_order(
            LockOrderRequest(
                account=settings.ksm_handler_name,
                account_name=settings.ksm_handler_name,
                account_number=settings.ksm_handler_number,
                bill_id=fields.bill_id,
                deal_opinion=_LOCK_OPINION,
            )
        )
    except KSMError as e:
        if any(h in str(e) for h in _ALREADY_LOCKED_HINTS):
            logger.info("ksm_takeover_already_locked_ok", bill_id=fields.bill_id)
            return
        raise


def _handle(client: KSMClient, fields: _KSMFields, settings: Settings) -> None:
    client.handle_order(
        HandleOrderRequest(
            account=settings.ksm_handler_name,
            account_name=settings.ksm_handler_name,
            account_number=settings.ksm_handler_number,
            bill_id=fields.bill_id,
            linkman=fields.linkman,
            customer_email=fields.email,
            customer_mobile=fields.mobile,
            product_id=fields.product_id,
            version_id=fields.version_id,
            module_id=fields.module_id,
            back_type=fields.back_type,
            node_id=fields.node_id,
            deal_opinion=_HANDLE_OPINION,
            is_deal=False,
        )
    )


def _refresh(
    client: KSMClient, fields: _KSMFields, notice_store: NoticeStoreLike | None
) -> _KSMFields:
    """重拉详情刷新 node/product 等 id（lock 后节点流转）。拉不到回落原字段，
    让 handle 报 KSM 错误暴露 —— 绝不静默成功。"""
    if notice_store is None:
        return fields
    notice = notice_store.get(fields.bill_id)
    if notice is None:
        logger.info("ksm_takeover_refresh_no_notice", bill_id=fields.bill_id)
        return fields
    try:
        detail = client.get_order_detail(
            bill_id=fields.bill_id,
            notice_num=notice.notice_num,
            subscribe_num=notice.subscribe_num,
        )
    except KSMError as e:
        logger.warning("ksm_takeover_refresh_failed", bill_id=fields.bill_id, error=str(e))
        return fields
    return _merge_refreshed(fields, detail)
