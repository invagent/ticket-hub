"""SQLAlchemy ORM models — D0 + D1 + D2.

D0 (commit 59e40fc): sources / product_lines / users — already present.
D1 adds:
  identity / 分工 (5):  user_supervisors / user_partners /
                        assignment_scopes_module / assignment_scopes_feature /
                        assignment_scope_history
  customer (3):         customers / customer_identities / customer_merge_history
  ticket entities (2):  tickets / hub_issues
  weak relations (2):   hub_issue_relations / ticket_hub_issue_history
  history (2):          status_history / hub_issue_reply_history
  KSM mapping (1):      ksm_issue_type_mappings
  notification (1):     notification_log  (originally D2; pulled forward per D6 plan)
D2 adds:
  metrics cache (1):    materialized_metrics — Celery-refreshed dashboard snapshot
  catalog (2):          modules / features — admin-maintained dropdown source

PK strategy: INT autoincrement throughout (deviates from spec UUID; see ADR-0002).
Soft-deletion: customers / customer_identities / tickets / hub_issues / users only.
Status / type CHECKs: enforced as CHECK constraints (parity with spec).
Arrays / JSONB: stored as `JSON` (cross-compatible PG ↔ SQLite for tests).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# ---- D0 tables (unchanged) -------------------------------------------------


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProductLine(Base):
    __tablename__ = "product_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # D2-C: per-product-line SLA threshold overrides. NULL = use SLAWatcher
    # built-in defaults (4h ticket reply / 4-24h hub_issue by type).
    sla_reply_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sla_resolve_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("feishu_uid", name="uq_users_feishu_uid"),
        CheckConstraint("role IN ('assignee','supervisor','admin','member')", name="ck_users_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feishu_uid: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    employee_no: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    linear_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    mobile: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ksm_account: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    zhichi_agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(32), default="member", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---- D1: identity / 分工 ----------------------------------------------------


class UserSupervisor(Base):
    """user → supervisor 1:1 + optional deputy."""

    __tablename__ = "user_supervisors"
    __table_args__ = (CheckConstraint("user_id <> supervisor_id", name="ck_user_supervisors_self"),)

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)
    supervisor_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    deputy_supervisor_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UserPartner(Base):
    """Symmetric partner pairs. Two users in the same partnership are treated
    as a single routing unit (both can take work; redundant assignments dedup'd).
    """

    __tablename__ = "user_partners"
    __table_args__ = (CheckConstraint("user_id <> partner_id", name="ck_user_partners_self"),)

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)
    partner_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)


class AssignmentScopeModule(Base):
    """Routing primary key: (product_line, module) → user_id."""

    __tablename__ = "assignment_scopes_module"
    __table_args__ = (
        UniqueConstraint("product_line_code", "module", "user_id", name="uq_scope_module"),
        Index("ix_scope_module_lookup", "product_line_code", "module"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    product_line_code: Mapped[str] = mapped_column(
        String(64), ForeignKey("product_lines.code"), nullable=False
    )
    module: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AssignmentScopeFeature(Base):
    """Routing fallback: feature (cross product_line) → user_id."""

    __tablename__ = "assignment_scopes_feature"
    __table_args__ = (
        UniqueConstraint("feature", "user_id", name="uq_scope_feature"),
        Index("ix_scope_feature_lookup", "feature"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    feature: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AssignmentScopeHistory(Base):
    """Audit log for scope changes (add/remove)."""

    __tablename__ = "assignment_scope_history"
    __table_args__ = (
        CheckConstraint("scope_type IN ('module','feature')", name="ck_scope_history_type"),
        CheckConstraint("action IN ('add','remove')", name="ck_scope_history_action"),
        Index("ix_scope_history_user", "user_id", "changed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    changed_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---- D1: customers ---------------------------------------------------------


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        CheckConstraint(
            "merged_into_customer_id IS NULL OR merged_into_customer_id <> id",
            name="ck_customers_merge_self",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    primary_contact: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    merged_into_customer_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("customers.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CustomerIdentity(Base):
    __tablename__ = "customer_identities"
    __table_args__ = (
        CheckConstraint(
            "resolved_by_key IN ('erp_uid','mobile','email','source_custom_id','manual','none')",
            name="ck_customer_identity_resolved_by_key",
        ),
        UniqueConstraint("source_code", "source_user_id", name="uq_customer_identity_source_user"),
        Index("ix_customer_identity_customer", "customer_id"),
        Index("ix_customer_identity_erp_uid", "erp_uid"),
        Index("ix_customer_identity_email", "email"),
        Index("ix_customer_identity_mobile", "mobile"),
        Index("ix_customer_identity_source_custom", "source_custom_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), nullable=False)
    source_code: Mapped[str] = mapped_column(String(32), ForeignKey("sources.code"), nullable=False)
    source_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_custom_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    erp_uid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mobile: Mapped[str | None] = mapped_column(String(32), nullable=True)
    raw_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    resolved_by_key: Mapped[str] = mapped_column(String(32), default="none", nullable=False)
    human_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CustomerMergeHistory(Base):
    __tablename__ = "customer_merge_history"
    __table_args__ = (
        CheckConstraint("from_customer_id <> to_customer_id", name="ck_customer_merge_self"),
        Index("ix_customer_merge_from", "from_customer_id"),
        Index("ix_customer_merge_to", "to_customer_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_customer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("customers.id"), nullable=False
    )
    to_customer_id: Mapped[int] = mapped_column(Integer, ForeignKey("customers.id"), nullable=False)
    merge_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    merged_by: Mapped[str] = mapped_column(String(64), nullable=False)
    merged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---- D1: ticket entities ---------------------------------------------------


class Ticket(Base):
    """Single-source ticket; type column distinguishes Raw / Parent / Child.

    Decision (D0 review): single table, not three-table split.
    """

    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint("type IN ('Raw','Parent','Child')", name="ck_tickets_type"),
        CheckConstraint(
            "(type IN ('Raw','Parent') "
            " AND source_code IS NOT NULL "
            " AND source_ticket_id IS NOT NULL "
            " AND internal_split_id IS NULL) "
            "OR (type='Child' "
            " AND source_code IS NULL "
            " AND source_ticket_id IS NULL "
            " AND internal_split_id IS NOT NULL "
            " AND parent_ticket_id IS NOT NULL)",
            name="ck_tickets_type_fields",
        ),
        CheckConstraint(
            "parent_ticket_id IS NULL OR parent_ticket_id <> id",
            name="ck_tickets_parent_self",
        ),
        Index("ix_tickets_hub_issue", "hub_issue_id"),
        Index("ix_tickets_parent", "parent_ticket_id"),
        Index("ix_tickets_customer", "customer_identity_id"),
        Index("ix_tickets_status", "status"),
        Index("ix_tickets_type", "type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    short_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)

    # Source provenance
    source_code: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("sources.code"), nullable=True
    )
    source_ticket_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    internal_split_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    source_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Type / split structure
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_ticket_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("tickets.id"), nullable=True
    )
    children_ticket_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)

    # Customer / product
    customer_identity_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("customer_identities.id"), nullable=True
    )
    product_line_code: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("product_lines.code"), nullable=True
    )

    # Content
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    reporter: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Status (CHECK omitted for SQLite test-friendliness; status whitelist enforced
    # in ticket repository; full PG CHECK added in §future Alembic migration if needed)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_status: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Hub linkage (FK added later — circular ref hub_issues.id ↔ tickets.hub_issue_id)
    hub_issue_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Timeline mirror fields (kept consistent with hub_issues)
    expected_resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    actual_resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    actual_replied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expected_released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    actual_released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scheduled_iteration: Mapped[str | None] = mapped_column(String(64), nullable=True)
    linear_uuid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    linear_identifier: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Reply cache (cascade-from-hub_issue)
    cached_reply_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    cached_reply_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    customer_replied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Routing (D1: assigned user, optional)
    assigned_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, index=True
    )
    module: Mapped[str | None] = mapped_column(String(128), nullable=True)
    feature: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # D3-C: LLM classification (predicted hub_issue type + confidence)
    # CHECK constraint enforced at the DB level — see migration 0006.
    predicted_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    predicted_confidence: Mapped[Numeric | None] = mapped_column(Numeric(3, 2), nullable=True)
    classified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Misc
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    closed_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attachments_synced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HubIssue(Base):
    """Hub-internal subject ticket; type column distinguishes 4 出口 types."""

    __tablename__ = "hub_issues"
    __table_args__ = (
        CheckConstraint(
            "type IN ('Operation','Bug_fix','Demand','Internal_task')",
            name="ck_hub_issues_type",
        ),
        CheckConstraint(
            "priority IS NULL OR priority IN ('critical','high','medium','low','lowest')",
            name="ck_hub_issues_priority",
        ),
        CheckConstraint(
            "type='Operation' OR (reply_content IS NULL AND reply_authored_by IS NULL)",
            name="ck_hub_issues_operation_fields",
        ),
        CheckConstraint(
            "type IN ('Bug_fix','Demand') OR linear_uuid IS NULL",
            name="ck_hub_issues_linear_fields",
        ),
        CheckConstraint(
            "type='Internal_task' OR feishu_task_id IS NULL",
            name="ck_hub_issues_internal_task_fields",
        ),
        CheckConstraint(
            "superseded_by_hub_issue_id IS NULL OR superseded_by_hub_issue_id <> id",
            name="ck_hub_issues_supersede_self",
        ),
        Index("ix_hub_issues_type_status", "type", "status"),
        Index("ix_hub_issues_product_module", "product", "module"),
        Index("ix_hub_issues_linear_uuid", "linear_uuid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    short_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    canonical_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    product_line_code: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("product_lines.code"), nullable=True
    )
    product: Mapped[str | None] = mapped_column(String(128), nullable=True)
    module: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[str | None] = mapped_column(String(16), nullable=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Operation-only
    reply_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    reply_content_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reply_authored_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reply_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Bug_fix / Demand only
    linear_uuid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    linear_identifier: Mapped[str | None] = mapped_column(String(64), nullable=True)
    linear_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    linear_status_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scheduled_iteration: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expected_released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    actual_released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    customer_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Internal_task only
    feishu_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    feishu_task_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    feishu_task_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Routing
    assigned_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, index=True
    )

    # Timeline
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expected_resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    actual_resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Type-immutable supersede chain
    superseded_by_hub_issue_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("hub_issues.id"), nullable=True
    )
    supersede_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---- D1: weak relations & history ------------------------------------------


class HubIssueRelation(Base):
    """Weak relations among hub_issues (duplicate_of / related_to / partially_overlaps)."""

    __tablename__ = "hub_issue_relations"
    __table_args__ = (
        CheckConstraint(
            "relation IN ('duplicate_of','related_to','partially_overlaps')",
            name="ck_hub_issue_relations_relation",
        ),
        CheckConstraint("from_hub_issue_id <> to_hub_issue_id", name="ck_hub_issue_relations_self"),
        Index("ix_hub_issue_relations_from", "from_hub_issue_id"),
        Index("ix_hub_issue_relations_to", "to_hub_issue_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_hub_issue_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("hub_issues.id"), nullable=False
    )
    to_hub_issue_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("hub_issues.id"), nullable=False
    )
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    weight: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    human_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TicketHubIssueHistory(Base):
    """Audit of ticket → hub_issue association changes."""

    __tablename__ = "ticket_hub_issue_history"
    __table_args__ = (
        Index("ix_ticket_hub_issue_history_ticket", "ticket_id"),
        Index("ix_ticket_hub_issue_history_hub", "hub_issue_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(Integer, ForeignKey("tickets.id"), nullable=False)
    hub_issue_id: Mapped[int] = mapped_column(Integer, ForeignKey("hub_issues.id"), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    human_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class StatusHistory(Base):
    """Status transition audit, shared by ticket + hub_issue."""

    __tablename__ = "status_history"
    __table_args__ = (
        CheckConstraint("entity_type IN ('ticket','hub_issue')", name="ck_status_history_entity"),
        Index("ix_status_history_entity", "entity_type", "entity_id", "changed_at"),
        Index("ix_status_history_changed", "changed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    changed_by: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HubIssueReplyHistory(Base):
    """Operation reply versions."""

    __tablename__ = "hub_issue_reply_history"
    __table_args__ = (
        UniqueConstraint("hub_issue_id", "version", name="uq_reply_history_version"),
        Index("ix_reply_history_hub", "hub_issue_id", "version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hub_issue_id: Mapped[int] = mapped_column(Integer, ForeignKey("hub_issues.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    reply_content: Mapped[str] = mapped_column(Text, nullable=False)
    authored_by: Mapped[str] = mapped_column(String(64), nullable=False)
    authored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---- D1: KSM problem-type mapping (D0 review decision) ---------------------


class KSMIssueTypeMapping(Base):
    """KSM 问题分类 → 路由提示。Replaces xlsx migration (decision: cancel R2)."""

    __tablename__ = "ksm_issue_type_mappings"
    __table_args__ = (
        UniqueConstraint("ksm_category", "ksm_subcategory", name="uq_ksm_mapping_category"),
        CheckConstraint(
            "classification_hint IS NULL OR classification_hint IN "
            "('operation','bug_fix','demand','internal_task')",
            name="ck_ksm_mapping_hint",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ksm_category: Mapped[str] = mapped_column(String(128), nullable=False)
    ksm_subcategory: Mapped[str | None] = mapped_column(String(128), nullable=True)
    product_line_code: Mapped[str] = mapped_column(
        String(64), ForeignKey("product_lines.code"), nullable=False
    )
    target_module: Mapped[str] = mapped_column(String(128), nullable=False)
    target_feature: Mapped[str | None] = mapped_column(String(128), nullable=True)
    classification_hint: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---- D1: notification_log (D6 escalation; pulled forward) ------------------


class NotificationLog(Base):
    """SLA notification + escalation audit (decision D6)."""

    __tablename__ = "notification_log"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('feishu_bot','feishu_im','email')", name="ck_notification_channel"
        ),
        CheckConstraint(
            "notify_type IN ('sla_overdue','revert_spike','assignee_assigned','escalation')",
            name="ck_notification_type",
        ),
        CheckConstraint(
            "related_entity_type IS NULL OR related_entity_type IN "
            "('ticket','hub_issue','agent_decision')",
            name="ck_notification_entity_type",
        ),
        Index("ix_notification_recipient_pending", "recipient_user_id", "sent_at"),
        Index("ix_notification_entity", "related_entity_type", "related_entity_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recipient_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    notify_type: Mapped[str] = mapped_column(String(32), nullable=False)
    related_entity_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    related_entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    escalated_to_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---- D2 tables -------------------------------------------------------------


class MaterializedMetrics(Base):
    """Cached dashboard snapshot refreshed by Celery beat every 5 minutes.

    Only one "live" row is kept (slot_key='latest'); the materializer does
    an UPSERT so the table never grows unbounded.  Falls back to on-the-fly
    computation in `dashboard.py` when the table is empty (e.g. fresh DB).
    """

    __tablename__ = "materialized_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slot_key: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, default="latest"
    )
    refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class Module(Base):
    """D2-G: canonical module catalog. Bound to a product_line.

    Source-of-truth for the dropdown that backs assignment_scopes_module.module
    + tickets.module + hub_issues.module. Admin maintains via /admin/catalog UI.
    """

    __tablename__ = "modules"
    __table_args__ = (
        UniqueConstraint("product_line_code", "name", name="uq_modules_pl_name"),
        Index("ix_modules_pl_code", "product_line_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_line_code: Mapped[str] = mapped_column(
        String(64), ForeignKey("product_lines.code"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Feature(Base):
    """D2-G: canonical feature catalog. Cross-product (matches the
    assignment_scopes_feature semantics: feature 兜底 跨产品线)."""

    __tablename__ = "features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
