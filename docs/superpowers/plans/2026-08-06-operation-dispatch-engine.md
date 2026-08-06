# Operation 运营处理人分派引擎 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Operation 工单毕业时按多维规则引擎预分配具体运营处理人（写 `op_handler_user_id`），不打断 agent 自动答复；转人工时优先转该预分配运营而非模糊"主管"。

**Architecture:** 新增 `app/services/dispatch/` 服务（与 `routing/` 研发责任田正交）。4 张新表（rules/assignees/config/log）。creator 毕业处预分配、operation_answer/ksm_ingester 转人工处消费。前端 `/admin` 加运营分派规则管理页。

**Tech Stack:** FastAPI + SQLAlchemy + Alembic + Celery（backend），Vite+React+TS+TanStack Query（frontend）。

## Global Constraints

- Python 3.11+；ORM 模型集中在 `app/models.py`；PK 用 INT autoincrement；JSON 字段用 `JSON` 类型（PG JSONB / SQLite 兼容）。
- 单测 SQLite in-memory（StaticPool），不需 Docker；覆盖率门槛 ≥70%（`make unit`）。
- 迁移当前 head = `0028_reviewing_and_reply_draft`；新迁移从 `0029` 起。
- 改后端 API 后必须 `make gen-types` 同步 `frontend/src/api/types.ts`，否则 CI `make check-types` 失败。
- admin 配置端点权限 `require_admin`（与现有 `admin_scopes` 一致）。
- `resolve_supervisor_name` 现有 5 个调用点：`ksm_ingester.py:105`、`operation_answer.py:231/250/278/335`。
- 分派原则：**绝不阻断毕业/自动答复**，任何异常吞掉回落。
- 研发类 hub（Bug_fix/Demand/Internal_task）不参与，`op_handler_user_id` 恒 None。

---

## File Structure

**Backend 新建：**
- `app/services/dispatch/__init__.py` — 导出 `dispatch_operation_handler`, `DispatchResult`
- `app/services/dispatch/engine.py` — 核心分派算法（匹配 + count/ratio + 溢出 + 兜底 + 写 log）
- `app/repositories/dispatch.py` — 4 表数据访问
- `app/api/admin_dispatch.py` — admin CRUD 端点
- `migrations/versions/0029_dispatch_engine.py` — 建 4 表

**Backend 修改：**
- `app/models.py` — 4 个新模型 + `HubIssue.op_handler_user_id` 字段
- `app/services/hub_issues/op_status.py` — 加 `resolve_op_handler(db, hub, settings)`
- `app/services/hub_issues/creator.py` — Operation 毕业处调分派
- `app/services/agents/operation_answer.py` — 4 处转人工改用 `resolve_op_handler`
- `app/services/ingest/ksm_ingester.py` — 驳回处改用 `resolve_op_handler`
- `app/main.py` — 注册 admin_dispatch router

**Frontend 新建：**
- `src/pages/admin/dispatch/DispatchRulesPage.tsx` — 规则列表 + 编辑弹窗 + 分派人子表 + 日志查看
- `src/pages/admin/dispatch/dispatchApi.ts` — API 封装

**Frontend 修改：**
- `src/pages/admin/AdminTabs.tsx` — 加「运营分派」标签

---

## Task 1: 数据模型 + 迁移

**Files:**
- Modify: `backend/app/models.py`（4 新模型 + HubIssue 加字段）
- Create: `backend/migrations/versions/0029_dispatch_engine.py`
- Test: `backend/tests/unit/test_models_dispatch.py`

**Interfaces:**
- Produces: ORM 模型 `DispatchRule`, `DispatchAssignee`, `DispatchConfig`, `DispatchLog`；`HubIssue.op_handler_user_id: Mapped[int | None]`

- [ ] **Step 1: 写失败测试** `backend/tests/unit/test_models_dispatch.py`

```python
"""Dispatch 引擎 4 表 + hub.op_handler_user_id 建模冒烟。"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import DispatchAssignee, DispatchConfig, DispatchLog, DispatchRule, HubIssue


def test_dispatch_rule_roundtrip(db_session: Session) -> None:
    r = DispatchRule(
        name="发票云-数电开票",
        match_sources=["ksm"],
        match_product_lines=["cloud-fapiao"],
        match_modules=["数电开票"],
        match_sla=[],
        dispatch_mode="count",
        rule_type="primary",
        priority=10,
        is_active=True,
    )
    db_session.add(r)
    db_session.flush()
    a = DispatchAssignee(
        rule_id=r.id, user_id=1, alloc_value=Decimal("1"), daily_cap=20, tier="main", is_active=True
    )
    db_session.add(a)
    db_session.add(DispatchConfig(key="default_operation_assignee", value="9"))
    db_session.add(DispatchLog(hub_issue_id=1, rule_id=r.id, assignee_user_id=1, tier_hit="main"))
    db_session.commit()
    assert r.id is not None and a.daily_cap == 20
    assert db_session.query(DispatchLog).count() == 1


def test_hub_op_handler_user_id_defaults_none(db_session: Session) -> None:
    h = HubIssue(
        short_code="HUB-000001", type="Operation", status="created",
        title="t", product_line_code=None, module=None,
    )
    db_session.add(h)
    db_session.commit()
    assert h.op_handler_user_id is None
```

- [ ] **Step 2: 运行验证失败**

Run: `cd backend && .venv/bin/pytest tests/unit/test_models_dispatch.py -v`
Expected: FAIL（ImportError: cannot import name 'DispatchRule'）

- [ ] **Step 3: 在 `app/models.py` 加 4 个模型 + hub 字段**

在 `HubIssue` 的 `op_status_changed_at` 字段后加：
```python
    op_handler_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, index=True
    )
```

在文件末尾（其他表定义区）加：
```python
# ---- Operation 运营分派引擎（dispatch）------------------------------------


class DispatchRule(Base):
    """运营处理人分派规则（多维匹配 + count/ratio）。与 routing 研发责任田正交。"""

    __tablename__ = "dispatch_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    match_sources: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    match_product_lines: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    match_modules: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    match_sla: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    dispatch_mode: Mapped[str] = mapped_column(String(16), nullable=False)  # count|ratio
    rule_type: Mapped[str] = mapped_column(String(16), default="primary", nullable=False)
    overflow_rule_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("dispatch_rules.id"), nullable=True
    )
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    __table_args__ = (
        CheckConstraint("dispatch_mode IN ('count','ratio')", name="ck_dispatch_rules_mode"),
        CheckConstraint("rule_type IN ('primary','overflow')", name="ck_dispatch_rules_type"),
        Index("ix_dispatch_rules_active_priority", "is_active", "priority"),
    )


class DispatchAssignee(Base):
    """规则下的运营处理人。count 模式用 daily_cap，ratio 模式用 alloc_value。"""

    __tablename__ = "dispatch_assignees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dispatch_rules.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    alloc_value: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=1, nullable=False)
    daily_cap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tier: Mapped[str] = mapped_column(String(8), default="main", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (
        CheckConstraint("tier IN ('main','overflow')", name="ck_dispatch_assignees_tier"),
    )


class DispatchConfig(Base):
    """key-value 全局兜底配置（如 default_operation_assignee）。"""

    __tablename__ = "dispatch_config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(128), nullable=False)


class DispatchLog(Base):
    """派单留痕 + 按天计数来源（count/ratio 查 created_at >= 今日零点）。"""

    __tablename__ = "dispatch_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hub_issue_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("hub_issues.id"), nullable=False, index=True
    )
    rule_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("dispatch_rules.id"), nullable=True
    )
    assignee_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    tier_hit: Mapped[str] = mapped_column(String(16), nullable=False)  # main|overflow|default
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    __table_args__ = (
        Index("ix_dispatch_log_rule_created", "rule_id", "created_at"),
    )
```

确认 `app/models.py` 顶部已 import `Decimal`（`from decimal import Decimal`）；若无则加。`CheckConstraint`/`Index`/`Numeric`/`JSON`/`ForeignKey`/`Boolean`/`func` 已在现有 import 中（models.py:30-45）。

- [ ] **Step 4: 写迁移** `backend/migrations/versions/0029_dispatch_engine.py`

```python
"""dispatch 引擎：4 表 + hub_issues.op_handler_user_id

Revision ID: 0029_dispatch_engine
Revises: 0028_reviewing_and_reply_draft
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0029_dispatch_engine"
down_revision: str | None = "0028_reviewing_and_reply_draft"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "dispatch_rules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("match_sources", sa.JSON(), nullable=False),
        sa.Column("match_product_lines", sa.JSON(), nullable=False),
        sa.Column("match_modules", sa.JSON(), nullable=False),
        sa.Column("match_sla", sa.JSON(), nullable=False),
        sa.Column("dispatch_mode", sa.String(16), nullable=False),
        sa.Column("rule_type", sa.String(16), nullable=False, server_default="primary"),
        sa.Column("overflow_rule_id", sa.Integer(), sa.ForeignKey("dispatch_rules.id"), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("dispatch_mode IN ('count','ratio')", name="ck_dispatch_rules_mode"),
        sa.CheckConstraint("rule_type IN ('primary','overflow')", name="ck_dispatch_rules_type"),
    )
    op.create_index("ix_dispatch_rules_active_priority", "dispatch_rules", ["is_active", "priority"])
    op.create_table(
        "dispatch_assignees",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("rule_id", sa.Integer(), sa.ForeignKey("dispatch_rules.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("alloc_value", sa.Numeric(10, 2), nullable=False, server_default="1"),
        sa.Column("daily_cap", sa.Integer(), nullable=True),
        sa.Column("tier", sa.String(8), nullable=False, server_default="main"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint("tier IN ('main','overflow')", name="ck_dispatch_assignees_tier"),
    )
    op.create_index("ix_dispatch_assignees_rule", "dispatch_assignees", ["rule_id"])
    op.create_table(
        "dispatch_config",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.String(128), nullable=False),
    )
    op.create_table(
        "dispatch_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("hub_issue_id", sa.Integer(), sa.ForeignKey("hub_issues.id"), nullable=False),
        sa.Column("rule_id", sa.Integer(), sa.ForeignKey("dispatch_rules.id"), nullable=True),
        sa.Column("assignee_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("tier_hit", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_dispatch_log_hub", "dispatch_log", ["hub_issue_id"])
    op.create_index("ix_dispatch_log_rule_created", "dispatch_log", ["rule_id", "created_at"])
    op.add_column(
        "hub_issues",
        sa.Column("op_handler_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index("ix_hub_issues_op_handler_user", "hub_issues", ["op_handler_user_id"])


def downgrade() -> None:
    op.drop_index("ix_hub_issues_op_handler_user", table_name="hub_issues")
    op.drop_column("hub_issues", "op_handler_user_id")
    op.drop_index("ix_dispatch_log_rule_created", table_name="dispatch_log")
    op.drop_index("ix_dispatch_log_hub", table_name="dispatch_log")
    op.drop_table("dispatch_log")
    op.drop_table("dispatch_config")
    op.drop_index("ix_dispatch_assignees_rule", table_name="dispatch_assignees")
    op.drop_table("dispatch_assignees")
    op.drop_index("ix_dispatch_rules_active_priority", table_name="dispatch_rules")
    op.drop_table("dispatch_rules")
```

- [ ] **Step 5: 运行验证通过**

Run: `cd backend && .venv/bin/pytest tests/unit/test_models_dispatch.py -v`
Expected: PASS（2 passed）

- [ ] **Step 6: 提交**

```bash
git add backend/app/models.py backend/migrations/versions/0029_dispatch_engine.py backend/tests/unit/test_models_dispatch.py
git commit -m "feat(dispatch): 4 表模型 + hub.op_handler_user_id + 迁移 0029"
```

---

## Task 2: 仓储层 `repositories/dispatch.py`

**Files:**
- Create: `backend/app/repositories/dispatch.py`
- Test: `backend/tests/unit/repositories/test_dispatch_repo.py`

**Interfaces:**
- Consumes: Task 1 的模型
- Produces: `DispatchRepository(db)`，方法：
  - `find_matching_rules(source, product_line_code, module, sla) -> list[DispatchRule]`（返回命中的 primary 规则，按 priority 升序）
  - `get_rule(rule_id) -> DispatchRule | None`
  - `active_assignees(rule_id, tier="main") -> list[DispatchAssignee]`
  - `today_counts(rule_id) -> dict[int, int]`（user_id → 今日已派数，查 dispatch_log created_at >= 今日零点）
  - `get_config(key) -> str | None`
  - `add_log(hub_issue_id, rule_id, assignee_user_id, tier_hit) -> DispatchLog`

- [ ] **Step 1: 写失败测试** `backend/tests/unit/repositories/test_dispatch_repo.py`

```python
"""DispatchRepository：匹配、今日计数、兜底配置。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import DispatchLog, DispatchRule
from app.repositories.dispatch import DispatchRepository


def _rule(db: Session, **ov) -> DispatchRule:
    base = dict(
        name="r", match_sources=[], match_product_lines=[], match_modules=[], match_sla=[],
        dispatch_mode="count", rule_type="primary", priority=100, is_active=True,
    )
    base.update(ov)
    r = DispatchRule(**base)
    db.add(r)
    db.flush()
    return r


def test_match_and_or_semantics(db_session: Session) -> None:
    repo = DispatchRepository(db_session)
    _rule(db_session, name="A", match_product_lines=["cloud-fapiao"], match_modules=["数电开票"], priority=10)
    _rule(db_session, name="B", match_product_lines=["cloud-erp-star"], priority=20)  # 不同产品线
    db_session.commit()
    hits = repo.find_matching_rules(source="ksm", product_line_code="cloud-fapiao", module="数电开票", sla=None)
    assert [r.name for r in hits] == ["A"]  # 只 A 命中，B 产品线不符


def test_empty_list_means_unrestricted(db_session: Session) -> None:
    repo = DispatchRepository(db_session)
    _rule(db_session, name="ALL")  # 全空 = 不限
    db_session.commit()
    hits = repo.find_matching_rules(source="zhichi", product_line_code="x", module="y", sla=None)
    assert [r.name for r in hits] == ["ALL"]


def test_priority_order(db_session: Session) -> None:
    repo = DispatchRepository(db_session)
    _rule(db_session, name="LO", priority=50)
    _rule(db_session, name="HI", priority=5)
    db_session.commit()
    hits = repo.find_matching_rules(source="ksm", product_line_code="p", module="m", sla=None)
    assert [r.name for r in hits] == ["HI", "LO"]  # priority 升序


def test_today_counts_windows_by_day(db_session: Session) -> None:
    repo = DispatchRepository(db_session)
    r = _rule(db_session)
    now = datetime.now(UTC)
    db_session.add(DispatchLog(hub_issue_id=1, rule_id=r.id, assignee_user_id=7, tier_hit="main", created_at=now))
    yesterday = now - timedelta(days=1)
    db_session.add(DispatchLog(hub_issue_id=2, rule_id=r.id, assignee_user_id=7, tier_hit="main", created_at=yesterday))
    db_session.commit()
    counts = repo.today_counts(r.id)
    assert counts.get(7) == 1  # 昨天那条不计


def test_inactive_rule_excluded(db_session: Session) -> None:
    repo = DispatchRepository(db_session)
    _rule(db_session, name="OFF", is_active=False)
    db_session.commit()
    assert repo.find_matching_rules(source="ksm", product_line_code="p", module="m", sla=None) == []
```

- [ ] **Step 2: 运行验证失败**

Run: `cd backend && .venv/bin/pytest tests/unit/repositories/test_dispatch_repo.py -v`
Expected: FAIL（ModuleNotFoundError: app.repositories.dispatch）

- [ ] **Step 3: 实现** `backend/app/repositories/dispatch.py`

```python
"""DispatchRepository — 运营分派引擎数据访问。

匹配语义：match_* 之间 AND，列表内 OR，空列表=该维度不限。
按天计数：dispatch_log.created_at >= 今日零点（本地自然日，用 UTC 存储）。
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DispatchAssignee, DispatchConfig, DispatchLog, DispatchRule
from app.services.sla.workday import BEIJING


def _today_start() -> datetime:
    """北京自然日 00:00 换算成 UTC（与 metrics/workbench.py 的 BEIJING 口径一致）。
    dispatch_log.created_at 存 UTC，故返回 tz-aware UTC datetime 供比较。"""
    now_bj = datetime.now(UTC).astimezone(BEIJING)
    midnight_bj = datetime.combine(now_bj.date(), time.min, tzinfo=BEIJING)
    return midnight_bj.astimezone(UTC)


def _match(rule_values: list, candidate: str | None) -> bool:
    """空列表 = 不限（命中）；非空则 candidate 必须在列表内。"""
    if not rule_values:
        return True
    return candidate is not None and candidate in rule_values


class DispatchRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def find_matching_rules(
        self, *, source: str | None, product_line_code: str | None,
        module: str | None, sla: str | None,
    ) -> list[DispatchRule]:
        rules = list(
            self._db.execute(
                select(DispatchRule)
                .where(DispatchRule.is_active.is_(True), DispatchRule.rule_type == "primary")
                .order_by(DispatchRule.priority.asc(), DispatchRule.id.asc())
            ).scalars().all()
        )
        return [
            r for r in rules
            if _match(r.match_sources, source)
            and _match(r.match_product_lines, product_line_code)
            and _match(r.match_modules, module)
            and _match(r.match_sla, sla)
        ]

    def get_rule(self, rule_id: int) -> DispatchRule | None:
        return self._db.get(DispatchRule, rule_id)

    def active_assignees(self, rule_id: int, tier: str = "main") -> list[DispatchAssignee]:
        return list(
            self._db.execute(
                select(DispatchAssignee).where(
                    DispatchAssignee.rule_id == rule_id,
                    DispatchAssignee.tier == tier,
                    DispatchAssignee.is_active.is_(True),
                ).order_by(DispatchAssignee.id.asc())
            ).scalars().all()
        )

    def today_counts(self, rule_id: int) -> dict[int, int]:
        rows = self._db.execute(
            select(DispatchLog.assignee_user_id, func.count(DispatchLog.id))
            .where(DispatchLog.rule_id == rule_id, DispatchLog.created_at >= _today_start())
            .group_by(DispatchLog.assignee_user_id)
        ).all()
        return {uid: cnt for uid, cnt in rows}

    def get_config(self, key: str) -> str | None:
        row = self._db.get(DispatchConfig, key)
        return row.value if row else None

    def add_log(
        self, *, hub_issue_id: int, rule_id: int | None, assignee_user_id: int, tier_hit: str
    ) -> DispatchLog:
        log = DispatchLog(
            hub_issue_id=hub_issue_id, rule_id=rule_id,
            assignee_user_id=assignee_user_id, tier_hit=tier_hit,
        )
        self._db.add(log)
        self._db.flush()
        return log
```

- [ ] **Step 4: 运行验证通过**

Run: `cd backend && .venv/bin/pytest tests/unit/repositories/test_dispatch_repo.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/app/repositories/dispatch.py backend/tests/unit/repositories/test_dispatch_repo.py
git commit -m "feat(dispatch): DispatchRepository 匹配/今日计数/兜底"
```

---

## Task 3: 分派引擎 `services/dispatch/engine.py`

**Files:**
- Create: `backend/app/services/dispatch/__init__.py`, `backend/app/services/dispatch/engine.py`
- Test: `backend/tests/unit/services/test_dispatch_engine.py`

**Interfaces:**
- Consumes: Task 2 的 `DispatchRepository`；校验 user 有效性用 `app.models.User`
- Produces:
  - `DispatchResult(user_id: int | None, user_name: str | None, rule_id: int | None, tier: str | None, reason: str)`
  - `dispatch_operation_handler(db, hub) -> DispatchResult`（异常内部吞掉，返回 user_id=None）

- [ ] **Step 1: 写失败测试** `backend/tests/unit/services/test_dispatch_engine.py`

```python
"""分派引擎：count 最少者/daily_cap/溢出/兜底、ratio 缺口最大、跨天、边界。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import DispatchAssignee, DispatchConfig, DispatchLog, DispatchRule, HubIssue, User
from app.services.dispatch.engine import dispatch_operation_handler


def _user(db: Session, uid: int, name: str) -> None:
    db.add(User(id=uid, feishu_uid=f"ou_{uid}", name=name, role="assignee"))


def _hub(db: Session, **ov) -> HubIssue:
    base = dict(short_code="HUB-000001", type="Operation", status="created", title="t",
                product_line_code="cloud-fapiao", module="数电开票")
    base.update(ov)
    h = HubIssue(**base)
    db.add(h)
    db.flush()
    return h


def _rule(db: Session, mode: str = "count", **ov) -> DispatchRule:
    base = dict(name="r", match_sources=[], match_product_lines=[], match_modules=[],
                match_sla=[], dispatch_mode=mode, rule_type="primary", priority=100, is_active=True)
    base.update(ov)
    r = DispatchRule(**base)
    db.add(r)
    db.flush()
    return r


def test_count_picks_least_today(db_session: Session) -> None:
    _user(db_session, 1, "张三"); _user(db_session, 2, "李四")
    r = _rule(db_session)
    db_session.add(DispatchAssignee(rule_id=r.id, user_id=1, daily_cap=20, tier="main", is_active=True))
    db_session.add(DispatchAssignee(rule_id=r.id, user_id=2, daily_cap=20, tier="main", is_active=True))
    db_session.add(DispatchLog(hub_issue_id=99, rule_id=r.id, assignee_user_id=1, tier_hit="main"))  # 张三今日已1
    h = _hub(db_session)
    db_session.commit()
    res = dispatch_operation_handler(db_session, h)
    assert res.user_id == 2 and res.tier == "main"  # 李四今日0，更少


def test_count_daily_cap_full_goes_overflow(db_session: Session) -> None:
    _user(db_session, 1, "张三"); _user(db_session, 5, "出王")
    over = _rule(db_session, name="overflow", rule_type="overflow")
    db_session.add(DispatchAssignee(rule_id=over.id, user_id=5, daily_cap=None, tier="main", is_active=True))
    db_session.flush()
    main = _rule(db_session, name="main", overflow_rule_id=over.id)
    db_session.add(DispatchAssignee(rule_id=main.id, user_id=1, daily_cap=1, tier="main", is_active=True))
    db_session.add(DispatchLog(hub_issue_id=99, rule_id=main.id, assignee_user_id=1, tier_hit="main"))  # 已满 1/1
    h = _hub(db_session)
    db_session.commit()
    res = dispatch_operation_handler(db_session, h)
    assert res.user_id == 5 and res.tier == "overflow"


def test_falls_back_to_config_default(db_session: Session) -> None:
    _user(db_session, 1, "张三"); _user(db_session, 9, "兜底")
    r = _rule(db_session)
    db_session.add(DispatchAssignee(rule_id=r.id, user_id=1, daily_cap=1, tier="main", is_active=True))
    db_session.add(DispatchLog(hub_issue_id=99, rule_id=r.id, assignee_user_id=1, tier_hit="main"))  # 满，无溢出
    db_session.add(DispatchConfig(key="default_operation_assignee", value="9"))
    h = _hub(db_session)
    db_session.commit()
    res = dispatch_operation_handler(db_session, h)
    assert res.user_id == 9 and res.tier == "default"


def test_no_match_returns_none(db_session: Session) -> None:
    r = _rule(db_session, match_product_lines=["other-product"])  # 不命中
    h = _hub(db_session)
    db_session.commit()
    res = dispatch_operation_handler(db_session, h)
    assert res.user_id is None


def test_ratio_picks_largest_gap(db_session: Session) -> None:
    _user(db_session, 1, "赵六"); _user(db_session, 2, "钱七")
    r = _rule(db_session, mode="ratio")
    db_session.add(DispatchAssignee(rule_id=r.id, user_id=1, alloc_value=Decimal("5"), tier="main", is_active=True))
    db_session.add(DispatchAssignee(rule_id=r.id, user_id=2, alloc_value=Decimal("5"), tier="main", is_active=True))
    db_session.add(DispatchLog(hub_issue_id=99, rule_id=r.id, assignee_user_id=1, tier_hit="main"))  # 赵六已1，钱七0
    h = _hub(db_session)
    db_session.commit()
    res = dispatch_operation_handler(db_session, h)
    assert res.user_id == 2  # 同权重，钱七缺口大


def test_inactive_assignee_filtered(db_session: Session) -> None:
    _user(db_session, 1, "停用"); _user(db_session, 2, "在岗")
    r = _rule(db_session)
    db_session.add(DispatchAssignee(rule_id=r.id, user_id=1, daily_cap=20, tier="main", is_active=False))
    db_session.add(DispatchAssignee(rule_id=r.id, user_id=2, daily_cap=20, tier="main", is_active=True))
    h = _hub(db_session)
    db_session.commit()
    res = dispatch_operation_handler(db_session, h)
    assert res.user_id == 2
```

- [ ] **Step 2: 运行验证失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_dispatch_engine.py -v`
Expected: FAIL（ModuleNotFoundError: app.services.dispatch.engine）

- [ ] **Step 3: 实现引擎**

`backend/app/services/dispatch/engine.py`：
```python
"""Operation 运营处理人分派引擎.

毕业时调 dispatch_operation_handler 选运营。绝不抛异常（吞掉返回 None），
不阻断毕业。count：今日未达 daily_cap 者选最少，全满→溢出规则→兜底配置。
ratio：按 alloc_value 权重，选今日「应得占比 - 实际占比」缺口最大者。
按天靠 today_counts 只查当天 dispatch_log 天然重置。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import DispatchAssignee, DispatchRule, HubIssue, User
from app.repositories.dispatch import DispatchRepository

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class DispatchResult:
    user_id: int | None
    user_name: str | None
    rule_id: int | None
    tier: str | None
    reason: str


_NONE = DispatchResult(user_id=None, user_name=None, rule_id=None, tier=None, reason="no dispatch")


def _valid_user_name(db: Session, user_id: int) -> str | None:
    u = db.get(User, user_id)
    if u is None or u.deleted_at is not None or not u.is_active:
        return None
    return u.name


def _pick_count(assignees: list[DispatchAssignee], counts: dict[int, int]) -> int | None:
    """今日未达 daily_cap 的人里选今日最少者；全满返回 None。"""
    avail = [
        a for a in assignees
        if a.daily_cap is None or counts.get(a.user_id, 0) < a.daily_cap
    ]
    if not avail:
        return None
    return min(avail, key=lambda a: counts.get(a.user_id, 0)).user_id


def _pick_ratio(assignees: list[DispatchAssignee], counts: dict[int, int]) -> int | None:
    """按 alloc_value 权重选「应得占比 - 实际占比」缺口最大者。"""
    if not assignees:
        return None
    total_w = sum(float(a.alloc_value) for a in assignees) or 1.0
    total_n = sum(counts.get(a.user_id, 0) for a in assignees) or 0
    best_uid, best_gap = None, None
    for a in assignees:
        want = float(a.alloc_value) / total_w
        actual = (counts.get(a.user_id, 0) / total_n) if total_n else 0.0
        gap = want - actual
        if best_gap is None or gap > best_gap:
            best_gap, best_uid = gap, a.user_id
    return best_uid


def _pick_from_rule(
    db: Session, repo: DispatchRepository, rule: DispatchRule
) -> tuple[int | None, str]:
    """在单条规则的 main 层选人。返回 (user_id, tier)。全不可用 → (None, '')。"""
    assignees = [a for a in repo.active_assignees(rule.id, tier="main")
                 if _valid_user_name(db, a.user_id) is not None]
    if not assignees:
        return None, ""
    counts = repo.today_counts(rule.id)
    uid = _pick_ratio(assignees, counts) if rule.dispatch_mode == "ratio" \
        else _pick_count(assignees, counts)
    return uid, ("main" if uid is not None else "")


def dispatch_operation_handler(db: Session, hub: HubIssue) -> DispatchResult:
    """选运营处理人。只对 Operation 有意义（调用方保证）。任何异常吞掉返回 _NONE。"""
    try:
        repo = DispatchRepository(db)
        source = _hub_source_code(db, hub)
        rules = repo.find_matching_rules(
            source=source, product_line_code=hub.product_line_code,
            module=hub.module, sla=None,
        )
        if not rules:
            return _NONE
        rule = rules[0]  # priority 最小

        uid, tier = _pick_from_rule(db, repo, rule)
        rule_id_hit = rule.id

        # main 全满 → 溢出规则
        if uid is None and rule.overflow_rule_id is not None:
            over = repo.get_rule(rule.overflow_rule_id)
            if over is not None and over.is_active:
                uid, _ = _pick_from_rule(db, repo, over)
                if uid is not None:
                    tier, rule_id_hit = "overflow", over.id

        # 仍无 → 兜底配置
        if uid is None:
            cfg = repo.get_config("default_operation_assignee")
            if cfg and cfg.isdigit():
                uid, tier, rule_id_hit = int(cfg), "default", None

        if uid is None:
            return _NONE
        name = _valid_user_name(db, uid)
        if name is None:
            return _NONE

        repo.add_log(hub_issue_id=hub.id, rule_id=rule_id_hit, assignee_user_id=uid, tier_hit=tier)
        return DispatchResult(
            user_id=uid, user_name=name, rule_id=rule_id_hit, tier=tier,
            reason=f"dispatch tier={tier} rule={rule_id_hit}",
        )
    except Exception:
        logger.exception("dispatch_operation_handler_failed", hub_issue_id=getattr(hub, "id", None))
        return _NONE


def _hub_source_code(db: Session, hub: HubIssue) -> str | None:
    """取关联工单的 source_code（多工单取第一个；无则 None）。dispatch 按来源匹配用。"""
    from app.models import Ticket

    t = (
        db.query(Ticket)
        .filter(Ticket.hub_issue_id == hub.id, Ticket.deleted_at.is_(None))
        .order_by(Ticket.id.asc())
        .first()
    )
    return t.source_code if t is not None else None
```

`backend/app/services/dispatch/__init__.py`：
```python
"""Operation 运营处理人分派引擎。"""

from app.services.dispatch.engine import DispatchResult, dispatch_operation_handler

__all__ = ["DispatchResult", "dispatch_operation_handler"]
```

- [ ] **Step 4: 运行验证通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_dispatch_engine.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/dispatch/ backend/tests/unit/services/test_dispatch_engine.py
git commit -m "feat(dispatch): 分派引擎 count/ratio/溢出/兜底"
```

---

## Task 4: 毕业时预分配 + resolve_op_handler 消费

**Files:**
- Modify: `backend/app/services/hub_issues/op_status.py`（加 `resolve_op_handler`）
- Modify: `backend/app/services/hub_issues/creator.py`（毕业后调分派写 op_handler_user_id）
- Modify: `backend/app/services/agents/operation_answer.py`（4 处 resolve_supervisor_name → resolve_op_handler）
- Modify: `backend/app/services/ingest/ksm_ingester.py`（驳回处 1 处）
- Test: `backend/tests/unit/services/test_dispatch_integration.py`

**Interfaces:**
- Consumes: Task 3 的 `dispatch_operation_handler`；现有 `resolve_supervisor_name(db, settings)`
- Produces: `resolve_op_handler(db, hub, settings) -> str`（有 op_handler_user_id 且 user 有效 → 该 user.name；否则 resolve_supervisor_name）

- [ ] **Step 1: 写失败测试** `backend/tests/unit/services/test_dispatch_integration.py`

```python
"""集成点：毕业预分配、resolve_op_handler 回落、drain 口径不变。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import DispatchAssignee, DispatchRule, HubIssue, User
from app.services.hub_issues.op_status import resolve_op_handler


def _user(db: Session, uid: int, name: str, active: bool = True) -> None:
    db.add(User(id=uid, feishu_uid=f"ou_{uid}", name=name, role="assignee", is_active=active))


def test_resolve_prefers_preassigned(db_session: Session) -> None:
    _user(db_session, 3, "运营小美")
    h = HubIssue(short_code="HUB-1", type="Operation", status="created", title="t",
                 op_handler_user_id=3)
    db_session.add(h); db_session.commit()
    assert resolve_op_handler(db_session, h, get_settings()) == "运营小美"


def test_resolve_falls_back_when_no_preassign(db_session: Session) -> None:
    h = HubIssue(short_code="HUB-2", type="Operation", status="created", title="t",
                 op_handler_user_id=None)
    db_session.add(h); db_session.commit()
    # 无 default_pool 配置时 resolve_supervisor_name 返回 "主管"
    assert resolve_op_handler(db_session, h, get_settings()) == "主管"


def test_resolve_falls_back_when_preassigned_inactive(db_session: Session) -> None:
    _user(db_session, 4, "已离职", active=False)
    h = HubIssue(short_code="HUB-3", type="Operation", status="created", title="t",
                 op_handler_user_id=4)
    db_session.add(h); db_session.commit()
    assert resolve_op_handler(db_session, h, get_settings()) == "主管"
```

- [ ] **Step 2: 运行验证失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_dispatch_integration.py -v`
Expected: FAIL（ImportError: cannot import name 'resolve_op_handler'）

- [ ] **Step 3: 在 `op_status.py` 加 `resolve_op_handler`**

在 `resolve_supervisor_name` 函数后加：
```python
def resolve_op_handler(db: Session, hub: "HubIssue", settings: Settings | None = None) -> str:
    """转人工处理人名：优先预分配运营（op_handler_user_id 且 user 有效），
    否则回落 resolve_supervisor_name（default_pool 或 '主管'）。"""
    settings = settings or get_settings()
    uid = hub.op_handler_user_id
    if uid is not None:
        from app.models import User

        u = db.get(User, uid)
        if u is not None and u.deleted_at is None and u.is_active and u.name:
            return str(u.name)
    return resolve_supervisor_name(db, settings)
```

- [ ] **Step 4: `creator.py` 毕业后调分派**

在 `db.flush()`（`hub` 已有 id）之后、hub_dedup 查重之前，加：
```python
    # Operation 毕业：按规则预分配运营处理人（写 op_handler_user_id）。
    # op_handler 名字仍保持 'agent'，不打断 drain 自动答复；转人工时才切成运营名。
    if issue_type == "Operation":
        from app.services.dispatch import dispatch_operation_handler

        dr = dispatch_operation_handler(db, hub)
        if dr.user_id is not None:
            hub.op_handler_user_id = dr.user_id
```

- [ ] **Step 5: `operation_answer.py` 4 处替换**

import 处（第 31 行附近）把 `resolve_supervisor_name` 换成 `resolve_op_handler`（保留 resolve_supervisor_name 若他处仍用；此处两者都 import）。第 231/250/278/335 行的 `handler=resolve_supervisor_name(db, settings)` 全部改为 `handler=resolve_op_handler(db, hub, settings)`。

- [ ] **Step 6: `ksm_ingester.py` 驳回处替换**

第 36 行 import 加 `resolve_op_handler`；第 105 行 `handler=resolve_supervisor_name(self._db, get_settings())` 改为 `handler=resolve_op_handler(self._db, hub, get_settings())`（该分支 `hub` 变量已存在，见 ksm_ingester.py:98-110 驳回块）。

- [ ] **Step 7: 运行验证通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_dispatch_integration.py -v`
Expected: PASS（3 passed）

- [ ] **Step 8: 回归 + 提交**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_operation_answer.py tests/unit/ -q -k "operation or ksm or dispatch"`
Expected: PASS（无回归）

```bash
git add backend/app/services/hub_issues/op_status.py backend/app/services/hub_issues/creator.py backend/app/services/agents/operation_answer.py backend/app/services/ingest/ksm_ingester.py backend/tests/unit/services/test_dispatch_integration.py
git commit -m "feat(dispatch): 毕业预分配运营 + 转人工优先转预分配(resolve_op_handler)"
```

---

## Task 5: admin CRUD 端点

**Files:**
- Create: `backend/app/api/admin_dispatch.py`
- Modify: `backend/app/main.py`（注册 router）
- Test: `backend/tests/unit/api/test_admin_dispatch.py`

**Interfaces:**
- Consumes: Task 1 模型、`require_admin`、`get_session`
- Produces: 端点 `GET/POST/PUT/DELETE /api/admin/dispatch/rules`、`.../rules/{id}/assignees`（GET/POST/DELETE）、`GET/PUT /api/admin/dispatch/config`、`GET /api/admin/dispatch/logs`

- [ ] **Step 1: 写失败测试** `backend/tests/unit/api/test_admin_dispatch.py`

```python
"""admin dispatch CRUD + 权限。"""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def test_non_admin_forbidden(app_client: TestClient, member_token: str) -> None:
    r = app_client.get("/api/admin/dispatch/rules", headers={"Authorization": f"Bearer {member_token}"})
    assert r.status_code == 403


def test_rule_crud(app_client: TestClient, admin_token: str) -> None:
    h = {"Authorization": f"Bearer {admin_token}"}
    body = {
        "name": "发票云运营", "match_sources": ["ksm"], "match_product_lines": ["cloud-fapiao"],
        "match_modules": ["数电开票"], "match_sla": [], "dispatch_mode": "count",
        "rule_type": "primary", "priority": 10,
    }
    r = app_client.post("/api/admin/dispatch/rules", json=body, headers=h)
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert app_client.get("/api/admin/dispatch/rules", headers=h).json()  # 非空列表
    r2 = app_client.put(f"/api/admin/dispatch/rules/{rid}", json={**body, "priority": 5}, headers=h)
    assert r2.status_code == 200 and r2.json()["priority"] == 5
    assert app_client.delete(f"/api/admin/dispatch/rules/{rid}", headers=h).status_code == 204


def test_assignee_and_config(app_client: TestClient, admin_token: str, db_session: Session) -> None:
    from app.models import User

    db_session.add(User(id=50, feishu_uid="ou_50", name="运营A", role="assignee"))
    db_session.commit()
    h = {"Authorization": f"Bearer {admin_token}"}
    rid = app_client.post("/api/admin/dispatch/rules", json={
        "name": "r", "match_sources": [], "match_product_lines": [], "match_modules": [],
        "match_sla": [], "dispatch_mode": "count", "rule_type": "primary", "priority": 100,
    }, headers=h).json()["id"]
    ra = app_client.post(f"/api/admin/dispatch/rules/{rid}/assignees", json={
        "user_id": 50, "alloc_value": 1, "daily_cap": 20, "tier": "main",
    }, headers=h)
    assert ra.status_code == 200, ra.text
    rc = app_client.put("/api/admin/dispatch/config", json={"key": "default_operation_assignee", "value": "50"}, headers=h)
    assert rc.status_code == 200
    assert app_client.get("/api/admin/dispatch/config", headers=h).json()["default_operation_assignee"] == "50"
```

（注：`app_client` / `admin_token` / `member_token` / `db_session` fixture 沿用现有 conftest；若 `admin_token`/`member_token` 命名不同，先 grep `tests/conftest.py` 与现有 `test_admin_scopes.py` 对齐 fixture 名。）

- [ ] **Step 2: 运行验证失败**

Run: `cd backend && .venv/bin/pytest tests/unit/api/test_admin_dispatch.py -v`
Expected: FAIL（404，路由未注册）

- [ ] **Step 3: 实现端点** `backend/app/api/admin_dispatch.py`

```python
"""Admin /api/admin/dispatch/* — 运营分派规则 CRUD（require_admin）。"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps.auth import AuthedUser, require_admin
from app.core.logging import get_logger
from app.db import get_session
from app.models import DispatchAssignee, DispatchConfig, DispatchLog, DispatchRule

router = APIRouter()
logger = get_logger(__name__)


class RuleBody(BaseModel):
    name: str
    match_sources: list[str] = Field(default_factory=list)
    match_product_lines: list[str] = Field(default_factory=list)
    match_modules: list[str] = Field(default_factory=list)
    match_sla: list[str] = Field(default_factory=list)
    dispatch_mode: str = Field(pattern="^(count|ratio)$")
    rule_type: str = Field(default="primary", pattern="^(primary|overflow)$")
    overflow_rule_id: int | None = None
    priority: int = 100
    is_active: bool = True


class RuleOut(RuleBody):
    id: int


class AssigneeBody(BaseModel):
    user_id: int
    alloc_value: float = 1
    daily_cap: int | None = None
    tier: str = Field(default="main", pattern="^(main|overflow)$")
    is_active: bool = True


class AssigneeOut(AssigneeBody):
    id: int
    rule_id: int


def _rule_out(r: DispatchRule) -> RuleOut:
    return RuleOut(
        id=r.id, name=r.name, match_sources=r.match_sources, match_product_lines=r.match_product_lines,
        match_modules=r.match_modules, match_sla=r.match_sla, dispatch_mode=r.dispatch_mode,
        rule_type=r.rule_type, overflow_rule_id=r.overflow_rule_id, priority=r.priority,
        is_active=r.is_active,
    )


@router.get("/rules", response_model=list[RuleOut])
def list_rules(
    user: AuthedUser = Depends(require_admin), db: Session = Depends(get_session)
) -> list[RuleOut]:
    rows = db.execute(select(DispatchRule).order_by(DispatchRule.priority.asc(), DispatchRule.id.asc())).scalars().all()
    return [_rule_out(r) for r in rows]


@router.post("/rules", response_model=RuleOut)
def create_rule(
    body: RuleBody, user: AuthedUser = Depends(require_admin), db: Session = Depends(get_session)
) -> RuleOut:
    r = DispatchRule(**body.model_dump())
    db.add(r)
    db.commit()
    return _rule_out(r)


@router.put("/rules/{rule_id}", response_model=RuleOut)
def update_rule(
    rule_id: int, body: RuleBody,
    user: AuthedUser = Depends(require_admin), db: Session = Depends(get_session)
) -> RuleOut:
    r = db.get(DispatchRule, rule_id)
    if r is None:
        raise HTTPException(status_code=404, detail="rule not found")
    for k, v in body.model_dump().items():
        setattr(r, k, v)
    db.commit()
    return _rule_out(r)


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(
    rule_id: int, user: AuthedUser = Depends(require_admin), db: Session = Depends(get_session)
) -> Response:
    r = db.get(DispatchRule, rule_id)
    if r is not None:
        db.delete(r)
        db.commit()
    return Response(status_code=204)


@router.get("/rules/{rule_id}/assignees", response_model=list[AssigneeOut])
def list_assignees(
    rule_id: int, user: AuthedUser = Depends(require_admin), db: Session = Depends(get_session)
) -> list[AssigneeOut]:
    rows = db.execute(select(DispatchAssignee).where(DispatchAssignee.rule_id == rule_id).order_by(DispatchAssignee.id)).scalars().all()
    return [
        AssigneeOut(id=a.id, rule_id=a.rule_id, user_id=a.user_id, alloc_value=float(a.alloc_value),
                    daily_cap=a.daily_cap, tier=a.tier, is_active=a.is_active)
        for a in rows
    ]


@router.post("/rules/{rule_id}/assignees", response_model=AssigneeOut)
def add_assignee(
    rule_id: int, body: AssigneeBody,
    user: AuthedUser = Depends(require_admin), db: Session = Depends(get_session)
) -> AssigneeOut:
    if db.get(DispatchRule, rule_id) is None:
        raise HTTPException(status_code=404, detail="rule not found")
    a = DispatchAssignee(
        rule_id=rule_id, user_id=body.user_id, alloc_value=Decimal(str(body.alloc_value)),
        daily_cap=body.daily_cap, tier=body.tier, is_active=body.is_active,
    )
    db.add(a)
    db.commit()
    return AssigneeOut(id=a.id, rule_id=a.rule_id, user_id=a.user_id, alloc_value=float(a.alloc_value),
                       daily_cap=a.daily_cap, tier=a.tier, is_active=a.is_active)


@router.delete("/rules/{rule_id}/assignees/{assignee_id}", status_code=204)
def delete_assignee(
    rule_id: int, assignee_id: int,
    user: AuthedUser = Depends(require_admin), db: Session = Depends(get_session)
) -> Response:
    a = db.get(DispatchAssignee, assignee_id)
    if a is not None and a.rule_id == rule_id:
        db.delete(a)
        db.commit()
    return Response(status_code=204)


@router.get("/config")
def get_config(
    user: AuthedUser = Depends(require_admin), db: Session = Depends(get_session)
) -> dict[str, str]:
    rows = db.execute(select(DispatchConfig)).scalars().all()
    return {r.key: r.value for r in rows}


class ConfigBody(BaseModel):
    key: str
    value: str


@router.put("/config")
def put_config(
    body: ConfigBody, user: AuthedUser = Depends(require_admin), db: Session = Depends(get_session)
) -> dict[str, str]:
    row = db.get(DispatchConfig, body.key)
    if row is None:
        db.add(DispatchConfig(key=body.key, value=body.value))
    else:
        row.value = body.value
    db.commit()
    return {body.key: body.value}


@router.get("/logs")
def list_logs(
    rule_id: int | None = Query(default=None),
    user: AuthedUser = Depends(require_admin), db: Session = Depends(get_session)
) -> list[dict[str, Any]]:
    stmt = select(DispatchLog).order_by(DispatchLog.created_at.desc()).limit(200)
    if rule_id is not None:
        stmt = select(DispatchLog).where(DispatchLog.rule_id == rule_id).order_by(DispatchLog.created_at.desc()).limit(200)
    rows = db.execute(stmt).scalars().all()
    return [
        {"id": r.id, "hub_issue_id": r.hub_issue_id, "rule_id": r.rule_id,
         "assignee_user_id": r.assignee_user_id, "tier_hit": r.tier_hit,
         "created_at": r.created_at.isoformat()}
        for r in rows
    ]
```

- [ ] **Step 4: 注册 router** — `backend/app/main.py`

import 区加 `admin_dispatch`（与 admin_scopes 同处），注册区加：
```python
    app.include_router(admin_dispatch.router, prefix="/api/admin/dispatch", tags=["admin-dispatch"])
```

- [ ] **Step 5: 运行验证通过 + 同步类型**

Run: `cd backend && .venv/bin/pytest tests/unit/api/test_admin_dispatch.py -v`
Expected: PASS（3 passed）
Run: `cd /Users/junill/Documents/04_claude/01_ticket/hub-issue && make gen-types`
Expected: 更新 `frontend/src/api/openapi.json` + `types.ts`

- [ ] **Step 6: 提交**

```bash
git add backend/app/api/admin_dispatch.py backend/app/main.py backend/tests/unit/api/test_admin_dispatch.py frontend/src/api/openapi.json frontend/src/api/types.ts
git commit -m "feat(dispatch): admin CRUD 端点 + 类型同步"
```

---

## Task 6: 前端运营分派规则管理页

**Files:**
- Create: `frontend/src/pages/admin/dispatch/DispatchRulesPage.tsx`, `frontend/src/pages/admin/dispatch/dispatchApi.ts`
- Modify: `frontend/src/pages/admin/AdminTabs.tsx`（加标签）
- Test: `frontend/src/pages/admin/dispatch/DispatchRulesPage.test.tsx`

**Interfaces:**
- Consumes: Task 5 端点（经生成的 types）；现有 `UserSelect` 组件、`api` client
- Produces: `/admin` 下「运营分派」标签页

- [ ] **Step 1: 写失败组件测试** `DispatchRulesPage.test.tsx`

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import DispatchRulesPage from "./DispatchRulesPage";

vi.mock("../../../api/client", () => ({
  api: { get: vi.fn().mockResolvedValue({ data: [] }) },
}));

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

describe("DispatchRulesPage", () => {
  it("renders rules heading", async () => {
    render(wrap(<DispatchRulesPage />));
    expect(await screen.findByText(/运营分派规则/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 运行验证失败**

Run: `cd frontend && npm run test -- DispatchRulesPage`
Expected: FAIL（找不到模块）

- [ ] **Step 3: 实现 API 封装 + 页面**

`dispatchApi.ts`：封装 rules/assignees/config/logs 的 GET/POST/PUT/DELETE（用现有 `api` client，参考 `PeopleScopesPage.tsx` 的调用惯例）。

`DispatchRulesPage.tsx`：
- 标题「运营分派规则」
- `useQuery` 拉 `/api/admin/dispatch/rules` 渲染表格（规则名/匹配摘要/模式/优先级/溢出/启用）
- 「新建规则」按钮打开编辑弹窗：匹配条件多选（产品线/模块/来源/SLA 用文本/多选输入）、`dispatch_mode` 切换（切到 count 显示 daily_cap 列，ratio 显示 alloc_value 列）、溢出规则下拉（仅 count，选 rule_type=overflow 的规则）、优先级
- 分派人子表：动态行（`UserSelect` 选人 + daily_cap/alloc_value 输入 + tier 下拉），保存调 assignees 端点
- 「查看今日派单」按钮：拉 `/logs?rule_id=` 展示各运营今日计数

（实现时对照 `frontend/src/pages/admin/users/PeopleScopesPage.tsx` 的表格+弹窗+mutation 模式，样式复用现有 Tailwind hub-* 类。）

- [ ] **Step 4: 加标签** — `AdminTabs.tsx`

在现有标签数组加一项（label「运营分派」，指向 `DispatchRulesPage`），仅 admin 可见（参考现有标签的角色过滤）。

- [ ] **Step 5: 运行验证通过**

Run: `cd frontend && npm run test -- DispatchRulesPage`
Expected: PASS
Run: `cd frontend && npm run type-check`
Expected: 无错误

- [ ] **Step 6: 提交**

```bash
git add frontend/src/pages/admin/dispatch/ frontend/src/pages/admin/AdminTabs.tsx
git commit -m "feat(dispatch): 前端运营分派规则管理页"
```

---

## Task 7: 全量验证

**Files:** 无新增，仅验证。

- [ ] **Step 1: 后端全量**

Run: `cd backend && make lint && make unit`
Expected: lint 通过（新文件）；unit 全过、覆盖率 ≥70%

- [ ] **Step 2: 迁移升降**

Run: `cd backend && .venv/bin/alembic upgrade head && .venv/bin/alembic downgrade -1 && .venv/bin/alembic upgrade head`
Expected: 无错误（需本地 PG；无 PG 时跳过并记录）
Expected: `alembic current` = `0029_dispatch_engine`

- [ ] **Step 3: 类型同步校验**

Run: `cd /Users/junill/Documents/04_claude/01_ticket/hub-issue && make check-types`
Expected: openapi.json / types.ts 与后端一致

- [ ] **Step 4: 前端**

Run: `cd frontend && npm run type-check && npm run test`
Expected: 全过

- [ ] **Step 5: 端到端手验（起本地服务）**

起后端 + 前端，`/admin` → 运营分派标签：新建一条 count 规则（发票云/数电开票，加 2 个运营 daily_cap=1），造 2 张 Operation 工单毕业（走 ksm webhook 或直接建），查 hub.op_handler_user_id 分别落到两人；第 3 张触发溢出/兜底。查 `/api/admin/dispatch/logs?rule_id=` 验证计数。

- [ ] **Step 6: 最终提交（若手验有微调）**

```bash
git add -A && git commit -m "test(dispatch): 全量验证通过"
```

---

## Self-Review

**Spec 覆盖**：4 表(T1)、仓储匹配/计数(T2)、count/ratio/溢出/兜底引擎(T3)、毕业预分配+resolve_op_handler 消费(T4)、admin CRUD(T5)、前端(T6)、验证(T7)。8 项决策全覆盖：①毕业触发→T4；②独立规则→T1；③④多维引擎+溢出→T1/T3；⑤dispatch_log→T1/T2；⑥按天重置→T2 today_counts；⑦op_handler_user_id→T1/T4；⑧不打断自动答复(op_handler 仍 'agent')→T4 Step4 注释+不改 drain 口径。✓

**占位符扫描**：所有代码步骤含完整代码；前端 T6 Step3 描述性较多但给了对照文件与结构（前端组件难逐行硬写，指明模板文件 PeopleScopesPage）。✓

**类型一致**：`dispatch_operation_handler` / `DispatchResult` / `resolve_op_handler(db, hub, settings)` / `DispatchRepository` 方法名在 T2/T3/T4 间一致；`op_handler_user_id` 全程一致；迁移 revision `0029_dispatch_engine` 与模型一致。✓

**已知风险**：T5 fixture 名（admin_token/member_token）需实测对齐 conftest —— 已在 Task 5 注明先 grep 对齐。
