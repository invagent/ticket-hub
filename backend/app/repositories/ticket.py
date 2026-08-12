"""ticket / hub_issue queries used by SLAWatcher, ingest, and read API."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Generic, TypeVar

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models import HubIssue, Ticket

T = TypeVar("T")


@dataclass(slots=True)
class Page(Generic[T]):
    items: list[T] = field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 50

    @property
    def has_more(self) -> bool:
        return self.page * self.page_size < self.total


class TicketRepository:
    """Read + write helpers. Soft-delete-aware on reads."""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ---- ingest helpers ------------------------------------------------

    def find_by_source(self, source_code: str, source_ticket_id: str) -> Ticket | None:
        """Idempotency lookup: a webhook may fire multiple times for the same bill."""
        stmt = select(Ticket).where(
            Ticket.source_code == source_code,
            Ticket.source_ticket_id == source_ticket_id,
            Ticket.deleted_at.is_(None),
        )
        return self._db.execute(stmt).scalar_one_or_none()

    def add(self, ticket: Ticket) -> Ticket:
        self._db.add(ticket)
        self._db.flush()
        return ticket

    def next_short_code(self, prefix: str = "TKT") -> str:
        """Generate the next short_code by counting current rows + 1.

        D1 fast path: simple counter; D2+ may switch to a sequence/redis counter
        if write contention becomes an issue.
        """
        n: int | None = self._db.execute(select(func.count(Ticket.id))).scalar()
        return f"{prefix}-{(n or 0) + 1:06d}"

    # ---- read API ------------------------------------------------------

    def get(self, ticket_id: int) -> Ticket | None:
        """Get a non-deleted ticket by id."""
        t = self._db.get(Ticket, ticket_id)
        if t is None or t.deleted_at is not None:
            return None
        return t

    def list_by_ids(self, ticket_ids: list[int]) -> list[Ticket]:
        """Fetch multiple tickets by id in one query. Soft-delete-aware."""
        if not ticket_ids:
            return []
        stmt = select(Ticket).where(
            Ticket.id.in_(ticket_ids),
            Ticket.deleted_at.is_(None),
        )
        return list(self._db.execute(stmt).scalars().all())

    def list_paginated(
        self,
        *,
        source_code: str | None = None,
        type_: str | None = None,
        status: str | None = None,
        assigned_user_id: int | None = None,
        handler_user_ids: list[int] | None = None,
        visible_to_user_id: int | None = None,
        predicted_types: list[str] | None = None,
        unassigned_only: bool = False,
        customer_identity_id: int | None = None,
        hub_issue_id: int | None = None,
        source_ticket_q: str | None = None,
        op_status: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Page[Ticket]:
        page = max(page, 1)
        page_size = max(min(page_size, 200), 1)

        base = select(Ticket).where(Ticket.deleted_at.is_(None))
        count_base = select(func.count(Ticket.id)).where(Ticket.deleted_at.is_(None))
        if source_code:
            base = base.where(Ticket.source_code == source_code)
            count_base = count_base.where(Ticket.source_code == source_code)
        if type_:
            base = base.where(Ticket.type == type_)
            count_base = count_base.where(Ticket.type == type_)
        if status:
            base = base.where(Ticket.status == status)
            count_base = count_base.where(Ticket.status == status)
        if assigned_user_id is not None:
            base = base.where(Ticket.assigned_user_id == assigned_user_id)
            count_base = count_base.where(Ticket.assigned_user_id == assigned_user_id)
        # 处理人多选筛选（handler_user_id）
        if handler_user_ids:
            base = base.where(Ticket.handler_user_id.in_(handler_user_ids))
            count_base = count_base.where(Ticket.handler_user_id.in_(handler_user_ids))
        # 行级可见性：非特权用户强制只见处理人=自己的工单
        if visible_to_user_id is not None:
            base = base.where(Ticket.handler_user_id == visible_to_user_id)
            count_base = count_base.where(Ticket.handler_user_id == visible_to_user_id)
        if predicted_types:
            base = base.where(Ticket.predicted_type.in_(predicted_types))
            count_base = count_base.where(Ticket.predicted_type.in_(predicted_types))
        if unassigned_only:
            base = base.where(Ticket.assigned_user_id.is_(None))
            count_base = count_base.where(Ticket.assigned_user_id.is_(None))
        if customer_identity_id is not None:
            base = base.where(Ticket.customer_identity_id == customer_identity_id)
            count_base = count_base.where(Ticket.customer_identity_id == customer_identity_id)
        if hub_issue_id is not None:
            base = base.where(Ticket.hub_issue_id == hub_issue_id)
            count_base = count_base.where(Ticket.hub_issue_id == hub_issue_id)
        if source_ticket_q:
            # 工单号子串匹配（支持输入后几位）：来源工单号 source_ticket_id OR
            # 本系统工单编号 short_code（如 TKT-005920）。ilike 大小写不敏感，转义 LIKE 元字符。
            esc = source_ticket_q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{esc}%"
            cond = or_(
                Ticket.source_ticket_id.ilike(pattern, escape="\\"),
                Ticket.short_code.ilike(pattern, escape="\\"),
            )
            base = base.where(cond)
            count_base = count_base.where(cond)
        if op_status:
            # 处理状态筛选（op_status 在所挂 hub_issue 上，仅 Operation 有值）。
            # 与列表展示口径一致：研发类（Bug_fix/Demand）无 op_status，其「处理状态」
            # 由 hub.status 派生——released=处理完成(answered)，其余已毕业=处理中(processing)。
            # 故筛 processing/answered 时把对应研发类 hub 一并纳入，保证筛选与展示一致。
            hub_cond = HubIssue.op_status == op_status
            dev_types = ("Bug_fix", "Demand")
            if op_status == "processing":
                hub_cond = or_(
                    hub_cond,
                    and_(HubIssue.type.in_(dev_types), HubIssue.status != "released"),
                )
            elif op_status == "answered":
                hub_cond = or_(
                    hub_cond,
                    and_(HubIssue.type.in_(dev_types), HubIssue.status == "released"),
                )
            hub_sub = select(HubIssue.id).where(hub_cond)
            base = base.where(Ticket.hub_issue_id.in_(hub_sub))
            count_base = count_base.where(Ticket.hub_issue_id.in_(hub_sub))

        total = self._db.execute(count_base).scalar() or 0
        rows_stmt = (
            base.order_by(Ticket.received_at.desc(), Ticket.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = list(self._db.execute(rows_stmt).scalars().all())
        return Page(items=items, total=total, page=page, page_size=page_size)

    def list_for_hub_issue(self, hub_issue_id: int) -> list[Ticket]:
        """All non-deleted tickets currently linked to a hub_issue."""
        stmt = (
            select(Ticket)
            .where(
                Ticket.hub_issue_id == hub_issue_id,
                Ticket.deleted_at.is_(None),
            )
            .order_by(Ticket.received_at.desc())
        )
        return list(self._db.execute(stmt).scalars().all())

    # ---- SLA scan ------------------------------------------------------

    def find_unreplied_overdue(
        self, *, threshold: timedelta, now: datetime | None = None
    ) -> list[Ticket]:
        """Tickets received before (now - threshold) without customer reply.

        Status whitelist: only the active ones (not done/superseded/rejected).
        """
        cutoff = (now or datetime.now(UTC)) - threshold
        active_statuses = (
            "received",
            "linked",
            "waiting_reply",
            "waiting_schedule",
            "scheduled",
            "in_progress",
            "code_merged",
            "released",
            "waiting_assign",
            "assigned",
        )
        stmt = (
            select(Ticket)
            .where(
                Ticket.deleted_at.is_(None),
                Ticket.received_at < cutoff,
                Ticket.customer_replied_at.is_(None),
                Ticket.status.in_(active_statuses),
            )
            .order_by(Ticket.received_at)
        )
        return list(self._db.execute(stmt).scalars().all())


# 运营态 op_status 档位（工单状态筛选）；研发态直接精确匹配实际 linear_status（数据驱动）。
# 二者取代旧的进行中/已完成二分 + DEV_STAGE_MATCH 中文档位映射。
OP_STATUS_VALUES = ["processing", "answered", "closed", "supplementing", "exception", "reviewing"]


class HubIssueRepository:
    """Read helpers for SLA scanning + read API."""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ---- read API ------------------------------------------------------

    def get(self, hub_issue_id: int) -> HubIssue | None:
        h = self._db.get(HubIssue, hub_issue_id)
        if h is None or h.deleted_at is not None:
            return None
        return h

    @staticmethod
    def _hub_filter_clauses(
        *,
        type_: str | None = None,
        status: str | None = None,
        assigned_user_id: int | None = None,
        product: str | None = None,
        module: str | None = None,
        search: str | None = None,
        op_status: str | None = None,
        dev_stage: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        closed_from: datetime | None = None,
        closed_to: datetime | None = None,
    ) -> list[Any]:
        """构造 hub_issue 列表/计数共用的 where 条件（deleted_at 除外）。

        op_status → 运营处理状态（仅 Operation 有值）；dev_stage → 精确匹配实际 linear_status；
        时间 from/to 已是 datetime 边界（调用方把 date 转成 [from, to) 半开区间）。
        """
        clauses: list[Any] = []
        if type_:
            clauses.append(HubIssue.type == type_)
        if status:
            clauses.append(HubIssue.status == status)
        if assigned_user_id is not None:
            clauses.append(HubIssue.assigned_user_id == assigned_user_id)
        if product:
            # 产品分类筛选匹配 product_line_code（product 字段创建时未赋值，恒为空——
            # 真实产品信息在 product_line_code，继承自 ticket）。兼容历史 product 有值的行。
            clauses.append(or_(HubIssue.product_line_code == product, HubIssue.product == product))
        if module:
            clauses.append(HubIssue.module == module)
        if search:
            like = f"%{search}%"
            clauses.append(or_(HubIssue.short_code.ilike(like), HubIssue.title.ilike(like)))
        if op_status:
            clauses.append(HubIssue.op_status == op_status)
        if dev_stage:
            clauses.append(HubIssue.linear_status == dev_stage)
        if created_from is not None:
            clauses.append(HubIssue.first_seen_at >= created_from)
        if created_to is not None:
            clauses.append(HubIssue.first_seen_at < created_to)
        if closed_from is not None:
            clauses.append(HubIssue.closed_at >= closed_from)
        if closed_to is not None:
            clauses.append(HubIssue.closed_at < closed_to)
        return clauses

    def list_paginated(
        self,
        *,
        type_: str | None = None,
        status: str | None = None,
        assigned_user_id: int | None = None,
        product: str | None = None,
        module: str | None = None,
        search: str | None = None,
        op_status: str | None = None,
        dev_stage: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        closed_from: datetime | None = None,
        closed_to: datetime | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Page[HubIssue]:
        page = max(page, 1)
        page_size = max(min(page_size, 200), 1)

        clauses = self._hub_filter_clauses(
            type_=type_,
            status=status,
            assigned_user_id=assigned_user_id,
            product=product,
            module=module,
            search=search,
            op_status=op_status,
            dev_stage=dev_stage,
            created_from=created_from,
            created_to=created_to,
            closed_from=closed_from,
            closed_to=closed_to,
        )
        base = select(HubIssue).where(HubIssue.deleted_at.is_(None), *clauses)
        count_base = select(func.count(HubIssue.id)).where(HubIssue.deleted_at.is_(None), *clauses)

        total = self._db.execute(count_base).scalar() or 0
        rows_stmt = (
            base.order_by(HubIssue.last_seen_at.desc(), HubIssue.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = list(self._db.execute(rows_stmt).scalars().all())
        return Page(items=items, total=total, page=page, page_size=page_size)

    def distinct_product_lines(self) -> list[str]:
        """数据里实际存在的 product_line_code 列表（非空，按数量降序）——供产品分类筛选下拉。"""
        stmt = (
            select(HubIssue.product_line_code, func.count(HubIssue.id).label("n"))
            .where(HubIssue.deleted_at.is_(None), HubIssue.product_line_code.is_not(None))
            .group_by(HubIssue.product_line_code)
            .order_by(func.count(HubIssue.id).desc())
        )
        return [r[0] for r in self._db.execute(stmt).all() if r[0]]

    def distinct_linear_statuses(self) -> list[str]:
        """数据里实际存在的 linear_status 列表（非空，按数量降序）——供研发状态筛选下拉。

        存的是 Linear 列显示名（团队自定义）；工单推 Linear 后才有值。
        """
        stmt = (
            select(HubIssue.linear_status, func.count(HubIssue.id).label("n"))
            .where(HubIssue.deleted_at.is_(None), HubIssue.linear_status.is_not(None))
            .group_by(HubIssue.linear_status)
            .order_by(func.count(HubIssue.id).desc())
        )
        return [r[0] for r in self._db.execute(stmt).all() if r[0]]

    def _hub_count(self, clauses: list[Any]) -> int:
        stmt = select(func.count(HubIssue.id)).where(HubIssue.deleted_at.is_(None), *clauses)
        return self._db.execute(stmt).scalar() or 0

    def filter_counts(
        self,
        *,
        type_: str | None = None,
        status: str | None = None,
        assigned_user_id: int | None = None,
        product: str | None = None,
        module: str | None = None,
        search: str | None = None,
        op_status: str | None = None,
        dev_stage: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        closed_from: datetime | None = None,
        closed_to: datetime | None = None,
    ) -> dict[str, dict[str, int]]:
        """各筛选维度的全量分档计数（跨页真实值）。

        每个维度排除其自身选择再计数——切换该维度档位时其它档计数保持稳定。
        op_status：运营处理状态 6 档；dev_stage：数据里实际的 linear_status 各值。
        """
        # 工单状态(op_status)：排除 op_status 自身，各档 + all
        base_no_op = self._hub_filter_clauses(
            type_=type_,
            status=status,
            assigned_user_id=assigned_user_id,
            product=product,
            module=module,
            search=search,
            dev_stage=dev_stage,
            created_from=created_from,
            created_to=created_to,
            closed_from=closed_from,
            closed_to=closed_to,
        )
        op_counts: dict[str, int] = {"all": self._hub_count(base_no_op)}
        for key in OP_STATUS_VALUES:
            op_counts[key] = self._hub_count([*base_no_op, HubIssue.op_status == key])

        # 研发状态(dev_stage)：排除 dev_stage 自身，按实际 linear_status 各值分组计数
        base_no_dev = self._hub_filter_clauses(
            type_=type_,
            status=status,
            assigned_user_id=assigned_user_id,
            product=product,
            module=module,
            search=search,
            op_status=op_status,
            created_from=created_from,
            created_to=created_to,
            closed_from=closed_from,
            closed_to=closed_to,
        )
        dev_stmt = (
            select(HubIssue.linear_status, func.count(HubIssue.id))
            .where(HubIssue.deleted_at.is_(None), HubIssue.linear_status.is_not(None), *base_no_dev)
            .group_by(HubIssue.linear_status)
        )
        dev_counts: dict[str, int] = {r[0]: r[1] for r in self._db.execute(dev_stmt).all() if r[0]}

        return {"op_status": op_counts, "dev_stage": dev_counts}

    def find_overdue_by_type(
        self,
        *,
        type_thresholds: dict[str, timedelta],
        now: datetime | None = None,
    ) -> list[HubIssue]:
        """Per-type SLA scan.

        `type_thresholds`: {'Operation': 4h, 'Bug_fix': 8h, ...}; tickets older
        than their type's threshold AND still in an open status are returned.
        """
        if not type_thresholds:
            return []
        ts_now = now or datetime.now(UTC)
        active_open = (
            "created",
            "waiting_reply",
            "waiting_schedule",
            "in_progress",
            "scheduled",
            "waiting_assign",
            "assigned",
            # 待主管确认才推 Linear(require_review_before_linear 默认开下的主路径)
            # 和 Linear 推送失败待人工的 pending —— 都需 SLA 监控,否则静默滞留。
            "pending_review",
            "pending",
        )
        clauses = []
        for type_name, threshold in type_thresholds.items():
            cutoff = ts_now - threshold
            clauses.append((HubIssue.type == type_name) & (HubIssue.first_seen_at < cutoff))
        if not clauses:
            return []
        stmt = (
            select(HubIssue)
            .where(
                HubIssue.deleted_at.is_(None),
                HubIssue.actual_resolved_at.is_(None),
                HubIssue.status.in_(active_open),
                or_(*clauses),
            )
            .order_by(HubIssue.first_seen_at)
        )
        return list(self._db.execute(stmt).scalars().all())
