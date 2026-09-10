"""Push a Bug_fix / Demand hub_issue to Linear (D4).

BackgroundTask body — never raises. The hub_issue stays linear_uuid=NULL on
any non-success so a later retry can push again (idempotent on linear_uuid).

Gates (all must hold, else skip with a log line):
    linear_push_enabled AND linear_api_key AND linear_team_id
    hub.type in (Bug_fix, Demand)        — ck_hub_issues_linear_fields
    hub.linear_uuid is NULL              — idempotency

Pending (待人工处理) write-back — instead of silently degrading:
    * assignee is an INDIVIDUAL (has email) but unknown to Linear
      (linear_user_id NULL — e.g. not in the workspace yet) → no push,
      hub.status='pending' + status_history with the reason
    * Linear API rejects/errors → hub.status='pending' + the error
    Group assignees (数电开票组 …, no email) keep the graceful fallback:
    default team, no assignee — that degradation is configured intent.
    A later successful push flips status back pending→created.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from adapters.linear import (
    CreateIssueRequest,
    LinearAuthError,
    LinearBusinessError,
    LinearClient,
    LinearConfig,
    LinearNetworkError,
)
from app.config import get_settings
from app.core.logging import get_logger
from app.db import make_session
from app.models import HubIssue, Ticket, User
from app.repositories.status_history import StatusHistoryRepository
from app.services.hub_issues.hub_dedup import maybe_supersede_duplicate
from app.services.hub_issues.module_owner import consume_module_owner
from app.services.hub_issues.webhook_push import (
    _SOURCE_ZH,
    _TICKET_TYPE_ZH,
    _customer_name,
    _feishu_url,
    _primary_source_ticket,
    _product_line_name,
    _reporter_field,
    push_hub_issue_to_webhook,
)

logger = get_logger(__name__)

# hub_issues.priority → Linear priority (0=None 1=Urgent 2=High 3=Medium 4=Low)
_PRIORITY_MAP = {"critical": 1, "high": 2, "medium": 3, "low": 4, "lowest": 4}


@dataclass(slots=True, frozen=True)
class LinearPushResult:
    hub_issue_id: int
    linear_uuid: str
    linear_identifier: str
    linear_url: str


def _mark_pending(db: Session, hub: HubIssue, *, reason: str) -> None:
    """Flip the hub_issue to 'pending' (待人工处理) with an audit trail.
    Commits — pending must survive even though the push itself failed."""
    prev = hub.status
    if prev == "pending":
        return  # already pending; don't spam history on every retry
    hub.status = "pending"
    StatusHistoryRepository(db).record(
        entity_type="hub_issue",
        entity_id=hub.id,
        from_status=prev,
        to_status="pending",
        changed_by="agent:linear_push",
        reason=reason,
    )
    db.commit()
    logger.warning("linear_push_pending", hub_issue_id=hub.id, reason=reason)


def _build_description(db: Session, hub: HubIssue) -> str:
    """构建 Linear Issue 描述正文（Markdown 结构化格式）。

    包含：
    1. 💡 指派说明（hub.reply_content）
    2. 📝 原始问题描述（hub.canonical_body）
    3. 📋 工单背景信息（短码、工单来源、客户名称、提单联系人、归属分类、系统链接）
    4. 关联源工单引用底注
    """
    sections: list[str] = []

    # 1. 💡 指派说明
    instructions = (hub.reply_content or "").strip()
    if instructions:
        sections.append(f"### 💡 指派说明\n{instructions}")

    # 2. 📝 原始问题描述
    orig_body = (hub.canonical_body or "").strip()
    if orig_body:
        sections.append(f"### 📝 原始问题描述\n{orig_body}")

    # 3. 📋 工单背景信息
    meta_lines: list[str] = ["### 📋 工单背景信息"]
    meta_lines.append(
        f"- **任务短码**: {hub.short_code} ({_TICKET_TYPE_ZH.get(hub.type, hub.type)})"
    )

    src = _primary_source_ticket(db, hub)
    if src:
        # 工单来源
        source_name = _SOURCE_ZH.get(src.source_code or "", src.source_code or "未知")
        ticket_no = src.source_ticket_number or src.source_ticket_id or src.short_code
        meta_lines.append(f"- **工单来源**: {source_name} ({ticket_no})")

        # 客户信息
        cust_name = _customer_name(db, src) or (src.reporter_company or "")
        if cust_name:
            meta_lines.append(f"- **客户名称**: {cust_name}")

        # 联系人信息
        rep_name = _reporter_field(src, "name")
        rep_mobile = _reporter_field(src, "mobile")
        rep_email = _reporter_field(src, "email")
        contact_parts = [p for p in [rep_name, rep_mobile, rep_email] if p]
        if contact_parts:
            meta_lines.append(f"- **提单联系人**: {' / '.join(contact_parts)}")

        # 工单处理人（支持/客服人员）
        handler_uid = src.handler_user_id or src.assigned_user_id
        if handler_uid:
            hu = db.get(User, handler_uid)
            if hu:
                hu_text = f"{hu.name} ({hu.email})" if hu.email else hu.name
                meta_lines.append(f"- **工单处理人**: {hu_text}")

    # 产研责任人（研发人员）
    owner_id = hub.owner_user_id or hub.assigned_user_id
    if owner_id:
        ou = db.get(User, owner_id)
        if ou:
            ou_text = f"{ou.name} ({ou.email})" if ou.email else ou.name
            meta_lines.append(f"- **产研责任人**: {ou_text}")

    # 归属分类
    pl_name = _product_line_name(db, hub.product_line_code)
    cat_parts = [p for p in [pl_name, hub.module] if p]
    if cat_parts:
        meta_lines.append(f"- **归属分类**: {' / '.join(cat_parts)}")

    # 系统链接
    if src:
        link = _feishu_url(src)
        if link:
            meta_lines.append(f"- **系统链接**: [点击在 Ticket-Hub 中查看详情]({link})")

    sections.append("\n".join(meta_lines))

    # 4. 底注引用
    sources = (
        db.query(Ticket)
        .filter(
            (Ticket.hub_issue_id == hub.id) | (Ticket.id == hub.ticket_id),
            Ticket.deleted_at.is_(None),
        )
        .order_by(Ticket.id)
        .all()
    )
    if sources:
        refs = ", ".join(f"{t.short_code} ({t.source_code or 'internal'})" for t in sources)
        sections.append(f"---\n*ticket-hub: {hub.short_code} · source tickets: {refs}*")

    return "\n\n".join(sections).strip()


def _push_via_webhook(
    db: Session, hub: HubIssue, *, assignee_override_user_id: int | None = None
) -> LinearPushResult | None:
    """转研发 webhook 分支。成功回写真实 Linear id/identifier（供展示/幂等/状态回同步）。"""
    if assignee_override_user_id is not None:
        owner = db.get(User, assignee_override_user_id)
        if owner is not None:
            hub.owner_user_id = owner.id
    else:
        owner = consume_module_owner(db, hub.product_line_code, hub.module)
        if owner is not None:
            hub.owner_user_id = owner.id

    if owner is None:
        _mark_pending(
            db,
            hub,
            reason="转研发 webhook：模块负责人未配置且未手选责任人，推送暂停待人工确认",
        )
        return None

    assignee_name = owner.name or ""
    try:
        push_result = push_hub_issue_to_webhook(db, hub, assignee_name=assignee_name)
    except (LinearAuthError, LinearBusinessError, LinearNetworkError) as e:
        logger.warning("linear_webhook_push_failed", hub_issue_id=hub.id, error=str(e))
        _mark_pending(db, hub, reason=f"转研发 webhook 推送失败：{e}")
        return None

    identifier = push_result.linear_identifier or f"WEBHOOK-{hub.short_code}"
    hub.linear_uuid = push_result.linear_uuid or None
    hub.linear_identifier = identifier
    hub.linear_status = "已转产研"
    hub.linear_status_synced_at = datetime.now(UTC)
    if hub.status in ("pending", "returned"):
        prev_status = hub.status
        hub.status = "processing"
        StatusHistoryRepository(db).record(
            entity_type="hub_issue",
            entity_id=hub.id,
            from_status=prev_status,
            to_status="processing",
            changed_by="agent:linear_webhook",
            reason=(
                "转研发 webhook 重推成功，pending 解除"
                if prev_status == "pending"
                else f"转研发 webhook 重新推送成功（{identifier}）"
            ),
        )
    db.commit()
    logger.info("linear_webhook_push_committed", hub_issue_id=hub.id, identifier=identifier)
    return LinearPushResult(
        hub_issue_id=hub.id,
        linear_uuid=push_result.linear_uuid,
        linear_identifier=identifier,
        linear_url=push_result.linear_url,
    )


def push_hub_issue_to_linear(
    hub_issue_id: int,
    db: Session | None = None,
    *,
    client: LinearClient | None = None,
    assignee_override_user_id: int | None = None,
) -> LinearPushResult | None:
    """Returns None when skipped or failed (logged); never raises."""
    settings = get_settings()
    own_session = db is None
    if own_session:
        db = make_session()
    assert db is not None

    try:
        # ---- 出口无关的共享前置检查（取 hub / 类型 / 幂等 / 去重）----
        hub = db.get(HubIssue, hub_issue_id)
        if hub is None or hub.deleted_at is not None:
            logger.warning("linear_push_hub_not_found", hub_issue_id=hub_issue_id)
            return None
        if hub.type not in ("Bug_fix", "Demand"):
            logger.info("linear_push_skip_type", hub_issue_id=hub_issue_id, type=hub.type)
            return None
        if (
            hub.linear_uuid is not None or hub.linear_identifier is not None
        ) and hub.status != "returned":
            logger.info(
                "linear_push_already_pushed",
                hub_issue_id=hub_issue_id,
                linear_identifier=hub.linear_identifier,
            )
            return None
        # creator 毕业时已 hub-dedup 合并 → 不重复查/推
        if hub.superseded_by_hub_issue_id is not None:
            logger.info("linear_push_skip_superseded", hub_issue_id=hub_issue_id)
            return None
        # hub 级语义去重：与已推的同产品线 hub 重复 → supersede，不重复建
        if settings.hub_dedup_enabled and hub.status != "returned":
            dup_id = maybe_supersede_duplicate(db, hub)
            if dup_id is not None:
                return None

        # ---- 出口分流 ----
        # 默认直连 Linear GraphQL（需 key+team+push_enabled）；开启时走飞书 webhook 分流。
        if settings.linear_webhook_enabled:
            return _push_via_webhook(db, hub, assignee_override_user_id=assignee_override_user_id)

        # ---- 直连 Linear 前置强校验：指派说明与责任人 ----
        solution = (hub.reply_content or "").strip()
        if not solution:
            logger.warning("linear_push_missing_solution", hub_issue_id=hub.id)
            _mark_pending(db, hub, reason="指派说明为空，推送暂停请先录入指派说明")
            return None

        # 责任人为任务处理人：优先使用显式指定 (override)，否则直接取 hub.assigned_user_id
        target_user_id = assignee_override_user_id or hub.assigned_user_id
        if target_user_id is None:
            logger.warning("linear_push_missing_assignee", hub_issue_id=hub.id)
            _mark_pending(db, hub, reason="任务处理人为空，推送暂停请先分配处理人")
            return None

        assignee_user = db.get(User, target_user_id)
        if assignee_user is None:
            logger.warning(
                "linear_push_assignee_not_found", hub_issue_id=hub.id, user_id=target_user_id
            )
            _mark_pending(
                db, hub, reason=f"任务处理人(ID={target_user_id})不存在，推送暂停请重新分配"
            )
            return None

        hub.owner_user_id = assignee_user.id

        if not (
            settings.linear_push_enabled and settings.linear_api_key and settings.linear_team_id
        ):
            logger.info("linear_push_disabled", hub_issue_id=hub_issue_id)
            return None

        # Per-assignee team routing: land the issue on the assignee's Linear
        # team (and set them as assignee). Group assignees (no email) fall
        # back to the default team; INDIVIDUALS unknown to Linear stop here
        # as 'pending' instead of silently losing their assignee.
        assignee_linear_id: str | None = None
        team_id = settings.linear_team_id
        if assignee_user.email and not assignee_user.linear_user_id:
            _mark_pending(
                db,
                hub,
                reason=(
                    f"处理人 {assignee_user.name}（{assignee_user.email}）在 Linear 工作区"
                    "查无此人，推送暂停待人工处理（加入 Linear 后执行"
                    " sync-from-linear 再重推）"
                ),
            )
            return None
        assignee_linear_id = assignee_user.linear_user_id
        if assignee_user.linear_team_id:
            team_id = assignee_user.linear_team_id

        req = CreateIssueRequest(
            title=f"[{hub.short_code}] {hub.title}",
            team_id=team_id,
            description=_build_description(db, hub),
            assignee_id=assignee_linear_id,
            priority=_PRIORITY_MAP.get(hub.priority or "", 0),
        )

        owns_client = client is None
        if client is None:
            client = LinearClient(LinearConfig.from_settings(settings))
        try:
            created = client.create_issue(req)
        except (LinearAuthError, LinearBusinessError, LinearNetworkError) as e:
            logger.warning("linear_push_failed", hub_issue_id=hub_issue_id, error=str(e))
            _mark_pending(db, hub, reason=f"Linear 推送失败：{e}")
            return None
        finally:
            if owns_client:
                client.close()

        hub.linear_uuid = created.id
        hub.linear_identifier = created.identifier
        hub.linear_status_synced_at = datetime.now(UTC)
        hub.linear_status = "待处理"
        prev_status = hub.status
        if prev_status in ("pending", "returned"):
            # pending 解除恢复 created；returned 重推恢复 processing
            hub.status = "processing" if prev_status == "returned" else "created"
            StatusHistoryRepository(db).record(
                entity_type="hub_issue",
                entity_id=hub.id,
                from_status=prev_status,
                to_status=hub.status,
                changed_by="agent:linear_push",
                reason=(
                    f"Linear 重新推送成功（{created.identifier}），恢复处理中"
                    if prev_status == "returned"
                    else f"Linear 重推成功（{created.identifier}），pending 解除"
                ),
            )
        db.commit()
        logger.info(
            "linear_push_ok",
            hub_issue_id=hub.id,
            linear_uuid=created.id,
            linear_identifier=created.identifier,
            url=created.url,
        )
        return LinearPushResult(
            hub_issue_id=hub.id,
            linear_uuid=created.id,
            linear_identifier=created.identifier,
            linear_url=created.url,
        )
    except Exception:  # defensive: BG task must not propagate
        if own_session:
            db.rollback()
        logger.exception("linear_push_unexpected_failure", hub_issue_id=hub_issue_id)
        return None
    finally:
        if own_session:
            db.close()
