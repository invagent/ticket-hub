"""owner-split（ADR-0016 P4）— 1 hub_issue 按责任人分解为 N 个 Linear 子 issue.

v1 触发 = 主管手动（研发协同页「按责任人拆分」）：填 N 个子任务标题+责任人 →
每个建一个 Linear 子 issue（parentId 挂 hub 主 issue），落
`hub_issue_linear_issues` 跟踪行。LLM 预拆建议留 v2。

进度通知（每子 issue Done 即自动发，**永不等齐**——无「差一个小功能卡住」；
带 x/n 进度框架——不碎片化）：
    x < n → sync_outbox kind='progress_note'（KSM handleKsmOrder is_deal=False
            只回复不关单）
    x = n → kind='release_note'（答复关单）+ hub.release_notified_at 置位
            （与 devcollab.notify_release 互斥防二次关单，谁先谁算）
自动只入 outbox；真正对客发送受 ksm_writeback_enabled/dry_run 灰度阀保护。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
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
from app.models import HubIssue, HubIssueLinearIssue, SyncOutbox, Ticket, User
from app.repositories.status_history import StatusHistoryRepository

logger = get_logger(__name__)

_DEV_TYPES = ("Bug_fix", "Demand")


class OwnerSplitError(Exception):
    """Action rejected; message is operator-facing (maps to HTTP 409/503)."""


@dataclass(slots=True, frozen=True)
class SubTaskIn:
    title: str
    assignee_user_id: int | None = None


@dataclass(slots=True, frozen=True)
class SubIssueOut:
    id: int
    linear_uuid: str
    linear_identifier: str
    title: str
    assignee_user_id: int | None


@dataclass(slots=True, frozen=True)
class OwnerSplitResult:
    hub_issue_id: int
    sub_issues: list[SubIssueOut]


def execute_owner_split(
    db: Session,
    hub_issue_id: int,
    *,
    subtasks: list[SubTaskIn],
    executed_by: str,
    client: LinearClient | None = None,
) -> OwnerSplitResult:
    """建 N 个 Linear 子 issue + 跟踪行。Commits。

    中途失败：已建的子 issue 行保留（Linear 侧已存在，不假装没发生），
    抛错带上已建数量——「已拆过」守卫会挡住简单重试，主管去 Linear 手工补齐
    或先 revert（v1 无自动回滚，Linear 删 issue 是破坏性动作留人工）。
    """
    settings = get_settings()
    hub = db.get(HubIssue, hub_issue_id)
    if hub is None or hub.deleted_at is not None:
        raise OwnerSplitError(f"hub_issue {hub_issue_id} 不存在")
    if hub.type not in _DEV_TYPES:
        raise OwnerSplitError(f"{hub.short_code} 是 {hub.type} —— 按责任人拆分仅限研发类")
    if not hub.linear_uuid:
        raise OwnerSplitError(
            f"{hub.short_code} 尚未推送 Linear（子任务要挂主 issue），先推送/重推"
        )
    existing = (
        db.execute(select(HubIssueLinearIssue).where(HubIssueLinearIssue.hub_issue_id == hub.id))
        .scalars()
        .first()
    )
    if existing is not None:
        raise OwnerSplitError(
            f"{hub.short_code} 已拆分过（{existing.linear_identifier} 等）——v1 不支持追加/重拆"
        )
    cleaned = [
        SubTaskIn(title=(s.title or "").strip(), assignee_user_id=s.assignee_user_id)
        for s in subtasks
    ]
    if len(cleaned) < 2:
        raise OwnerSplitError("子任务至少 2 个（1 个没有拆的意义）")
    if any(not s.title for s in cleaned):
        raise OwnerSplitError("子任务标题不能为空")
    if not settings.linear_api_key:
        raise OwnerSplitError("Linear 未接通（LINEAR_API_KEY 未配置）")

    # 责任人 → Linear 身份（同 linear_push 剧本：个人查无此人是硬伤直接拒绝，
    # 这是同步的主管动作——报错让主管当场改，比 pending 更直接）
    resolved: list[tuple[SubTaskIn, str | None, str]] = []  # (task, assignee_linear_id, team_id)
    for s in cleaned:
        assignee_linear_id: str | None = None
        team_id = settings.linear_team_id
        if s.assignee_user_id is not None:
            u = db.get(User, s.assignee_user_id)
            if u is None:
                raise OwnerSplitError(f"责任人 user_id={s.assignee_user_id} 不存在")
            if u.email and not u.linear_user_id:
                raise OwnerSplitError(
                    f"责任人 {u.name}（{u.email}）在 Linear 工作区查无此人——"
                    "先加入 Linear 并执行 sync-from-linear"
                )
            assignee_linear_id = u.linear_user_id
            if u.linear_team_id:
                team_id = u.linear_team_id
        if not team_id:
            raise OwnerSplitError("无可用 Linear team（默认 LINEAR_TEAM_ID 未配置）")
        resolved.append((s, assignee_linear_id, team_id))

    owns_client = client is None
    if client is None:
        client = LinearClient(LinearConfig.from_settings(settings))
    created: list[SubIssueOut] = []
    try:
        for i, (s, assignee_linear_id, team_id) in enumerate(resolved, start=1):
            try:
                issue = client.create_issue(
                    CreateIssueRequest(
                        title=f"[{hub.short_code}·{i}/{len(resolved)}] {s.title}",
                        team_id=team_id,
                        description=(
                            f"owner-split 子任务 {i}/{len(resolved)} · "
                            f"ticket-hub {hub.short_code}「{hub.title}」"
                        ),
                        assignee_id=assignee_linear_id,
                        parent_id=hub.linear_uuid,
                    )
                )
            except (LinearAuthError, LinearBusinessError, LinearNetworkError) as e:
                db.commit()  # 已建行落库（Linear 侧已存在）
                raise OwnerSplitError(
                    f"第 {i}/{len(resolved)} 个子 issue 建失败：{e}"
                    f"（前 {len(created)} 个已建成，请到 Linear 手工补齐或联系管理员）"
                ) from e
            row = HubIssueLinearIssue(
                hub_issue_id=hub.id,
                linear_uuid=issue.id,
                linear_identifier=issue.identifier,
                title=s.title,
                assignee_user_id=s.assignee_user_id,
                created_by=executed_by,
            )
            db.add(row)
            db.flush()
            created.append(
                SubIssueOut(
                    id=row.id,
                    linear_uuid=issue.id,
                    linear_identifier=issue.identifier,
                    title=s.title,
                    assignee_user_id=s.assignee_user_id,
                )
            )
    finally:
        if owns_client:
            client.close()

    StatusHistoryRepository(db).record(
        entity_type="hub_issue",
        entity_id=hub.id,
        from_status=hub.status,
        to_status=hub.status,  # audit event — status unchanged
        changed_by=executed_by,
        reason=f"按责任人拆分为 {len(created)} 个 Linear 子任务："
        + "、".join(c.linear_identifier for c in created),
        metadata={"kind": "owner_split", "n": len(created)},
    )
    db.commit()
    logger.info(
        "owner_split_executed",
        hub_issue_id=hub.id,
        n=len(created),
        identifiers=[c.linear_identifier for c in created],
        by=executed_by,
    )
    return OwnerSplitResult(hub_issue_id=hub.id, sub_issues=created)


# ---- 进度通知（子 issue Done → x/n 文案入 outbox）----------------------------


def _progress_note(hub: HubIssue, sub: HubIssueLinearIssue, x: int, n: int) -> str:
    return (
        f"您好，您的需求「{hub.title}」包含 {n} 个子任务，"
        f"本次已完成上线第 {x} 个：「{sub.title}」，剩余 {n - x} 个正在处理中，"
        f"完成后会继续通知您。（{hub.short_code}）"
    )


def _final_note(hub: HubIssue, n: int) -> str:
    return (
        f"您好，您的需求「{hub.title}」包含的全部 {n} 个子任务已完成上线。"
        f"如使用中有任何问题，欢迎随时反馈。（{hub.short_code}）"
    )


def notify_sub_issue_done(db: Session, sub: HubIssueLinearIssue) -> int:
    """子 issue 完成 → 进度/发版通知入 outbox（每个有源关联工单一行）。

    不 commit（轮询调用方统一提交）。幂等：sub.notified_at 非空直接跳过。
    返回入队 outbox 行数。
    """
    if sub.notified_at is not None:
        return 0
    now = datetime.now(UTC)
    hub = db.get(HubIssue, sub.hub_issue_id)
    if hub is None or hub.deleted_at is not None:
        sub.notified_at = now  # 挂空 hub 的孤儿行不重试
        return 0

    siblings = (
        db.execute(select(HubIssueLinearIssue).where(HubIssueLinearIssue.hub_issue_id == hub.id))
        .scalars()
        .all()
    )
    n = len(siblings)
    x = sum(1 for s in siblings if s.released_at is not None)

    tickets = (
        db.query(Ticket).filter(Ticket.hub_issue_id == hub.id, Ticket.deleted_at.is_(None)).all()
    )
    sourced = [t for t in tickets if t.source_code and t.source_ticket_id]
    if not sourced:
        sub.notified_at = now  # 自查/无源客户渠道 → 无处可通知
        logger.info("sub_issue_notify_no_sourced", hub_issue_id=hub.id, sub_id=sub.id)
        return 0

    is_final = x >= n
    if is_final and hub.release_notified_at is not None:
        # 主管已手动发过发版通知（或另一条末子 issue 已发）→ 防二次关单
        sub.notified_at = now
        logger.info("sub_issue_notify_already_released", hub_issue_id=hub.id, sub_id=sub.id)
        return 0

    kind = "release_note" if is_final else "progress_note"
    note = _final_note(hub, n) if is_final else _progress_note(hub, sub, x, n)
    count = 0
    for t in sourced:
        db.add(
            SyncOutbox(
                kind=kind,
                target_source_code=t.source_code,
                ticket_id=t.id,
                source_ticket_id=t.source_ticket_id,
                hub_issue_id=hub.id,
                payload={
                    "note": note,
                    "hub_short_code": hub.short_code,
                    "sub_identifier": sub.linear_identifier,
                    "progress": {"x": x, "n": n},
                    "notified_by": "agent:owner_split_progress",
                },
            )
        )
        count += 1
    sub.notified_at = now
    if is_final:
        hub.release_notified_at = now
        hub.release_note = note
        hub.feedback_status = "pending"
    StatusHistoryRepository(db).record(
        entity_type="hub_issue",
        entity_id=hub.id,
        from_status=hub.status,
        to_status=hub.status,
        changed_by="agent:owner_split_progress",
        reason=f"子任务 {sub.linear_identifier}「{sub.title}」完成 → "
        f"{'发版通知（全部完成）' if is_final else f'进度通知 {x}/{n}'} → {count} 个客户渠道",
        metadata={"kind": kind, "x": x, "n": n},
    )
    logger.info(
        "sub_issue_progress_notified",
        hub_issue_id=hub.id,
        sub_id=sub.id,
        kind=kind,
        x=x,
        n=n,
        outbox=count,
    )
    return count
