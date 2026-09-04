"""KSM 入库即接管受理（抢占式锁定）。

工单入库拉详情后主动把工单 lock 到我们配置的处理人名下，防止 KSM 侧被他人
操作。与 outbox 反向回写（答复/关单阶段）互补——这是入库瞬间的第一阶段。

受理 = 接管(lockKsmOrder) → 重拉详情 → 处理(handleKsmOrder)。严格顺序，
lock 后 node.id 会流转，必须重拉拿新 node 才能 handle（否则报"已流转至其他
节点"）。

是否 handle 取决于「我们系统里是不是已有这条工单」，不单看 KSM status：
  * 新工单（首次入库）        → 完整 lock → 重拉 → handle（进处理中）
  * 已存在工单（重复推/退回后再进来）→ 只 lock 接管，不 handle（之前已受理过）

改版（入库即分派改造）：接管不再在入库瞬间发生，改为人工审核确认分类（含模块
归类）之后由 `trigger_ksm_takeover_after_review` 触发——分类/模块判断错误时
接管还没发生，可以回退。接管身份也不再固定用全局配置，改用 ticket 的处理人
（见 identity.py），全局配置降级为处理人未配 ksm_account 时的兜底。

安全：
  * 灰度门 ksm_auto_takeover_enabled（默认关）+ 复用 ksm_writeback_dry_run。
  * 写操作（lock/handle）绝不自动重试（超时可能已成功，盲目重试会重复接管/处理）。
  * 业务成功判定由 client._post_business 负责（status=false 抛 KSMBusinessError 透传 message）。
  * 失败只记 ticket.ksm_takeover_status='failed' + error，不回滚 KSM（无法回滚）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from adapters.ksm import HandleOrderRequest, KSMClient, KSMConfig, KSMError, LockOrderRequest
from app.config import Settings, get_settings
from app.core.logging import get_logger
from app.db import make_session
from app.models import Ticket
from app.services.ksm.identity import KsmIdentity, resolve_ksm_identity
from app.services.ksm.notice_store import NoticeStore, NoticeStoreLike
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

    identity = resolve_ksm_identity(db, ticket, settings)
    if identity is None:
        logger.warning("ksm_takeover_no_identity", bill_id=bill_id, ticket_id=ticket.id)
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
            identity_source=identity.source,
        )
        return

    # ---- 1. 接管（lockKsmOrder），写操作不重试 ----
    try:
        _lock(client, fields, identity)
    except KSMError as e:
        ticket.ksm_takeover_status = "failed"
        ticket.ksm_takeover_error = str(e)[:1000]
        logger.warning("ksm_takeover_lock_failed", bill_id=fields.bill_id, error=str(e))
        return
    ticket.ksm_takeover_status = "locked"
    logger.info("ksm_takeover_locked", bill_id=fields.bill_id, identity_source=identity.source)

    # 已存在工单：只接管，不处理（之前已受理过）
    if not is_new:
        logger.info("ksm_takeover_lock_only", bill_id=fields.bill_id, reason="existing_ticket")
        return

    # ---- 2. 重拉详情刷新 node（lock 后节点已流转）----
    fresh = _refresh(client, fields, notice_store)

    # ---- 3. 处理（handleKsmOrder, is_deal=False → 只受理不关单）----
    try:
        _handle(client, fresh, identity)
    except KSMError as e:
        if not _handle_with_compensation(db, client, fresh, fields, identity, e, notice_store):
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
    identity: KsmIdentity,
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
            _handle(client, refreshed, identity)
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
            _lock(client, base, identity)
            refreshed = _refresh(client, base, notice_store)
            _handle(client, refreshed, identity)
            return True
        except KSMError as e3:
            logger.warning(
                "ksm_takeover_compensate_relock_failed", bill_id=base.bill_id, error=str(e3)
            )
            return False
    # c) 其他错误不可补偿
    return False


def _lock(client: KSMClient, fields: _KSMFields, identity: KsmIdentity) -> None:
    try:
        client.lock_order(
            LockOrderRequest(
                account=identity.account,
                account_name=identity.account_name,
                account_number=identity.account_number,
                bill_id=fields.bill_id,
                deal_opinion=_LOCK_OPINION,
            )
        )
    except KSMError as e:
        if any(h in str(e) for h in _ALREADY_LOCKED_HINTS):
            logger.info("ksm_takeover_already_locked_ok", bill_id=fields.bill_id)
            return
        raise


def _handle(client: KSMClient, fields: _KSMFields, identity: KsmIdentity) -> None:
    client.handle_order(
        HandleOrderRequest(
            account=identity.account,
            account_name=identity.account_name,
            account_number=identity.account_number,
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


def trigger_ksm_takeover_after_review(ticket_id: int) -> None:
    """人工审核确认分类（含模块归类）之后触发接管。独立 session/client，供
    supervisor.py 的 confirm_classification/reclassify 通过 background_tasks 调用
    ——网络 IO 不阻塞审核请求的响应。

    detail 来源：优先重新调 get_order_detail（需要 NoticeStore 里的 notice 仍未
    过期，24h TTL）；notice 已过期（审核发生在入库超过 24h 之后）时退化为直接用
    入库时存的 `_subscribe_callback` 快照——takeover 内部 `_refresh` 会在 lock 后
    再拉一次，只要那时 notice 仍有效就能收敛到最新状态；若彻底失效，让 handle
    报错落 ksm_takeover_status='failed'，不静默。

    接管失败不影响审核确认本身（调用方已提交事务）；本函数内部吞异常自行
    commit/rollback。非 KSM 来源直接跳过。
    """
    settings = get_settings()
    if not settings.ksm_auto_takeover_enabled:
        return

    db = make_session()
    try:
        ticket = db.get(Ticket, ticket_id)
        if ticket is None or ticket.source_code != "ksm":
            return
        if ticket.ksm_takeover_status in _TAKEN_OVER_STATUSES:
            return

        bill_id = ticket.source_ticket_id or ""
        notice_store = NoticeStore(redis_url=settings.redis_url)
        notice = notice_store.get(bill_id)

        client = KSMClient(KSMConfig.from_settings(settings))
        try:
            detail: dict[str, Any] | None = None
            if notice is not None:
                try:
                    detail = client.get_order_detail(
                        bill_id=bill_id,
                        notice_num=notice.notice_num,
                        subscribe_num=notice.subscribe_num,
                    )
                except KSMError as e:
                    logger.warning(
                        "ksm_takeover_review_refetch_failed", bill_id=bill_id, error=str(e)
                    )
            if detail is None:
                # notice 过期或重拉失败 → 退化用入库快照，takeover 内部 _refresh 会再试一次。
                snapshot = (ticket.source_payload or {}).get("_subscribe_callback")
                if not isinstance(snapshot, dict):
                    logger.warning("ksm_takeover_review_no_detail", bill_id=bill_id)
                    return
                detail = snapshot

            takeover_ksm_ticket(
                db,
                ticket,
                detail=detail,
                is_new=ticket.ksm_takeover_status is None,
                client=client,
                notice_store=notice_store,
                settings=settings,
            )
            db.commit()
        finally:
            client.close()
    except Exception:
        db.rollback()
        logger.exception("ksm_takeover_review_unexpected_failure", ticket_id=ticket_id)
    finally:
        db.close()
