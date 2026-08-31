"""hub_issue creation from a classified ticket (D4).

A ticket graduates into a hub_issue once its type is known (LLM classify or
supervisor judgment). The hub_issue carries the 出口-type semantics
(Operation/Bug_fix/Demand/Internal_task) and is what downstream exits
consume (Linear push for Bug_fix/Demand, reply flow for Operation, ...).

Trigger model (mirrors split's 灰度 playbook):
    - auto path: after classify, when hub_issue_auto_enabled AND
      predicted_confidence >= hub_issue_auto_confidence
    - manual path: POST /api/supervisor/create-hub-issue (no confidence
      gate — supervisor judgment overrides), optional explicit type

Both call ensure_hub_issue_for_ticket(). Idempotent: a ticket already
linked to a hub_issue is never re-created (returns the existing link).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.logging import get_logger
from app.db import make_session
from app.models import HubIssue, Ticket, TicketHubIssueHistory
from app.repositories.status_history import StatusHistoryRepository
from app.services.cascade.reply_sync import backfill_reply_to_ticket
from app.services.hub_issues.hub_dedup import maybe_supersede_duplicate
from app.services.hub_issues.module_owner import resolve_module_owner
from app.services.hub_issues.op_status import OP_PROCESSING

logger = get_logger(__name__)

_VALID_TYPES = frozenset({"Operation", "Bug_fix", "Demand", "Internal_task"})


class HubIssueCreateError(Exception):
    """Ticket can't graduate to a hub_issue; message is operator-facing."""


@dataclass(slots=True, frozen=True)
class HubIssueResult:
    hub_issue_id: int
    hub_issue_short_code: str
    ticket_id: int
    type: str
    created: bool  # False when the ticket was already linked
    dispatch_missed: bool = False  # 研发类分派无匹配处理人（auto 路径据此转 pending）
    module_owner_resolved: bool = (
        False  # 研发类模块负责人确定（auto 路径据此决定是否自动推 Linear）
    )


def _next_hub_short_code(db: Session) -> str:
    n: int | None = db.execute(select(func.count(HubIssue.id))).scalar()
    return f"HUB-{(n or 0) + 1:06d}"


def ensure_hub_issue_for_ticket(
    ticket_id: int,
    *,
    created_by: str,
    type_override: str | None = None,
    product_line_code: str | None = None,
    module: str | None = None,
    db: Session,
) -> HubIssueResult:
    """Create a hub_issue from a ticket and link them. Commits on success.

    Type comes from `type_override` (supervisor) or ticket.predicted_type
    (auto path — caller enforces the confidence gate). Raises
    HubIssueCreateError when neither yields a valid type.
    """
    ticket = db.get(Ticket, ticket_id)
    if ticket is None or ticket.deleted_at is not None:
        raise HubIssueCreateError(f"ticket {ticket_id} not found")
    if ticket.hub_issue_id is not None:
        hub = db.get(HubIssue, ticket.hub_issue_id)
        return HubIssueResult(
            hub_issue_id=ticket.hub_issue_id,
            hub_issue_short_code=hub.short_code if hub else "",
            ticket_id=ticket.id,
            type=hub.type if hub else "",
            created=False,
        )
    if ticket.type == "Parent":
        # A split parent is a container; its children graduate individually.
        raise HubIssueCreateError(f"ticket {ticket_id} is a split Parent — graduate its children")

    issue_type = type_override or ticket.predicted_type
    # ADR-0016 P2a：投诉不毕业 hub_issue（停 ticket 层转人工）。type_override
    # 允许主管把投诉转成 Op/Bug/Demand 后毕业，故只在无 override 时挡。
    if type_override is None and issue_type == "Complaint":
        raise HubIssueCreateError(
            f"ticket {ticket_id} is Complaint — 投诉停 ticket 层转人工，不自动毕业 hub_issue"
        )
    if issue_type not in _VALID_TYPES:
        raise HubIssueCreateError(
            f"ticket {ticket_id} has no valid type (predicted={ticket.predicted_type!r}, "
            f"override={type_override!r})"
        )
    if not (ticket.title or "").strip():
        raise HubIssueCreateError(f"ticket {ticket_id} has no title")

    # 主管/处理人确认分类时可覆盖产品线/模块（否则继承 ticket 原值）。覆盖时同步回
    # ticket 并 upsert_catalog 自动建目录，保持 ticket/hub 一致。
    eff_plc = product_line_code if product_line_code is not None else ticket.product_line_code
    eff_module = module if module is not None else ticket.module
    if product_line_code is not None or module is not None:
        from app.services.ingest.catalog_upsert import upsert_catalog

        upsert_catalog(db, product_line_code=eff_plc, module=eff_module)
        ticket.product_line_code = eff_plc
        ticket.module = eff_module

    hub = HubIssue(
        short_code=_next_hub_short_code(db),
        type=issue_type,
        title=(ticket.title or "").strip(),
        canonical_body=ticket.body,
        product_line_code=eff_plc,
        module=eff_module,
        status="created",
        op_status=OP_PROCESSING if issue_type == "Operation" else None,
        op_handler="agent" if issue_type == "Operation" else None,
        op_status_changed_at=datetime.now(UTC) if issue_type == "Operation" else None,
        assigned_user_id=ticket.assigned_user_id,
        occurrence_count=1,
    )
    db.add(hub)
    db.flush()  # need hub.id for the link

    # ADR-0016 §2.1：自动毕业时 hub_dedup 查重（不只 Bug/Demand 推 Linear 前）。
    # 命中则当前 hub supersede 到原 hub，ticket 挂原 hub，占用复用不重复毕业。
    # 主管手动毕业（created_by=user:*）跳过查重——人已显式判断要单独建，
    # 不该被自动相似度判断静默合并、推翻人的决定。
    is_manual = created_by.startswith("user:")
    if not is_manual and get_settings().hub_dedup_enabled:
        dup_id = maybe_supersede_duplicate(db, hub)
        if dup_id is not None:
            ticket.hub_issue_id = dup_id
            db.add(
                TicketHubIssueHistory(
                    ticket_id=ticket.id,
                    hub_issue_id=dup_id,
                    change_reason=f"hub-dedup 合并到 #{dup_id}（{created_by}）",
                    human_confirmed=created_by.startswith("user:"),
                )
            )
            dup = db.get(HubIssue, dup_id)
            # #2 修复：合并到已答复 Operation hub 时，给新挂进来的 ticket 补发答复
            # （回填缓存 + 入 reply outbox）——否则 author_reply 早已跑完，第二个
            # 客户收不到回复。
            if dup is not None:
                backfill_reply_to_ticket(db, dup, ticket)
            db.commit()
            logger.info("hub_issue_dedup_merged", ticket_id=ticket.id, dup_hub_id=dup_id)
            return HubIssueResult(
                hub_issue_id=dup_id,
                hub_issue_short_code=dup.short_code if dup else "",
                ticket_id=ticket.id,
                type=dup.type if dup else issue_type,
                created=False,
            )

    ticket.hub_issue_id = hub.id
    db.flush()  # autoflush=False：_hub_source_code 的裸查询看不到未 flush 的挂载

    # 毕业分派：按多维规则选处理人（Operation 运营 + 研发类共用规则/人池）。
    # Operation → op_handler_user_id（op_handler 名保持 'agent' 不打断 drain）；
    # Bug_fix/Demand → 写 ticket.handler_user_id（处理人），**不覆盖** hub/ticket
    # 的 assigned_user_id（责任人保持入库值，人工可回退查看原始责任人归属）。
    # 放在 ticket 挂 hub + flush 之后：dispatch_handler 的 _hub_source_code 反查
    # 需要 ticket.hub_issue_id 已落库。研发类分派无结果 → dispatch_missed，
    # auto 路径据此转 pending 人工（见 create_hub_issue_for_ticket_auto）。
    dispatch_missed = False
    if issue_type in ("Operation", "Bug_fix", "Demand"):
        from app.services.dispatch import dispatch_handler
        from app.services.hub_issues.op_status import set_hub_tickets_handler

        dr = dispatch_handler(db, hub)
        if dr.user_id is not None:
            if issue_type == "Operation":
                hub.op_handler_user_id = dr.user_id
            # 研发类：HubIssue 无 handler_user_id 列，写 ticket 层处理人，
            # 不覆盖 hub.assigned_user_id（责任人保持入库时的值）。
            set_hub_tickets_handler(db, hub, dr.user_id)
        elif issue_type in ("Bug_fix", "Demand"):
            dispatch_missed = True

    db.add(
        TicketHubIssueHistory(
            ticket_id=ticket.id,
            hub_issue_id=hub.id,
            change_reason=f"created by {created_by}",
            human_confirmed=created_by.startswith("user:"),
        )
    )
    StatusHistoryRepository(db).record(
        entity_type="hub_issue",
        entity_id=hub.id,
        from_status=None,
        to_status="created",
        changed_by=created_by,
        reason=f"graduated from ticket {ticket.short_code}",
        metadata={"ticket_id": ticket.id, "type": issue_type},
    )
    db.commit()
    logger.info(
        "hub_issue_created",
        hub_issue_id=hub.id,
        hub_short_code=hub.short_code,
        ticket_id=ticket.id,
        type=issue_type,
        created_by=created_by,
    )
    return HubIssueResult(
        hub_issue_id=hub.id,
        hub_issue_short_code=hub.short_code,
        ticket_id=ticket.id,
        type=issue_type,
        created=True,
        dispatch_missed=dispatch_missed,
        module_owner_resolved=(
            resolve_module_owner(db, hub.product_line_code, hub.module) is not None
        ),
    )


def create_hub_issue_for_ticket_auto(ticket_id: int) -> HubIssueResult | None:
    """Auto-path convenience (post-ingest chain): own session, swallows
    errors, then chains the Linear push for Bug_fix/Demand. The caller has
    already verified the confidence gate."""
    from app.services.hub_issues.linear_push import push_hub_issue_to_linear

    db = make_session()
    try:
        result = ensure_hub_issue_for_ticket(ticket_id, created_by="agent:hub_issue_auto", db=db)
    except HubIssueCreateError as e:
        db.rollback()
        logger.warning("hub_issue_auto_skipped", ticket_id=ticket_id, error=str(e))
        return None
    except Exception:
        db.rollback()
        logger.exception("hub_issue_auto_unexpected_failure", ticket_id=ticket_id)
        return None
    finally:
        db.close()

    if not result.created:
        return result

    settings = get_settings()
    if settings.gate_classify_enabled:
        # 闸门①：全类型（Operation/Bug_fix/Demand/Internal_task）毕业后停
        # pending_review 待人工确认分类，不自动分流（不推 Linear、不进答复链）。
        # 必须优先于 dispatch_missed 判断：分派缺人的研发类工单同样要先过分类
        # 确认闸门，不能因分派缺人就跳过 gate① 直接进 pending 队列（TKT-005963）。
        _mark_pending_review(result.hub_issue_id)
        return result

    if result.type in ("Bug_fix", "Demand") and result.dispatch_missed:
        # 闸门①关时才生效：研发类分派无匹配处理人 → 转人工（复用 pending
        # 队列），不推 Linear。闸门①开时上面已停 pending_review 提前返回。
        _mark_dispatch_pending(result.hub_issue_id)
        return result

    # 闸门①关：分类自动确认。研发类推 Linear 前按「模块负责人是否确定」分流：
    # 责任人确定（resolve_module_owner 命中）→ 自动推 Linear；不确定 → 停
    # pending_linear_review 待处理人确认（工作台选人推送）。
    # Operation 自动答复链由 Celery drain 扫 op_status=processing/agent
    # 触发（不在此处调用）；Internal_task 无动作。
    if result.type in ("Bug_fix", "Demand"):
        if result.module_owner_resolved:
            push_hub_issue_to_linear(result.hub_issue_id)
        else:
            _mark_pending_linear_review(result.hub_issue_id)
    return result


def _mark_pending_review(hub_issue_id: int) -> None:
    """研发类自动毕业 → pending_review 待主管确认（不推 Linear）。自开 session。"""
    db = make_session()
    try:
        hub = db.get(HubIssue, hub_issue_id)
        if hub is None:
            return
        prev = hub.status
        hub.status = "pending_review"
        StatusHistoryRepository(db).record(
            entity_type="hub_issue",
            entity_id=hub.id,
            from_status=prev,
            to_status="pending_review",
            changed_by="agent:hub_issue_auto",
            reason="研发类待主管确认分类后推 Linear",
        )
        db.commit()
    finally:
        db.close()


def _mark_pending_linear_review(hub_issue_id: int) -> None:
    """闸门①关+闸门③开：研发类自动分类确认后仍停 pending_linear_review 待
    处理人确认推 Linear（镜像 confirm-classification/reclassify 的闸门③分流）。
    自开 session。"""
    db = make_session()
    try:
        hub = db.get(HubIssue, hub_issue_id)
        if hub is None:
            return
        prev = hub.status
        hub.status = "pending_linear_review"
        StatusHistoryRepository(db).record(
            entity_type="hub_issue",
            entity_id=hub.id,
            from_status=prev,
            to_status="pending_linear_review",
            changed_by="agent:hub_issue_auto",
            reason="闸门③：待处理人确认后推 Linear",
        )
        db.commit()
    finally:
        db.close()


def _mark_dispatch_pending(hub_issue_id: int) -> None:
    """研发类分派无匹配处理人 → status=pending 转人工（复用 Linear 待人工队列）。
    自开 session。主管补齐处理人后可重推 Linear。"""
    db = make_session()
    try:
        hub = db.get(HubIssue, hub_issue_id)
        if hub is None:
            return
        prev = hub.status
        hub.status = "pending"
        StatusHistoryRepository(db).record(
            entity_type="hub_issue",
            entity_id=hub.id,
            from_status=prev,
            to_status="pending",
            changed_by="agent:dispatch",
            reason="分派无匹配处理人，转人工补齐后重推 Linear",
        )
        db.commit()
    finally:
        db.close()
