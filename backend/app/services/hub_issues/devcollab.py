"""研发协同动作（2026-07 后台重构 批次5）— 催办 / 发版通知 / 自查登记 / 回访记录.

四个 supervisor 动作，围绕已推 Linear 的研发类（Bug_fix/Demand）hub 工单闭环：

  urge_hub_issue      向 Linear issue 发催办评论并计数（24h 频率限制）
  notify_release      发版通知：文案级联所有有源关联工单 → sync_outbox
                      (kind='release_note'，KSM sender 按 reply 剧本消费)，
                      并把回访状态置 pending
  register_self_bug   研发自查发现且已修复的 bug —— 无客户来源 standalone
                      Bug_fix hub 工单（self_found=true，不触发客户通知）
  record_feedback     记录客户回访结果（resolved / stillbad）

全部写 status_history 审计；失败抛 DevCollabError（operator-facing message）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.logging import get_logger
from app.models import HubIssue, SyncOutbox, Ticket
from app.repositories.status_history import StatusHistoryRepository
from app.services.hub_issues.creator import _next_hub_short_code

logger = get_logger(__name__)

_URGE_COOLDOWN_HOURS = 24
_DEV_TYPES = ("Bug_fix", "Demand")


class DevCollabError(Exception):
    """Action rejected; message is operator-facing (maps to HTTP 409/503)."""


def _dev_hub(db: Session, hub_issue_id: int) -> HubIssue:
    hub = db.get(HubIssue, hub_issue_id)
    if hub is None or hub.deleted_at is not None:
        raise DevCollabError(f"hub_issue {hub_issue_id} 不存在")
    if hub.type not in _DEV_TYPES:
        raise DevCollabError(
            f"{hub.short_code} 是 {hub.type} 类型 —— 该动作仅限研发类（Bug修复/需求）"
        )
    return hub


def _audit(db: Session, hub: HubIssue, *, by: str, reason: str, kind: str, **meta: object) -> None:
    StatusHistoryRepository(db).record(
        entity_type="hub_issue",
        entity_id=hub.id,
        from_status=hub.status,
        to_status=hub.status,  # audit event — status unchanged
        changed_by=by,
        reason=reason,
        metadata={"kind": kind, **meta},
    )


# ---- 催办 -------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class UrgeResult:
    hub_issue_id: int
    urge_count: int
    linear_identifier: str


def urge_hub_issue(db: Session, hub_issue_id: int, *, urged_by: str) -> UrgeResult:
    """向 Linear issue 发催办评论；24h 内已催过 → 拒绝（频率限制）。Commits."""
    hub = _dev_hub(db, hub_issue_id)
    if not hub.linear_uuid:
        raise DevCollabError(f"{hub.short_code} 尚未推送 Linear，无处催办")

    now = datetime.now(UTC)
    if hub.last_urged_at is not None:
        last = hub.last_urged_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if now - last < timedelta(hours=_URGE_COOLDOWN_HOURS):
            raise DevCollabError("24 小时内已催办过，稍后再试（防打扰频率限制）")

    settings = get_settings()
    if not settings.linear_api_key:
        raise DevCollabError("Linear 未接通（LINEAR_API_KEY 未配置），无法催办")

    from adapters.linear import LinearClient, LinearConfig

    body = (
        f"⏰ **客服催办**（第 {hub.urge_count + 1} 次）\n\n"
        f"hub 工单 {hub.short_code}「{hub.title}」等待处理，"
        f"客户侧持续关注，请评估进展或更新状态。\n\n"
        f"— ticket-hub · {urged_by}"
    )
    with LinearClient(LinearConfig.from_settings(settings)) as client:
        client.create_comment(hub.linear_uuid, body)

    hub.urge_count += 1
    hub.last_urged_at = now
    _audit(
        db,
        hub,
        by=urged_by,
        reason=f"催办 Linear {hub.linear_identifier}（第 {hub.urge_count} 次）",
        kind="urge",
    )
    db.commit()
    logger.info("hub_urged", hub_issue_id=hub.id, urge_count=hub.urge_count, by=urged_by)
    return UrgeResult(
        hub_issue_id=hub.id,
        urge_count=hub.urge_count,
        linear_identifier=hub.linear_identifier or "",
    )


# ---- 发版通知 ---------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class ReleaseNotifyResult:
    hub_issue_id: int
    outbox_ids: list[int] = field(default_factory=list)
    cascaded_ticket_ids: list[int] = field(default_factory=list)


_RELEASED_LINEAR_STATES = ("done", "completed", "released")


def notify_release(
    db: Session,
    hub_issue_id: int,
    *,
    fix_version: str,
    note: str,
    notified_by: str,
) -> ReleaseNotifyResult:
    """发版通知：记录文案 → 每个有源关联工单入 outbox(kind='release_note')
    → 回访状态置 pending。自查工单（无关联客户）拒绝。Commits."""
    note = (note or "").strip()
    if not note:
        raise DevCollabError("通知文案为空")
    hub = _dev_hub(db, hub_issue_id)
    if hub.release_notified_at is not None:
        raise DevCollabError(
            f"{hub.short_code} 已发过发版通知（{hub.fix_version or '版本未记录'}）"
        )
    linear_ok = (hub.linear_status or "").lower() in _RELEASED_LINEAR_STATES
    hub_ok = hub.status in ("released", "done")
    if not (linear_ok or hub_ok):
        raise DevCollabError(
            f"{hub.short_code} 尚未完成/发版（Linear: {hub.linear_status or '—'} / hub: {hub.status}），不能发通知"
        )

    tickets = (
        db.query(Ticket).filter(Ticket.hub_issue_id == hub.id, Ticket.deleted_at.is_(None)).all()
    )
    sourced = [t for t in tickets if t.source_code and t.source_ticket_id]
    if not sourced:
        raise DevCollabError(f"{hub.short_code} 无有源关联工单（自查类不发客户通知）")

    now = datetime.now(UTC)
    hub.release_notified_at = now
    hub.release_note = note
    hub.fix_version = fix_version.strip() or hub.fix_version
    hub.feedback_status = "pending"

    outbox_ids: list[int] = []
    cascaded: list[int] = []
    for t in sourced:
        row = SyncOutbox(
            kind="release_note",
            target_source_code=t.source_code,
            ticket_id=t.id,
            source_ticket_id=t.source_ticket_id,
            hub_issue_id=hub.id,
            payload={
                "note": note,
                "fix_version": hub.fix_version,
                "hub_short_code": hub.short_code,
                "notified_by": notified_by,
            },
        )
        db.add(row)
        db.flush()
        outbox_ids.append(row.id)
        cascaded.append(t.id)

    _audit(
        db,
        hub,
        by=notified_by,
        reason=f"发版通知（{hub.fix_version or '版本未记录'}）→ {len(sourced)} 个客户渠道",
        kind="release_notify",
        fix_version=hub.fix_version,
        channels=len(sourced),
    )
    db.commit()
    logger.info(
        "release_notified",
        hub_issue_id=hub.id,
        fix_version=hub.fix_version,
        outbox=len(outbox_ids),
        by=notified_by,
    )
    return ReleaseNotifyResult(
        hub_issue_id=hub.id, outbox_ids=outbox_ids, cascaded_ticket_ids=cascaded
    )


# ---- 自查登记 ---------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class SelfBugResult:
    hub_issue_id: int
    short_code: str


def register_self_bug(
    db: Session,
    *,
    title: str,
    product_line_code: str | None,
    module: str | None,
    impact_versions: str | None,
    fix_version: str | None,
    released: bool,
    registered_by: str,
) -> SelfBugResult:
    """研发自查发现并已修复的 bug —— standalone Bug_fix hub 工单
    （无客户来源，不推 Linear、不发客户通知）。Commits."""
    title = (title or "").strip()
    if not title:
        raise DevCollabError("标题为空")
    now = datetime.now(UTC)
    hub = HubIssue(
        short_code=_next_hub_short_code(db),
        type="Bug_fix",
        title=title,
        status="released" if released else "created",
        product_line_code=product_line_code,
        module=module,
        impact_versions=(impact_versions or "").strip() or None,
        fix_version=(fix_version or "").strip() or None,
        self_found=True,
        status_changed_at=now,
        actual_released_at=now if released else None,
    )
    db.add(hub)
    db.flush()
    _audit(
        db,
        hub,
        by=registered_by,
        reason=f"自查登记（{'已发版' if released else '未发版'}·修复 {hub.fix_version or '—'}）",
        kind="self_bug",
    )
    db.commit()
    logger.info("self_bug_registered", hub_issue_id=hub.id, short_code=hub.short_code)
    return SelfBugResult(hub_issue_id=hub.id, short_code=hub.short_code)


# ---- 回访记录 ---------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class FeedbackResult:
    hub_issue_id: int
    feedback_status: str


def record_feedback(
    db: Session,
    hub_issue_id: int,
    *,
    status: str,
    note: str,
    recorded_by: str,
) -> FeedbackResult:
    """记录发版后客户回访结果。resolved=确认解决 / stillbad=仍报错（需升级）。Commits."""
    if status not in ("resolved", "stillbad"):
        raise DevCollabError(f"无效回访状态 {status!r}（resolved|stillbad）")
    hub = _dev_hub(db, hub_issue_id)
    if hub.release_notified_at is None:
        raise DevCollabError(f"{hub.short_code} 尚未发过发版通知，无回访可记录")
    hub.feedback_status = status
    hub.feedback_note = (note or "").strip() or None
    hub.feedback_at = datetime.now(UTC)
    _audit(
        db,
        hub,
        by=recorded_by,
        reason=f"回访记录：{'客户确认解决' if status == 'resolved' else '客户仍报错'}",
        kind="release_feedback",
        feedback=status,
    )
    db.commit()
    logger.info("feedback_recorded", hub_issue_id=hub.id, status=status, by=recorded_by)
    return FeedbackResult(hub_issue_id=hub.id, feedback_status=status)
