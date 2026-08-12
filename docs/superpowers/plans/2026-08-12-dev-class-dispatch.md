# 研发类工单走多维分派引擎 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让研发类（Bug_fix/Demand）工单毕业时也走多维分派引擎，分派人覆盖 `assigned_user_id`（供 Linear 推送做 team 路由 + assignee），分派无结果转 pending 人工队列。

**Architecture:** 分派引擎（`dispatch/engine.py`）逻辑 type 无关，直接复用——仅重命名 `dispatch_operation_handler` → `dispatch_handler`。`creator.py` 毕业逻辑把分派触发从 `Operation-only` 放开到 `Operation/Bug_fix/Demand`：Operation 写 `op_handler_user_id`，研发类写 `assigned_user_id`；研发类分派无结果时 `HubIssueResult.dispatch_missed=True`，auto 路径据此转 pending 而非推 Linear。

**Tech Stack:** FastAPI + SQLAlchemy，pytest（unit，SQLite in-memory StaticPool），ruff。

## Global Constraints

- 无数据库迁移（不加表/列；`dispatch_missed` 是内存 dataclass 字段，`HubIssueResult` 为 `frozen=True`，新字段必须带默认值 `= False`）。
- 无 API schema 变化（不改任何路由/响应模型，不需 `make gen-types`）。
- 分派引擎本身零逻辑改动，仅重命名符号。
- 单测在 `backend/` 目录下用 `.venv/bin/pytest` 跑；lint 用 `.venv/bin/ruff check` + `.venv/bin/ruff format`。
- 分派选出的人：Operation → `op_handler_user_id`；Bug_fix/Demand → `assigned_user_id`（覆盖入库责任人）。两类都调 `set_hub_tickets_handler` 让处理人流动到关联工单。
- 降级：研发类分派无结果，仅 **auto 路径**（`create_hub_issue_for_ticket_auto`）转 `status='pending'`；手动毕业不降级。

---

## File Structure

- `backend/app/services/dispatch/engine.py` — 重命名 `dispatch_operation_handler` → `dispatch_handler`（函数体不动）。
- `backend/app/services/dispatch/__init__.py` — 更新导出。
- `backend/app/services/hub_issues/creator.py` — `HubIssueResult` 加 `dispatch_missed` 字段；毕业分派块放开到研发类；`create_hub_issue_for_ticket_auto` 降级分流；新增 `_mark_dispatch_pending`。
- `backend/tests/unit/services/test_dispatch_engine.py` — 现有 Operation 分派单测（重命名回归）。
- `backend/tests/unit/services/test_hub_issue_creator_dispatch.py` — 新建：研发类分派 + 降级单测。

---

### Task 1: 重命名分派引擎入口 `dispatch_operation_handler` → `dispatch_handler`

**Files:**
- Modify: `backend/app/services/dispatch/engine.py`
- Modify: `backend/app/services/dispatch/__init__.py`
- Modify: `backend/app/services/hub_issues/creator.py`（调用点，约 162-164 行）

**Interfaces:**
- Produces: `dispatch_handler(db: Session, hub: HubIssue) -> DispatchResult`（签名/返回不变，仅改名）。`DispatchResult` 字段不变：`user_id: int|None, user_name: str|None, rule_id: int|None, tier: str|None, reason: str`。

- [ ] **Step 1: 先确认现有分派单测跑通（重命名前基线）**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_dispatch_engine.py -v`
Expected: PASS（若文件名不同，用 `rg -l "dispatch_operation_handler" tests/` 定位）

- [ ] **Step 2: 在 engine.py 重命名函数定义**

`backend/app/services/dispatch/engine.py` 约 82 行：

```python
def dispatch_handler(db: Session, hub: HubIssue) -> DispatchResult:
    """选处理人（Operation 运营 / 研发类均可）。任异常吞掉返回 _NONE。"""
```

同时更新 84-125 行函数体内 `except` 块的日志 key（约 124 行）：

```python
        logger.exception("dispatch_handler_failed", hub_issue_id=getattr(hub, "id", None))
```

- [ ] **Step 3: 更新 `__init__.py` 导出**

`backend/app/services/dispatch/__init__.py` 全文替换为：

```python
"""处理人分派引擎（Operation 运营 + 研发类共用）。"""

from app.services.dispatch.engine import DispatchResult, dispatch_handler

__all__ = ["DispatchResult", "dispatch_handler"]
```

- [ ] **Step 4: 更新 creator.py 调用点**

`backend/app/services/hub_issues/creator.py` 约 162-164 行，把 import 和调用从 `dispatch_operation_handler` 改为 `dispatch_handler`（本 Task 只改名，逻辑分流在 Task 3 做）：

```python
    if issue_type == "Operation":
        from app.services.dispatch import dispatch_handler

        dr = dispatch_handler(db, hub)
```

- [ ] **Step 5: 全仓搜索残留旧名**

Run: `cd backend && rg -n "dispatch_operation_handler" app/ tests/`
Expected: 无输出（全部改完）。若测试文件里有引用，一并改名。

- [ ] **Step 6: 跑分派单测 + lint 验证重命名无回归**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_dispatch_engine.py -q && .venv/bin/ruff check app/services/dispatch/ app/services/hub_issues/creator.py`
Expected: PASS + All checks passed

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/dispatch/ backend/app/services/hub_issues/creator.py backend/tests/
git commit -m "refactor(dispatch): 重命名 dispatch_operation_handler → dispatch_handler

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `HubIssueResult` 加 `dispatch_missed` 字段

**Files:**
- Modify: `backend/app/services/hub_issues/creator.py`（`HubIssueResult` dataclass，约 44-50 行）

**Interfaces:**
- Produces: `HubIssueResult.dispatch_missed: bool = False`（frozen dataclass 新字段，带默认值，不破坏现有 4 处构造）。

- [ ] **Step 1: 加字段**

`backend/app/services/hub_issues/creator.py` 约 44-50 行：

```python
@dataclass(slots=True, frozen=True)
class HubIssueResult:
    hub_issue_id: int
    hub_issue_short_code: str
    ticket_id: int
    type: str
    created: bool  # False when the ticket was already linked
    dispatch_missed: bool = False  # 研发类分派无匹配处理人（auto 路径据此转 pending）
```

- [ ] **Step 2: 验证 import 与现有构造不报错**

Run: `cd backend && .venv/bin/python -c "from app.services.hub_issues.creator import HubIssueResult; r = HubIssueResult(1,'HUB-1',2,'Bug_fix',True); print(r.dispatch_missed)"`
Expected: 打印 `False`（默认值生效，现有位置参数构造不受影响）

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/hub_issues/creator.py
git commit -m "feat(hub): HubIssueResult 加 dispatch_missed 字段

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: 毕业分派块放开到研发类（TDD）

**Files:**
- Modify: `backend/app/services/hub_issues/creator.py`（分派块，约 161-170 行）
- Test: `backend/tests/unit/services/test_hub_issue_creator_dispatch.py`（新建）

**Interfaces:**
- Consumes: `dispatch_handler`（Task 1）、`HubIssueResult.dispatch_missed`（Task 2）、`set_hub_tickets_handler(db, hub, user_id) -> int`（现有）。
- Produces: `ensure_hub_issue_for_ticket` 对 Bug_fix/Demand 命中分派时写 `hub.assigned_user_id`；无结果时返回的 `HubIssueResult.dispatch_missed=True`。

- [ ] **Step 1: 写失败测试（研发类命中分派 → 覆盖 assigned_user_id）**

新建 `backend/tests/unit/services/test_hub_issue_creator_dispatch.py`。先看现有分派测试怎么造 DispatchRule/DispatchAssignee 种子数据（`rg -n "DispatchRule\(|DispatchAssignee\(" backend/tests` 找骨架复用），本测试用同样方式建一条命中当前 hub 的规则：

```python
"""研发类工单走分派引擎单测。"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models import (
    DispatchAssignee,
    DispatchRule,
    Source,
    Ticket,
    User,
)
from app.services.hub_issues.creator import ensure_hub_issue_for_ticket


def _seed_user(db: Session, name: str) -> User:
    u = User(name=name, email=f"{name}@x.com", role="assignee", is_active=True)
    db.add(u)
    db.flush()
    return u


def _seed_dispatch_rule(db: Session, assignee_user_id: int) -> DispatchRule:
    """一条 match-all（空维度全通配）count 规则，命中任意 hub。"""
    rule = DispatchRule(
        name="all",
        priority=1,
        is_active=True,
        match_sources=[],
        match_product_lines=[],
        match_modules=[],
        match_sla=[],
        dispatch_mode="count",
    )
    db.add(rule)
    db.flush()
    db.add(
        DispatchAssignee(
            rule_id=rule.id, user_id=assignee_user_id, tier="main",
            alloc_value=1, daily_cap=None, is_active=True,
        )
    )
    db.flush()
    return rule


def _seed_classified_ticket(db: Session, *, ptype: str, reporter_uid: int) -> Ticket:
    if db.query(Source).filter_by(code="ksm").first() is None:
        db.add(Source(code="ksm", name="KSM"))
    t = Ticket(
        short_code=f"TKT-{ptype}",
        source_code="ksm",
        source_ticket_id="ksm-1",
        type="Raw",
        status="received",
        title="开票报错",
        body="点击开票提示系统异常",
        predicted_type=ptype,
        predicted_confidence=0.95,
        assigned_user_id=reporter_uid,  # 入库责任人
    )
    db.add(t)
    db.flush()
    return t


@pytest.mark.parametrize("ptype", ["Bug_fix", "Demand"])
def test_dev_class_dispatch_overrides_assigned_user(db_session: Session, ptype: str) -> None:
    """研发类命中分派 → assigned_user_id 被分派人覆盖（不再是入库责任人）。"""
    reporter = _seed_user(db_session, "reporter")
    handler = _seed_user(db_session, "handler")
    _seed_dispatch_rule(db_session, handler.id)
    t = _seed_classified_ticket(db_session, ptype=ptype, reporter_uid=reporter.id)
    db_session.commit()

    result = ensure_hub_issue_for_ticket(t.id, created_by="agent:hub_issue_auto", db=db_session)

    assert result.created is True
    assert result.dispatch_missed is False
    from app.models import HubIssue
    hub = db_session.get(HubIssue, result.hub_issue_id)
    assert hub.assigned_user_id == handler.id  # 分派人覆盖了 reporter
    assert hub.op_handler_user_id is None  # 研发类不写 op_handler_user_id
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_hub_issue_creator_dispatch.py::test_dev_class_dispatch_overrides_assigned_user -v`
Expected: FAIL（现状研发类不走分派，`hub.assigned_user_id` 仍是 reporter.id）

- [ ] **Step 3: 改分派块放开到研发类**

`backend/app/services/hub_issues/creator.py` 把现有分派块（约 161-170 行）替换为：

```python
    # 毕业分派：按多维规则选处理人（Operation 运营 + 研发类共用规则/人池）。
    # Operation → op_handler_user_id（op_handler 名保持 'agent' 不打断 drain）；
    # Bug_fix/Demand → 覆盖 assigned_user_id（Linear 推送用它做 team 路由 + assignee）。
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
            else:
                hub.assigned_user_id = dr.user_id  # 研发类：覆盖入库责任人
            set_hub_tickets_handler(db, hub, dr.user_id)
        elif issue_type in ("Bug_fix", "Demand"):
            dispatch_missed = True
```

然后把函数末尾 `created=True` 的那个 `return HubIssueResult(...)`（约 198-204 行）加上 `dispatch_missed`：

```python
    return HubIssueResult(
        hub_issue_id=hub.id,
        hub_issue_short_code=hub.short_code,
        ticket_id=ticket.id,
        type=issue_type,
        created=True,
        dispatch_missed=dispatch_missed,
    )
```

（早返回的两处——already-linked / dedup-merged——不动，`dispatch_missed` 用默认 `False`。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_hub_issue_creator_dispatch.py::test_dev_class_dispatch_overrides_assigned_user -v`
Expected: PASS（Bug_fix 和 Demand 两个参数化都过）

- [ ] **Step 5: 写「分派无结果 → dispatch_missed=True」测试**

在同文件追加：

```python
def test_dev_class_dispatch_missed_sets_flag(db_session: Session) -> None:
    """研发类无匹配规则+无兜底 → dispatch_missed=True，assigned_user_id 保持入库责任人。"""
    reporter = _seed_user(db_session, "reporter2")
    # 不建任何 DispatchRule → 分派无结果
    t = _seed_classified_ticket(db_session, ptype="Bug_fix", reporter_uid=reporter.id)
    db_session.commit()

    result = ensure_hub_issue_for_ticket(t.id, created_by="agent:hub_issue_auto", db=db_session)

    assert result.dispatch_missed is True
    from app.models import HubIssue
    hub = db_session.get(HubIssue, result.hub_issue_id)
    assert hub.assigned_user_id == reporter.id  # 无分派 → 保持责任人不变
```

- [ ] **Step 6: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_hub_issue_creator_dispatch.py -v`
Expected: 3 passed（2 参数化 + 1 missed）

- [ ] **Step 7: Operation 回归测试（确认仍写 op_handler_user_id 不写 assigned_user_id 覆盖）**

在同文件追加：

```python
def test_operation_dispatch_writes_op_handler_not_override(db_session: Session) -> None:
    """Operation 命中分派 → 写 op_handler_user_id，assigned_user_id 保持入库责任人。"""
    reporter = _seed_user(db_session, "reporter3")
    handler = _seed_user(db_session, "ophandler")
    _seed_dispatch_rule(db_session, handler.id)
    t = _seed_classified_ticket(db_session, ptype="Operation", reporter_uid=reporter.id)
    db_session.commit()

    result = ensure_hub_issue_for_ticket(t.id, created_by="agent:hub_issue_auto", db=db_session)

    from app.models import HubIssue
    hub = db_session.get(HubIssue, result.hub_issue_id)
    assert hub.op_handler_user_id == handler.id
    assert hub.assigned_user_id == reporter.id  # Operation 不覆盖责任人
    assert result.dispatch_missed is False  # Operation 无结果也不设 missed
```

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_hub_issue_creator_dispatch.py -v`
Expected: 4 passed

- [ ] **Step 8: lint + commit**

Run: `cd backend && .venv/bin/ruff check app/services/hub_issues/creator.py tests/unit/services/test_hub_issue_creator_dispatch.py && .venv/bin/ruff format app/services/hub_issues/creator.py tests/unit/services/test_hub_issue_creator_dispatch.py`

```bash
git add backend/app/services/hub_issues/creator.py backend/tests/unit/services/test_hub_issue_creator_dispatch.py
git commit -m "feat(dispatch): 研发类毕业走分派引擎，分派人覆盖 assigned_user_id

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: auto 路径分派无结果转 pending（TDD）

**Files:**
- Modify: `backend/app/services/hub_issues/creator.py`（`create_hub_issue_for_ticket_auto` 约 227-233 行；新增 `_mark_dispatch_pending`）
- Test: `backend/tests/unit/services/test_hub_issue_creator_dispatch.py`（追加）

**Interfaces:**
- Consumes: `HubIssueResult.dispatch_missed`（Task 3）、`push_hub_issue_to_linear`、`_mark_pending_review`（现有）、`StatusHistoryRepository`（现有 import）。
- Produces: `_mark_dispatch_pending(hub_issue_id: int) -> None`（置 status='pending' + status_history，自开 session）。

- [ ] **Step 1: 写失败测试（auto 路径 dispatch_missed → pending，不推 Linear）**

在 `test_hub_issue_creator_dispatch.py` 追加。参考现有对 `create_hub_issue_for_ticket_auto` 的测试写法（`rg -n "create_hub_issue_for_ticket_auto" backend/tests` 找 mock push 的骨架）：

```python
from unittest.mock import patch

from app.services.hub_issues.creator import create_hub_issue_for_ticket_auto


def test_auto_dispatch_missed_marks_pending_no_linear(db_session: Session, monkeypatch) -> None:
    """auto 路径研发类分派无结果 → status=pending，不调 push_hub_issue_to_linear。"""
    reporter = _seed_user(db_session, "reporter4")
    t = _seed_classified_ticket(db_session, ptype="Bug_fix", reporter_uid=reporter.id)
    db_session.commit()
    ticket_id = t.id

    # create_hub_issue_for_ticket_auto 自开 session，用 monkeypatch 让它复用测试 session
    monkeypatch.setattr(
        "app.services.hub_issues.creator.make_session", lambda: db_session
    )
    # 防 session 被内部 close 掉影响断言
    monkeypatch.setattr(db_session, "close", lambda: None)

    with patch(
        "app.services.hub_issues.linear_push.push_hub_issue_to_linear"
    ) as mock_push:
        result = create_hub_issue_for_ticket_auto(ticket_id)

    assert result is not None and result.dispatch_missed is True
    mock_push.assert_not_called()
    from app.models import HubIssue
    hub = db_session.get(HubIssue, result.hub_issue_id)
    assert hub.status == "pending"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_hub_issue_creator_dispatch.py::test_auto_dispatch_missed_marks_pending_no_linear -v`
Expected: FAIL（现状 dispatch_missed 不分流，会走 pending_review 或 push）

- [ ] **Step 3: 改 auto 路径分流 + 加 `_mark_dispatch_pending`**

`backend/app/services/hub_issues/creator.py` `create_hub_issue_for_ticket_auto` 里 push/pending_review 分流（约 227-233 行）改为：

```python
    if result.created and result.type in ("Bug_fix", "Demand"):
        if result.dispatch_missed:
            # 分派无匹配处理人 → 转人工（复用 pending 队列），不进 pending_review、不推 Linear
            _mark_dispatch_pending(result.hub_issue_id)
        elif get_settings().require_review_before_linear:
            # agent 自动毕业的研发类 → 进 pending_review 待主管确认，不自动推 Linear
            _mark_pending_review(result.hub_issue_id)
        else:
            push_hub_issue_to_linear(result.hub_issue_id)
    return result
```

在 `_mark_pending_review`（约 236 行）后新增：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_hub_issue_creator_dispatch.py::test_auto_dispatch_missed_marks_pending_no_linear -v`
Expected: PASS

- [ ] **Step 5: 写「auto 路径命中 + review 开 → pending_review 不误入 pending」测试**

追加：

```python
def test_auto_dispatch_hit_with_review_goes_pending_review(db_session: Session, monkeypatch) -> None:
    """auto 路径命中分派 + review 开 → pending_review（不误判为 dispatch pending）。"""
    reporter = _seed_user(db_session, "reporter5")
    handler = _seed_user(db_session, "handler5")
    _seed_dispatch_rule(db_session, handler.id)
    t = _seed_classified_ticket(db_session, ptype="Bug_fix", reporter_uid=reporter.id)
    db_session.commit()
    ticket_id = t.id

    monkeypatch.setattr("app.services.hub_issues.creator.make_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(
        "app.services.hub_issues.creator.get_settings",
        lambda: type("S", (), {"require_review_before_linear": True, "hub_dedup_enabled": False})(),
    )

    with patch("app.services.hub_issues.linear_push.push_hub_issue_to_linear") as mock_push:
        result = create_hub_issue_for_ticket_auto(ticket_id)

    assert result.dispatch_missed is False
    mock_push.assert_not_called()
    from app.models import HubIssue
    hub = db_session.get(HubIssue, result.hub_issue_id)
    assert hub.status == "pending_review"
```

注意：若 `get_settings` mock 干扰 `ensure_hub_issue_for_ticket` 内的 `hub_dedup_enabled` 读取，改为只 patch `require_review_before_linear` 的最小 settings stub（保留 `hub_dedup_enabled=False` 防查重路径）。跑不过时按实际 settings 字段补齐 stub。

- [ ] **Step 6: 跑全文件测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_hub_issue_creator_dispatch.py -v`
Expected: 6 passed

- [ ] **Step 7: lint + commit**

Run: `cd backend && .venv/bin/ruff check app/services/hub_issues/creator.py tests/unit/services/test_hub_issue_creator_dispatch.py && .venv/bin/ruff format app/services/hub_issues/creator.py tests/unit/services/test_hub_issue_creator_dispatch.py`

```bash
git add backend/app/services/hub_issues/creator.py backend/tests/unit/services/test_hub_issue_creator_dispatch.py
git commit -m "feat(dispatch): 研发类分派无结果 auto 路径转 pending 人工队列

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: linear_push 用分派人回归测试 + 全量验证

**Files:**
- Test: `backend/tests/unit/services/`（复用现有 linear_push 测试骨架，新增或扩充一条）

**Interfaces:**
- Consumes: `push_hub_issue_to_linear`、`hub.assigned_user_id`（分派后 = 分派人）。验证 push 请求的 team/assignee 取自分派人的 Linear 映射。

- [ ] **Step 1: 找现有 linear_push 测试骨架**

Run: `cd backend && rg -ln "push_hub_issue_to_linear|CreateIssueRequest" tests/`
Expected: 定位现有 `test_linear_push*.py`。阅读其 mock LinearClient + 造 User(linear_user_id/linear_team_id) 的方式。

- [ ] **Step 2: 写测试（assigned_user_id=分派人时，push 用其 Linear 映射）**

在现有 linear_push 测试文件追加一条：造 hub（Bug_fix, `assigned_user_id` 指向一个有 `linear_user_id`+`linear_team_id` 的 User，模拟已被分派），断言 mock client 收到的 `CreateIssueRequest.assignee_id == 该 user.linear_user_id`、`team_id == 该 user.linear_team_id`。

（此测试验证 spec「分派人同时推给 Linear」——因 `linear_push.py:141` 已用 `assigned_user_id`，无需改生产代码，仅回归确认。）

```python
def test_push_uses_dispatched_assignee(db_session, ...):
    # 复用文件内现有的 _seed_hub / fake client helper
    dispatched = _seed_user_with_linear(db_session, linear_user_id="lu-1", linear_team_id="team-x")
    hub = _seed_hub(db_session, type="Bug_fix", assigned_user_id=dispatched.id)
    db_session.commit()
    fake = FakeLinearClient()  # 文件内既有
    push_hub_issue_to_linear(hub.id, db=db_session, client=fake)
    assert fake.last_request.assignee_id == "lu-1"
    assert fake.last_request.team_id == "team-x"
```

按现有测试文件的 helper 命名调整（`_seed_user_with_linear` / `FakeLinearClient` / `last_request` 用实际名）。

- [ ] **Step 3: 跑该测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_linear_push.py -v`（文件名按实际）
Expected: PASS（生产代码无需改，验证既有行为）

- [ ] **Step 4: 全量单测 + lint（回归确认无破坏）**

Run: `cd backend && .venv/bin/pytest -q && .venv/bin/ruff check app/ tests/ && .venv/bin/ruff format --check app/services/dispatch/ app/services/hub_issues/creator.py`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add backend/tests/
git commit -m "test(dispatch): linear_push 用分派人做 assignee/team 回归

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- 研发类走分派、共用规则/人池 → Task 3 ✅
- 分派人覆盖 assigned_user_id → Task 3 ✅
- 自动毕业时分派 → Task 3（`ensure_hub_issue_for_ticket` 内）✅
- 分派人推 Linear → Task 5 回归（linear_push 已用 assigned_user_id，无需改）✅
- 无结果转 pending（复用队列）→ Task 4 ✅
- 手动毕业不降级 → Task 4（`_mark_dispatch_pending` 只在 auto 路径）✅
- 引擎重命名 → Task 1 ✅
- Operation 回归不变 → Task 3 Step 7 ✅

**Placeholder scan:** Task 5 的 helper 名依赖现有测试文件（已注明「按实际名调整」+ 给出定位命令），非占位符——是对既有代码的适配指令。其余步骤均有完整代码。

**Type consistency:** `dispatch_handler` 签名 Task 1↔3 一致；`HubIssueResult.dispatch_missed` Task 2 定义、Task 3 写入、Task 4 读取一致；`_mark_dispatch_pending` Task 4 定义与调用一致。

## 部署提醒（实现完成后）

- 无迁移、无 gen-types。
- ⚠️ **上线前必须确认 SIT/生产已配覆盖研发类的分派规则**，否则研发工单全部 dispatch_missed → 堆进 pending 人工队列。建议先 SIT 配规则 → 部署 → 观察研发类正确分派+推 Linear → 再上生产。
- SIT 前端无关（本改动纯后端）；重启 3 个 systemd（api/worker/beat）。
