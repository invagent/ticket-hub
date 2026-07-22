# Operation 工单状态机重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 Operation 工单一套 6 状态的中文状态机（op_status）+ 处理人 + 驳回次数，叠加在现有底层 status/回写之上，覆盖自动答复/补料回流/客户驳回/T+7自动关闭/人工介入/处理异常。

**Architecture:** Operation 专属状态层。新增 HubIssue.op_status/op_handler/reject_count/op_status_changed_at 四字段。新增 `apply_op_status` 统一入口（仿 apply_hub_status）。驳回与补料重提统一为「重推同工单+更新内容」，靠 op_status 门控在 ksm_ingester 分流。既有 drain 改为 op_status 驱动。新增 T+7 关闭 beat + 人工重答 API。研发类工单 op_status 恒 NULL 不受影响。

**Tech Stack:** Python 3.11 / SQLAlchemy / Alembic / Celery / FastAPI / pytest（SQLite in-memory）。后端 `backend/`，命令用 `.venv/bin/...`。前端 Vite+React+TS。

## Global Constraints

- **仅 Operation**：op_status 只对 `type='Operation'` 的 hub 非空；研发类恒 NULL，行为不变。
- op_status 6 值：`processing`（处理中）/ `answered`（处理完成）/ `closed`（已关闭）/ `supplementing`（补充资料）/ `resupplied`（补充重提）/ `exception`（处理异常）。
- **处理异常只给系统故障**（replay 超时 AiCsNetworkError 耗尽 / AiCsError 业务错误等意外）；业务上答不了（answer-router 判 transfer）→ 处理中 + handler=主管，不是异常。
- **处理中靠 op_handler 区分**：`agent`=自动阶段，≠agent（主管名）=人工介入阶段。
- **已关闭是硬终态**：驳回只认未关闭工单；已关闭重推=幂等 no-op。
- **T+7 自然日**（非工作日算法），从进入 answered 的 op_status_changed_at 起算。
- 主管名取 `default_pool_user_id` 对应 user.name；未配置置字面量 `'主管'`。
- 迁移目录：`backend/migrations/versions/`（最新 0020_knowledge_op_role.py，新迁移 0021）。
- Ticket.status 无 CHECK；HubIssue 现有 CHECK 见 models.py:459-491。
- lint/type 门槛：`.venv/bin/ruff check` + `.venv/bin/ruff format --check` + `.venv/bin/mypy` 必须过。
- `StatusHistoryRepository.record(*, entity_type, entity_id, from_status, to_status, changed_by, reason=None, metadata=None)`。
- 现有 `apply_supply_refill`（supply_refill.py，含"更新 source_payload + 追加 body + 同步 hub.canonical_body"）本次泛化为 `apply_content_refresh`，驳回和补料重提共用。

---

### Task 1: 模型字段 + 迁移 0021

**Files:**
- Modify: `backend/app/models.py`（HubIssue 类，reply 字段区附近 ~505-513 加 4 字段 + table_args 加 CHECK）
- Create: `backend/migrations/versions/0021_operation_status_machine.py`
- Test: `backend/tests/unit/test_models_op_status.py`

**Interfaces:**
- Produces: `HubIssue.op_status: str|None`、`HubIssue.op_handler: str|None`、`HubIssue.reject_count: int`(default 0)、`HubIssue.op_status_changed_at: datetime|None`。CHECK 约束 `ck_hub_issues_op_status`（op_status IN 6 值 OR NULL）。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/unit/test_models_op_status.py`：

```python
"""Operation op_status 字段模型测试。"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import HubIssue


def _op_hub(**kw) -> HubIssue:
    base = dict(
        short_code="HUB-OPS-1",
        type="Operation",
        title="开票失败",
        status="created",
    )
    base.update(kw)
    return HubIssue(**base)


def test_op_fields_default(db_session: Session) -> None:
    hub = _op_hub()
    db_session.add(hub)
    db_session.commit()
    db_session.refresh(hub)
    assert hub.op_status is None
    assert hub.op_handler is None
    assert hub.reject_count == 0
    assert hub.op_status_changed_at is None


def test_op_status_valid_value(db_session: Session) -> None:
    hub = _op_hub(op_status="processing", op_handler="agent")
    db_session.add(hub)
    db_session.commit()
    db_session.refresh(hub)
    assert hub.op_status == "processing"


def test_op_status_invalid_value_rejected(db_session: Session) -> None:
    hub = _op_hub(op_status="bogus")
    db_session.add(hub)
    with pytest.raises(IntegrityError):
        db_session.commit()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/pytest tests/unit/test_models_op_status.py -v`
Expected: FAIL（`TypeError: 'op_status' is an invalid keyword` 或字段不存在）

- [ ] **Step 3: 加字段 + CHECK**

在 `app/models.py` HubIssue 的 reply 字段区（`reply_updated_at` 之后，`# Bug_fix / Demand only` 之前）加：

```python
    # Operation 状态机（op_status 专属层，仅 Operation 非空；研发类恒 NULL）
    op_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    op_handler: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reject_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    op_status_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

在 HubIssue `__table_args__` 里（现有 CHECK 之后、Index 之前）加：

```python
        CheckConstraint(
            "op_status IS NULL OR op_status IN "
            "('processing','answered','closed','supplementing','resupplied','exception')",
            name="ck_hub_issues_op_status",
        ),
```

- [ ] **Step 4: 写迁移**

创建 `backend/migrations/versions/0021_operation_status_machine.py`（参照 0020 的 revision 链，down_revision="0020"）：

```python
"""operation status machine fields

Revision ID: 0021
Revises: 0020
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("hub_issues", sa.Column("op_status", sa.String(length=16), nullable=True))
    op.add_column("hub_issues", sa.Column("op_handler", sa.String(length=64), nullable=True))
    op.add_column(
        "hub_issues",
        sa.Column("reject_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "hub_issues",
        sa.Column("op_status_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_hub_issues_op_status",
        "hub_issues",
        "op_status IS NULL OR op_status IN "
        "('processing','answered','closed','supplementing','resupplied','exception')",
    )
    # 回填现存 Operation hub：reply_v>=1 → answered，否则 processing；handler=agent
    op.execute(
        """
        UPDATE hub_issues
        SET op_status = CASE WHEN reply_content_version >= 1 THEN 'answered' ELSE 'processing' END,
            op_handler = 'agent',
            op_status_changed_at = COALESCE(status_changed_at, created_at)
        WHERE type = 'Operation'
        """
    )


def downgrade() -> None:
    op.drop_constraint("ck_hub_issues_op_status", "hub_issues", type_="check")
    op.drop_column("hub_issues", "op_status_changed_at")
    op.drop_column("hub_issues", "reject_count")
    op.drop_column("hub_issues", "op_handler")
    op.drop_column("hub_issues", "op_status")
```

- [ ] **Step 5: 运行确认通过**

Run: `.venv/bin/pytest tests/unit/test_models_op_status.py -v`
Expected: PASS（3 passed）

- [ ] **Step 6: lint/type + 提交**

Run: `.venv/bin/ruff check app/models.py migrations/versions/0021_operation_status_machine.py tests/unit/test_models_op_status.py && .venv/bin/ruff format app/models.py migrations/versions/0021_operation_status_machine.py tests/unit/test_models_op_status.py && .venv/bin/mypy app/models.py`
Expected: pass

```bash
git add backend/app/models.py backend/migrations/versions/0021_operation_status_machine.py backend/tests/unit/test_models_op_status.py
git commit -m "feat(op-status): HubIssue 加 op_status/op_handler/reject_count 字段 + 迁移 0021"
```

---

### Task 2: apply_op_status 统一状态入口

**Files:**
- Create: `backend/app/services/hub_issues/op_status.py`
- Test: `backend/tests/unit/services/test_op_status.py`

**Interfaces:**
- Consumes: `HubIssue`（Task 1 字段）、`StatusHistoryRepository`。
- Produces: 常量 `OP_PROCESSING/OP_ANSWERED/OP_CLOSED/OP_SUPPLEMENTING/OP_RESUPPLIED/OP_EXCEPTION`（值同 spec）；`apply_op_status(db, hub, *, to_status, handler, reason=None) -> bool`——改 op_status/op_handler/op_status_changed_at + 写 status_history；幂等（to_status==当前 且 handler==当前 → no-op 返回 False）；不 commit（调用方负责）。返回 True=有变更。

- [ ] **Step 1: 写失败测试**

```python
"""apply_op_status 单测。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import HubIssue, StatusHistory
from app.services.hub_issues.op_status import (
    OP_ANSWERED,
    OP_PROCESSING,
    apply_op_status,
)


def _hub(db: Session) -> HubIssue:
    hub = HubIssue(
        short_code="HUB-OPS-A",
        type="Operation",
        title="t",
        status="created",
        op_status="processing",
        op_handler="agent",
    )
    db.add(hub)
    db.flush()
    return hub


def test_apply_changes_status_handler_and_records_history(db_session: Session) -> None:
    hub = _hub(db_session)
    db_session.commit()
    changed = apply_op_status(
        db_session, hub, to_status=OP_ANSWERED, handler="agent", reason="answered"
    )
    db_session.commit()
    assert changed is True
    db_session.refresh(hub)
    assert hub.op_status == OP_ANSWERED
    assert hub.op_status_changed_at is not None
    h = (
        db_session.query(StatusHistory)
        .filter_by(entity_type="hub_issue", entity_id=hub.id, to_status=OP_ANSWERED)
        .first()
    )
    assert h is not None


def test_apply_idempotent_noop(db_session: Session) -> None:
    hub = _hub(db_session)
    db_session.commit()
    changed = apply_op_status(
        db_session, hub, to_status=OP_PROCESSING, handler="agent"
    )
    assert changed is False


def test_apply_handler_change_only(db_session: Session) -> None:
    """同状态但换处理人（agent→主管，转人工）也算变更。"""
    hub = _hub(db_session)
    db_session.commit()
    changed = apply_op_status(
        db_session, hub, to_status=OP_PROCESSING, handler="主管", reason="转人工"
    )
    db_session.commit()
    assert changed is True
    db_session.refresh(hub)
    assert hub.op_handler == "主管"
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/pytest tests/unit/services/test_op_status.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 写实现**

创建 `backend/app/services/hub_issues/op_status.py`：

```python
"""Operation op_status 状态机统一入口（仿 status_cascade.apply_hub_status）.

改 op_status/op_handler/op_status_changed_at + 写 status_history。不 commit
（调用方负责事务边界）。映射驱动的底层动作（answered→author_reply、closed→关单
回写）不在这里做——由调用方在状态转换前后自行触发，保持本函数纯状态维护。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import HubIssue
from app.repositories.status_history import StatusHistoryRepository

logger = get_logger(__name__)

OP_PROCESSING = "processing"
OP_ANSWERED = "answered"
OP_CLOSED = "closed"
OP_SUPPLEMENTING = "supplementing"
OP_RESUPPLIED = "resupplied"
OP_EXCEPTION = "exception"

_VALID = frozenset(
    {OP_PROCESSING, OP_ANSWERED, OP_CLOSED, OP_SUPPLEMENTING, OP_RESUPPLIED, OP_EXCEPTION}
)


def apply_op_status(
    db: Session,
    hub: HubIssue,
    *,
    to_status: str,
    handler: str,
    reason: str | None = None,
) -> bool:
    """转 Operation hub 的 op_status + op_handler。幂等（状态与处理人都没变 → no-op）。
    写 status_history（entity_type='hub_issue'）。不 commit。返回 True=有变更。
    """
    if to_status not in _VALID:
        raise ValueError(f"invalid op_status: {to_status!r}")
    if hub.op_status == to_status and hub.op_handler == handler:
        return False

    prev = hub.op_status
    hub.op_status = to_status
    hub.op_handler = handler
    hub.op_status_changed_at = datetime.now(UTC)
    StatusHistoryRepository(db).record(
        entity_type="hub_issue",
        entity_id=hub.id,
        from_status=prev,
        to_status=to_status,
        changed_by=f"op:{handler}",
        reason=reason,
        metadata={"op_handler": handler},
    )
    logger.info(
        "op_status_changed",
        hub_issue_id=hub.id,
        from_status=prev,
        to_status=to_status,
        handler=handler,
    )
    return True
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/pytest tests/unit/services/test_op_status.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: lint/type + 提交**

Run: `.venv/bin/ruff check app/services/hub_issues/op_status.py tests/unit/services/test_op_status.py && .venv/bin/ruff format <同> && .venv/bin/mypy app/services/hub_issues/op_status.py`

```bash
git add backend/app/services/hub_issues/op_status.py backend/tests/unit/services/test_op_status.py
git commit -m "feat(op-status): apply_op_status 统一状态入口"
```

---

### Task 3: 毕业初始化 op_status

**Files:**
- Modify: `backend/app/services/hub_issues/creator.py`（HubIssue 构造处 ~99-108）
- Test: `backend/tests/unit/services/test_hub_creator.py`（追加；若无则新建）

**Interfaces:**
- Consumes: `OP_PROCESSING` from Task 2。
- Produces: Operation hub 毕业时 `op_status='processing', op_handler='agent', op_status_changed_at=now`；非 Operation 不设（NULL）。

- [ ] **Step 1: 写失败测试**

在 `backend/tests/unit/services/test_hub_creator.py` 追加（若文件不存在则创建，import 现有 fixtures）：

```python
def test_graduate_operation_inits_op_status(db_session) -> None:
    """Operation 毕业 → op_status=processing, handler=agent。"""
    from app.models import Source, Ticket
    from app.services.hub_issues.creator import ensure_hub_issue_for_ticket

    if db_session.query(Source).filter_by(code="ksm").first() is None:
        db_session.add(Source(code="ksm", name="KSM"))
    t = Ticket(
        short_code="TKT-GRAD-OP",
        source_code="ksm",
        source_ticket_id="grad-op-1",
        type="Raw",
        status="received",
        title="开票失败",
        body="报错",
        predicted_type="Operation",
    )
    db_session.add(t)
    db_session.commit()
    result = ensure_hub_issue_for_ticket(t.id, created_by="user:test", db=db_session, type_override="Operation")
    db_session.commit()
    from app.models import HubIssue

    hub = db_session.get(HubIssue, result.hub_issue_id)
    assert hub.op_status == "processing"
    assert hub.op_handler == "agent"
    assert hub.op_status_changed_at is not None


def test_graduate_bugfix_no_op_status(db_session) -> None:
    """研发类毕业 → op_status 恒 NULL。"""
    from app.models import HubIssue, Source, Ticket
    from app.services.hub_issues.creator import ensure_hub_issue_for_ticket

    if db_session.query(Source).filter_by(code="ksm").first() is None:
        db_session.add(Source(code="ksm", name="KSM"))
    t = Ticket(
        short_code="TKT-GRAD-BUG",
        source_code="ksm",
        source_ticket_id="grad-bug-1",
        type="Raw",
        status="received",
        title="崩溃",
        body="报错",
        predicted_type="Bug_fix",
    )
    db_session.add(t)
    db_session.commit()
    result = ensure_hub_issue_for_ticket(t.id, created_by="user:test", db=db_session, type_override="Bug_fix")
    db_session.commit()
    hub = db_session.get(HubIssue, result.hub_issue_id)
    assert hub.op_status is None
```

> 注：`ensure_hub_issue_for_ticket` 的确切签名（是否有 type_override 参数）以实际代码为准；实现者先读 `creator.py:55` 的签名，测试按实际调法调整。

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/pytest tests/unit/services/test_hub_creator.py -v -k op_status`
Expected: FAIL（op_status 为 None，未初始化）

- [ ] **Step 3: 写实现**

在 `creator.py` HubIssue 构造处（`status="created",` 那行附近）——只在 Operation 时设。因为 HubIssue(...) 是一次性构造，用条件表达式：

在 `hub = HubIssue(...)` 构造里加（issue_type 已知）：

```python
        status="created",
        op_status=OP_PROCESSING if issue_type == "Operation" else None,
        op_handler="agent" if issue_type == "Operation" else None,
        op_status_changed_at=datetime.now(UTC) if issue_type == "Operation" else None,
```

顶部 import：`from app.services.hub_issues.op_status import OP_PROCESSING`（+ 确认 `datetime, UTC` 已 import，否则加 `from datetime import UTC, datetime`）。

> 注意 hub_dedup supersede 分支（creator.py:113-140）：若 supersede 到已有 hub，当前 ticket 不新建 hub，op_status 不涉及（复用原 hub 的）。无需改。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/pytest tests/unit/services/test_hub_creator.py -v -k op_status`
Expected: PASS

- [ ] **Step 5: lint/type + 提交**

```bash
git add backend/app/services/hub_issues/creator.py backend/tests/unit/services/test_hub_creator.py
git commit -m "feat(op-status): Operation 毕业初始化 op_status=processing/agent"
```

---

### Task 4: auto_answer 分支落 op_status + drain 改 op_status 驱动

**Files:**
- Modify: `backend/app/services/agents/operation_answer.py`（auto_answer_operation D/C/transfer 分支 + 异常捕获 + drain_operation_auto_reply 扫描口径）
- Test: `backend/tests/unit/services/test_operation_answer.py`（改现有断言 + 加 op_status 断言）

**Interfaces:**
- Consumes: `apply_op_status` + 常量 from Task 2。
- Produces: auto_answer_operation 各分支落 op_status：D→answered/agent、C→supplementing/agent、transfer→processing/主管、系统故障（AiCsError）→exception/主管。drain 扫描口径改为 `op_status IN ('processing' 且 op_handler='agent', 'resupplied')`（替换旧的 reply_v=0 且无审计）。

- [ ] **Step 1: 写失败测试**

改 `test_operation_answer.py`。现有 `test_auto_answer_d_sends` 加断言 `hub.op_status == 'answered'`；`test_auto_answer_c_requests_supply` 加 `hub.op_status == 'supplementing'`；`test_auto_answer_transfer_leaves_to_human` 加 `hub.op_status == 'processing' and hub.op_handler != 'agent'`；`test_auto_answer_replay_error_leaves_to_human` 加 `hub.op_status == 'exception'`。种子 hub 需带初始 `op_status='processing', op_handler='agent'`（改 `_seed_op_hub`）。

新增 drain 口径测试：

```python
def test_drain_scans_processing_agent_and_resupplied(db_session) -> None:
    """drain 扫 op_status=processing(handler=agent) 和 resupplied；排除人工介入。"""
    # 造 3 个 Operation hub：processing/agent（扫）、processing/主管（不扫）、resupplied/agent（扫）
    # 断言 scanned 只含前者和后者
    ...
```

> 实现者按现有 test_operation_answer.py 的 `_seed_op_hub` helper 扩展，给 hub 设 op_status/op_handler。

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/pytest tests/unit/services/test_operation_answer.py -v`
Expected: FAIL（op_status 断言不成立）

- [ ] **Step 3: 写实现**

顶部 import：`from app.services.hub_issues.op_status import (OP_ANSWERED, OP_EXCEPTION, OP_PROCESSING, OP_SUPPLEMENTING, apply_op_status)`。

`auto_answer_operation` 各分支——在现有动作后加 apply_op_status（主管名用 helper 取，见下）：
- D 分支（author_reply 成功后）：`apply_op_status(db, hub, to_status=OP_ANSWERED, handler="agent", reason="agent 答复成功")`
- C 分支（request_supply 后）：`apply_op_status(db, hub, to_status=OP_SUPPLEMENTING, handler="agent", reason="需补料")`
- transfer 分支：`apply_op_status(db, hub, to_status=OP_PROCESSING, handler=_supervisor_name(settings, db), reason="agent 业务无解转人工")`
- 系统故障（`except AiCsError` 分支，现在 `return False`）：先 `apply_op_status(db, hub, to_status=OP_EXCEPTION, handler=_supervisor_name(...), reason="replay 系统故障")` 再 return False。commit。

加 helper：

```python
def _supervisor_name(settings: Settings, db: Session) -> str:
    """人工介入处理人名：default_pool 对应 user.name；未配则 '主管'。"""
    uid = settings.default_pool_user_id
    if uid is not None:
        from app.models import User

        u = db.get(User, uid)
        if u is not None and u.name:
            return str(u.name)
    return "主管"
```

> 注意：`_record_decision` 现有审计可保留（transfer 分支写 branch=transfer 审计）。但 drain 口径不再依赖它（改看 op_status）。保留审计仅作历史记录。

drain 扫描口径（`drain_operation_auto_reply`）替换 `already_processed` + reply_v 那段为 op_status：

```python
    stmt = (
        select(HubIssue.id)
        .where(
            HubIssue.type == "Operation",
            HubIssue.deleted_at.is_(None),
            ~ai_cs_ticket,
            or_(
                and_(HubIssue.op_status == OP_PROCESSING, HubIssue.op_handler == "agent"),
                HubIssue.op_status == OP_RESUPPLIED,
            ),
        )
        .order_by(HubIssue.id)
        .limit(settings.operation_auto_reply_batch)
    )
```

（import `from sqlalchemy import and_, or_, select`；`OP_RESUPPLIED` 也 import。删掉旧的 `already_processed` exists 子查询。）

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/pytest tests/unit/services/test_operation_answer.py -v`
Expected: PASS

- [ ] **Step 5: lint/type + 提交**

```bash
git add backend/app/services/agents/operation_answer.py backend/tests/unit/services/test_operation_answer.py
git commit -m "feat(op-status): auto_answer 落 op_status + drain 改 op_status 驱动"
```

---

### Task 5: content_refresh 泛化 + ingester 驳回/补料重提分流

**Files:**
- Rename/Modify: `backend/app/services/ingest/supply_refill.py` → 泛化为 `content_refresh.py`（保留 apply_supply_refill 为薄封装或直接迁移调用点）
- Modify: `backend/app/services/ingest/ksm_ingester.py`（幂等分支按 op_status 门控分流）
- Test: `backend/tests/unit/services/test_content_refresh.py`（新）、`test_ksm_ingester.py`（改）

**Interfaces:**
- Consumes: `apply_op_status` + 常量。
- Produces: `apply_content_refresh(db, ticket, new_payload) -> bool`——更新 source_payload + 追加 body + 同步 hub.canonical_body（不改 op_status，由 ingester 分流后设）。ksm_ingester 幂等分支：查 existing.hub.op_status → supplementing=补料重提(content_refresh + op_status→resupplied/agent) / answered=驳回(content_refresh + op_status→processing/主管 + reject_count+1) / closed=no-op / 其他=no-op。

- [ ] **Step 1: 写失败测试**

`test_content_refresh.py`：验证 apply_content_refresh 更新 source_payload/body/canonical_body（复用现有 supply_refill 测试的断言，去掉"清 auto_reply 审计"部分——那逻辑移走）。

`test_ksm_ingester.py` 加：
```python
def test_ingest_reject_on_answered(db_session, monkeypatch) -> None:
    """已存在 ticket 且 hub.op_status=answered → 驳回：content_refresh + op_status→processing/主管 + reject_count+1。"""
    ...
def test_ingest_resupply_on_supplementing(db_session, monkeypatch) -> None:
    """hub.op_status=supplementing → 补料重提：op_status→resupplied/agent。"""
    ...
def test_ingest_noop_on_closed(db_session, monkeypatch) -> None:
    """hub.op_status=closed → 硬终态 no-op。"""
    ...
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/pytest tests/unit/services/test_content_refresh.py tests/unit/services/test_ksm_ingester.py -v -k "refresh or reject or resupply or closed"`
Expected: FAIL

- [ ] **Step 3: 写实现**

1. 把 `supply_refill.py` 内容迁到 `content_refresh.py`，函数改名 `apply_content_refresh`，**去掉**"清 auto_reply 审计"逻辑（改由 op_status 驱动），保留"更新 source_payload + body + canonical_body"。reply_v≥1 保护逻辑改为：由 ingester 分流决定，不在此判。
2. `ksm_ingester.py` 幂等分支改为（替换现有 awaiting_supply 分支）：

```python
        existing = self._tickets.find_by_source("ksm", bill_id)
        if existing is not None:
            hub = (
                self._db.get(HubIssue, existing.hub_issue_id)
                if existing.hub_issue_id
                else None
            )
            op = hub.op_status if hub else None
            if op == OP_SUPPLEMENTING:
                apply_content_refresh(self._db, existing, payload)
                apply_op_status(self._db, hub, to_status=OP_RESUPPLIED, handler="agent",
                                reason="客户补料重提")
                logger.info("ksm_ingest_resupply", bill_id=bill_id, existing_ticket_id=existing.id)
                return self._dedup_result(existing)
            if op == OP_ANSWERED:
                apply_content_refresh(self._db, existing, payload)
                hub.reject_count += 1
                apply_op_status(self._db, hub, to_status=OP_PROCESSING,
                                handler=_supervisor_name_for_ingest(self._db),
                                reason=f"客户驳回（第{hub.reject_count}次）")
                logger.info("ksm_ingest_reject", bill_id=bill_id, existing_ticket_id=existing.id,
                            reject_count=hub.reject_count)
                return self._dedup_result(existing)
            # closed（硬终态）/ 其他 → 原 no-op
            logger.info("ksm_ingest_dedup", bill_id=bill_id, existing_ticket_id=existing.id)
            return self._dedup_result(existing)
```

`_supervisor_name_for_ingest(db)`：取 default_pool user.name，未配 '主管'（可复用 Task 4 的 helper——抽到 op_status.py 作公共 `resolve_supervisor_name(db, settings=None)` 供两处 import；实现者判断放哪最合适）。

> 注意 awaiting_supply：Task 5 后不再用 ticket.status=awaiting_supply 做补料识别（改用 hub.op_status=supplementing）。但 ksm/writeback 仍在补料真发后置 ticket.awaiting_supply（Task 6 处理是否保留）。ingester 分流只看 hub.op_status。

- [ ] **Step 4: 运行确认通过 + 全 ingester 回归**

Run: `.venv/bin/pytest tests/unit/services/test_content_refresh.py tests/unit/services/test_ksm_ingester.py -v`
Expected: PASS（含既有用例无回归）

- [ ] **Step 5: lint/type + 提交**

```bash
git add backend/app/services/ingest/content_refresh.py backend/app/services/ingest/ksm_ingester.py backend/tests/unit/services/test_content_refresh.py backend/tests/unit/services/test_ksm_ingester.py
git rm backend/app/services/ingest/supply_refill.py backend/tests/unit/services/test_supply_refill.py  # 若已完全迁移
git commit -m "feat(op-status): content_refresh 泛化 + ksm ingester 驳回/补料重提分流"
```

---

### Task 6: writeback 关单/补料 op_status 衔接

**Files:**
- Modify: `backend/app/services/ksm/writeback.py`（_close_local 关单成功 → op_status=closed；_mark_awaiting_supply 复核是否保留）
- Test: `backend/tests/unit/services/test_ksm_writeback.py`

**Interfaces:**
- Consumes: `apply_op_status`。
- Produces: KSM 答复关单回写成功时，若 hub 是 Operation 且 op_status=answered → 顺带置 op_status=closed（与 T+7 beat 二选一先到者生效）。补料真发成功仍置 ticket.awaiting_supply（保留，作 ticket 层展示）但补料识别已改看 hub.op_status。

> **实现者注**：先读 spec §五映射表确认。T+7 beat（Task 7）和这里的关单都可能把 answered→closed。需幂等（apply_op_status 幂等已保证）。此 Task 若发现关单与 op_status 语义有冲突（如关单本是 hub.status=resolved，而 op_status=closed 是另一层），以「op_status=closed 表示业务已关闭，hub.status=resolved 是底层」并存处理，二者不互斥。

- [ ] **Step 1-5**: 写测试（关单成功 Operation hub → op_status=closed）→ 确认失败 → 实现（_close_local 里若 hub.type=='Operation' 调 apply_op_status closed）→ 确认通过 → lint/commit。

```bash
git commit -m "feat(op-status): KSM 关单回写衔接 op_status=closed"
```

---

### Task 7: T+7 自动关闭 Celery beat

**Files:**
- Create: `backend/app/services/agents/op_close_task.py`（或 hub_issues/ 下）
- Modify: `backend/app/services/hub_issues/op_status.py`（加 `close_overdue_answered(db, *, days=7, settings) -> int` 服务函数）
- Modify: `backend/app/celery_app.py`（include + beat_schedule 加每日任务）
- Modify: `backend/app/config.py`（加 `operation_auto_close_days: int = 7` + `operation_auto_close_enabled: bool = False`）
- Test: `backend/tests/unit/services/test_op_close.py`

**Interfaces:**
- Produces: `close_overdue_answered(db, *, settings) -> int`——扫 `op_status='answered' 且 op_status_changed_at ≤ now-N天 且 deleted_at IS NULL`，逐个 apply_op_status→closed + 触发关单回写。Celery task `close_answered_operations`（每日 beat）。

- [ ] **Step 1: 写失败测试**

```python
def test_close_overdue_answered(db_session) -> None:
    """answered 超 7 天 → closed；未超不动。"""
    # 造两个 answered hub：一个 op_status_changed_at=8天前（关），一个=1天前（不关）
    # 调 close_overdue_answered，断言前者 closed 后者 answered
    ...
```

- [ ] **Step 2-5**: 确认失败 → 实现服务函数（用 `datetime.now(UTC) - timedelta(days=settings.operation_auto_close_days)` 比 op_status_changed_at）+ celery task（own session/swallow-all，仿 operation_answer_task）+ beat（crontab 每日如 `hour=3, minute=17`）+ config → 确认通过 → lint/commit。

```bash
git commit -m "feat(op-status): T+7 自动关闭 beat（close_answered_operations 每日）"
```

---

### Task 8: 人工重答 API

**Files:**
- Modify: `backend/app/api/hub_issues.py`（加 `POST /{id}/re-answer`）
- Test: `backend/tests/unit/api/test_hub_issues_reanswer.py`

**Interfaces:**
- Consumes: `auto_answer_operation` 的答复逻辑（复用；但同步调用 + 前置校验人工介入中）、`apply_op_status`。
- Produces: `POST /api/hub-issues/{id}/re-answer`（require_supervisor 或 knowledge_op）：前置 op_status=processing 且 op_handler≠agent，否则 409。同步跑 replay+answer-router：成功→answered/agent；业务答不了→留 processing/主管；系统故障→exception/主管。返回结果 JSON。

- [ ] **Step 1: 写失败测试**

覆盖：人工介入中调用→成功答复置 answered；非人工介入（handler=agent）调用→409；答不了→留 processing。用 patch 掉 replay client。

- [ ] **Step 2-5**: 确认失败 → 实现端点（复用 auto_answer_operation 的核心答复流程；建议把 auto_answer 的"replay→route→落状态"抽成可复用内部函数，re-answer 和 drain 都调）→ 确认通过 → lint/commit。

```bash
git commit -m "feat(op-status): 人工重答 API POST /hub-issues/{id}/re-answer"
```

---

### Task 9: 前端 op_status 展示 + 重答按钮

**Files:**
- Create: `frontend/src/components/OpStatusBadge.tsx`（op_status→中文标签+色）
- Modify: `frontend/src/pages/hub-issues/HubIssuesListPage.tsx` + 详情页（展示 op_status/处理人/驳回次数 + 重答按钮）
- Modify: `frontend/src/api/`（若 openapi 类型需重生成）
- Test: 前端 `npm run type-check` + 相关 vitest（若有）

**Interfaces:**
- Consumes: 后端 op_status/op_handler/reject_count 字段（需 hub_issue 响应 schema 暴露，Task 8 前确认 API 响应含这些字段）。
- Produces: OpStatusBadge 组件（6 状态中文映射：处理中/处理完成/已关闭/补充资料/补充重提/处理异常）；列表/详情展示 + 人工介入时【重答】按钮调 re-answer API。

- [ ] **Step 1**: 确认后端 hub_issue 响应 schema 已含 op_status 等字段（若无，先在 `app/api/hub_issues.py` 的响应模型加）+ `make gen-types` 重生成前端类型。
- [ ] **Step 2**: 写 OpStatusBadge 组件（映射表）。
- [ ] **Step 3**: 列表/详情页接入展示 + 重答按钮（仅 op_status=processing 且 handler≠agent 时显示，require supervisor/knowledge_op）。
- [ ] **Step 4**: `npm run type-check` + `npm run build` 通过。
- [ ] **Step 5**: 提交 `feat(op-status): 前端 op_status 展示 + 重答按钮`。

---

### Task 10: 全套回归 + 端到端验证

- [ ] **Step 1**: 后端全套 `.venv/bin/ruff check app/ && .venv/bin/ruff format --check app/ && .venv/bin/mypy app/`。
- [ ] **Step 2**: `.venv/bin/pytest -q --deselect tests/unit/adapters/test_glm_client.py::test_network_error`（GLM network 是 pre-existing）。
- [ ] **Step 3**: 端到端串验证（临时脚本，跑完删）：毕业→processing/agent → drain 答复→answered → T+7 close_overdue→closed；毕业→C补料→supplementing →重推→resupplied →drain重答；answered →重推驳回→processing/主管 reject_count=1 →re-answer 成功→answered。
- [ ] **Step 4**: 前端 `npm run type-check && npm run build`。
- [ ] **Step 5**: `git status` 无临时残留；更新记忆（新建 op-status-machine 记忆 + MEMORY.md 指针，链接 [[ksm-supply-refill]][[operation-auto-reply-async]]）。

---

## Self-Review

**1. Spec coverage：**
- 6 状态 + 4 字段 → Task 1 ✓
- apply_op_status 统一入口 → Task 2 ✓
- 毕业初始化 → Task 3 ✓
- D/C/transfer/异常 落状态 + drain 驱动 → Task 4 ✓
- 驳回=补料重提=重推分流（content_refresh + op_status 门控）→ Task 5 ✓
- 关单 op_status 衔接 → Task 6 ✓
- T+7 自动关闭 beat → Task 7 ✓
- 人工重答 API → Task 8 ✓
- 前端展示 + 重答按钮 → Task 9 ✓
- 处理异常只给系统故障 → Task 4（异常分支 AiCsError）✓
- 主管名取 default_pool → Task 4/5 helper ✓
- 迁移回填 → Task 1 ✓
- 边界（ai_cs 排除/已关闭 no-op/重提后系统故障→异常/反复重答留人工）→ Task 4/5/8 覆盖 ✓

**2. Placeholder scan：** Task 4/5/6/7/8 部分 Step 用 `...` 表示测试体细节，但每处都给了明确的断言目标和实现锚点；Task 6/7/8 用 "Step 1-5" 压缩 TDD 循环（结构与前面任务相同，实现者按同模式展开）。这些是有意压缩而非遗漏——每个都点明了要测什么、改哪个文件哪个函数。Task 3/5 明确要求"以实际代码签名为准"。

**3. Type consistency：** `apply_op_status(db, hub, *, to_status, handler, reason=None)->bool` 全任务一致；`apply_content_refresh(db, ticket, new_payload)->bool` Task 5 定义、Task 5 调用；常量 OP_* 在 Task 2 定义，Task 3/4/5/6 import 一致；`_supervisor_name`/`resolve_supervisor_name` 在 Task 4 引入、Task 5 复用（标注抽公共）。
