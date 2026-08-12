# AI 全自动链路 + 可开关人工审核闸门 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 AI 全自动链路的三个关键动作前加可独立开关的人工审核闸门（分类确认 / 答复确认[已有] / 推 Linear 确认），并修正分派引擎语义（写 handler 不覆盖责任人），推 Linear 默认用模块负责人可改选。

**Architecture:** 三道闸门各一个独立配置开关。闸门①（分类确认）把现有 `pending_review` 队列从"仅研发类"扩到全类型；闸门③（推 Linear 确认）新增 `pending_linear_review` 状态 + 确认端点，assignee 默认取 `assignment_scopes_module` 的模块负责人、可手动改选。分派引擎改为只写 `handler_user_id`（回退前一改动对 `assigned_user_id` 的覆盖）。

**Tech Stack:** FastAPI + SQLAlchemy，pytest（unit，SQLite StaticPool），ruff，前端 Vite+React+TS（TanStack Query）。

## Global Constraints

- `hub_issues.status` 是 `String(32)` **无 CHECK 约束**（models.py:401）→ 新增 `pending_linear_review` 状态**无需迁移**。
- 三个独立开关：`gate_classify_enabled`（未显式设置回落 `require_review_before_linear`）、`operation_answer_accuracy_mode`（已有，闸门②）、`gate_linear_push_enabled`。
- 分派引擎写 `handler_user_id`（处理人），**不覆盖 `assigned_user_id`**（责任人）——回退 `2026-08-12-dev-class-dispatch-design.md` 的覆盖决策。
- 推 Linear assignee 优先级：确认时手选 `assignee_user_id` > 模块负责人（`AssignmentScopeRepository.find_user_ids_by_module`）> 现有责任人回落 > 默认 team 无 assignee。
- 后端命令在 `backend/` 下用 `.venv/bin/pytest`、`.venv/bin/ruff`。迁移目录 `backend/migrations/versions`（本计划预计无迁移）。
- API schema 变化后须 `make gen-types`（根目录）。
- 提交信息结尾：`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- 现有可复用：`AssignmentScopeRepository.find_user_ids_by_module(product_line_code, module) -> list[int]`（assignment_scope.py:22）；`confirm-classification`/`reclassify` 端点（supervisor.py:1854/1886）；`push_hub_issue_to_linear`（linear_push.py:90）；`dispatch_handler`（dispatch/engine.py:82）。

---

## File Structure

- `backend/app/config.py` — 加 `gate_classify_enabled`、`gate_linear_push_enabled`；`gate_classify_enabled` 回落 `require_review_before_linear`。
- `backend/app/services/hub_issues/creator.py` — 分派改写 handler 不覆盖责任人；auto 路径闸门①全类型 pending_review。
- `backend/app/services/hub_issues/module_owner.py`（新）— `resolve_module_owner`。
- `backend/app/services/hub_issues/linear_push.py` — assignee 来源优先级（手选/模块负责人/责任人）。
- `backend/app/api/supervisor.py` — 分类确认队列扩全类型；confirm-classification 按类型分流；新增 pending-linear-review 队列 + confirm-linear-push 端点。
- `backend/tests/unit/services/` + `backend/tests/unit/api/` — 各任务测试。
- `frontend/src/pages/workbench/WorkbenchPage.tsx` — 三 tab 固定显示；分类队列全类型；新增待推 Linear 队列卡片。
- `frontend/src/api/openapi.json` + `types.ts` — gen-types。

---

### Task 1: 配置开关 `gate_classify_enabled` + `gate_linear_push_enabled`

**Files:**
- Modify: `backend/app/config.py`（约 170-177 行，`hub_issue_auto_*` / `require_review_before_linear` 附近）
- Test: `backend/tests/unit/test_config.py`

**Interfaces:**
- Produces: `Settings.gate_classify_enabled: bool`（未显式设置时等于 `require_review_before_linear`）、`Settings.gate_linear_push_enabled: bool = True`。

- [ ] **Step 1: 写失败测试**

`backend/tests/unit/test_config.py` 追加：

```python
def test_gate_classify_falls_back_to_require_review() -> None:
    from app.config import Settings
    # 未显式设 gate_classify_enabled 时，回落 require_review_before_linear
    s = Settings(require_review_before_linear=False)
    assert s.gate_classify_enabled is False
    s2 = Settings(require_review_before_linear=True)
    assert s2.gate_classify_enabled is True

def test_gate_classify_explicit_overrides_fallback() -> None:
    from app.config import Settings
    s = Settings(require_review_before_linear=True, gate_classify_enabled=False)
    assert s.gate_classify_enabled is False

def test_gate_linear_push_default_on() -> None:
    from app.config import Settings
    assert Settings().gate_linear_push_enabled is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/test_config.py -k gate -v`
Expected: FAIL（字段未定义）

- [ ] **Step 3: 实现**

`backend/app/config.py`：在 `require_review_before_linear` 定义之后加。因为回落逻辑依赖另一字段，用 pydantic v2 的 `model_validator(mode="after")` 或把 `gate_classify_enabled` 设为 `bool | None = None` + property。查现有 config 用的 pydantic 版本与写法（`rg -n "model_validator|@validator|BaseSettings|class Settings" backend/app/config.py`），按现有风格实现。推荐实现：

```python
    # 闸门③：研发类推 Linear 前停 pending_linear_review 待处理人确认（默认开）
    gate_linear_push_enabled: bool = True

    # 闸门①：全类型毕业后停 pending_review 待确认分类（默认 None → 回落 require_review_before_linear）
    gate_classify_enabled: bool | None = None
```

加一个 after-validator 或 property 把 None 解析成 `require_review_before_linear`。若用 property，字段改名 `_gate_classify_enabled_raw` 并暴露 `gate_classify_enabled` property。**优先用 model_validator(mode="after") 把 None 就地填成 require_review_before_linear 的值**，保持 `s.gate_classify_enabled` 是 bool：

```python
    @model_validator(mode="after")
    def _resolve_gate_classify(self) -> "Settings":
        if self.gate_classify_enabled is None:
            object.__setattr__(self, "gate_classify_enabled", self.require_review_before_linear)
        return self
```

（`object.__setattr__` 因 BaseSettings 可能 frozen；若非 frozen 直接赋值。测试里 `s.gate_classify_enabled` 断言 bool，故 resolve 后类型是 bool——测试的 type 注解无所谓，值对即可。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/test_config.py -k gate -v`
Expected: 3 passed

- [ ] **Step 5: lint + commit**

Run: `cd backend && .venv/bin/ruff check app/config.py tests/unit/test_config.py && .venv/bin/ruff format app/config.py tests/unit/test_config.py`

```bash
git add backend/app/config.py backend/tests/unit/test_config.py
git commit -m "feat(config): 加 gate_classify_enabled/gate_linear_push_enabled 闸门开关

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: 分派引擎写 handler 不覆盖责任人（回退前一改动）

**Files:**
- Modify: `backend/app/services/hub_issues/creator.py`（分派块，约 161-175 行）
- Test: `backend/tests/unit/services/test_hub_issue_creator_dispatch.py`（改现有断言）

**Interfaces:**
- Produces: 研发类命中分派 → 写 `hub.handler_user_id`（+ `set_hub_tickets_handler`），**不再写 `hub.assigned_user_id`**。`assigned_user_id` 保持入库责任人。`dispatch_missed` 语义不变（研发类无匹配仍 True）。

- [ ] **Step 1: 改现有测试断言（反映新语义）**

现有 `test_dev_class_dispatch_overrides_assigned_user` 断言 `hub.assigned_user_id == handler.id`。改为断言分派写 handler、**不动** assigned_user_id：

打开 `backend/tests/unit/services/test_hub_issue_creator_dispatch.py`，把该测试改名 `test_dev_class_dispatch_writes_handler_not_assigned` 并改断言：

```python
@pytest.mark.parametrize("ptype", ["Bug_fix", "Demand"])
def test_dev_class_dispatch_writes_handler_not_assigned(db_session: Session, ptype: str) -> None:
    """研发类命中分派 → 写 handler_user_id；assigned_user_id 保持入库责任人不变。"""
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
    assert hub.handler_user_id == handler.id       # 分派写 handler
    assert hub.assigned_user_id == reporter.id     # 责任人不被覆盖
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_hub_issue_creator_dispatch.py::test_dev_class_dispatch_writes_handler_not_assigned -v`
Expected: FAIL（现状写的是 assigned_user_id，且可能无 handler_user_id）

- [ ] **Step 3: 改 creator.py 分派块**

`backend/app/services/hub_issues/creator.py` 约 168-172 行，把研发类分支从写 `assigned_user_id` 改为写 `handler_user_id`：

```python
        dr = dispatch_handler(db, hub)
        if dr.user_id is not None:
            if issue_type == "Operation":
                hub.op_handler_user_id = dr.user_id
            else:
                hub.handler_user_id = dr.user_id  # 研发类：写处理人，不覆盖责任人
            set_hub_tickets_handler(db, hub, dr.user_id)
        elif issue_type in ("Bug_fix", "Demand"):
            dispatch_missed = True
```

确认 `HubIssue.handler_user_id` 字段存在（`rg -n "handler_user_id" backend/app/models.py`）；若 hub 无此列而只在 ticket 层，则写 `set_hub_tickets_handler` 已够，去掉 `hub.handler_user_id=` 行并调整断言到 ticket 层。**先 grep 确认 HubIssue 是否有 handler_user_id 列**，据此定断言目标。

- [ ] **Step 4: 跑测试确认通过 + 相关回归**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_hub_issue_creator_dispatch.py -v`
Expected: 全绿（含 missed / operation-regression 用例；operation 用例断言不变）

- [ ] **Step 5: lint + commit**

Run: `cd backend && .venv/bin/ruff check app/services/hub_issues/creator.py tests/unit/services/test_hub_issue_creator_dispatch.py && .venv/bin/ruff format app/services/hub_issues/creator.py tests/unit/services/test_hub_issue_creator_dispatch.py`

```bash
git add backend/app/services/hub_issues/creator.py backend/tests/unit/services/test_hub_issue_creator_dispatch.py
git commit -m "fix(dispatch): 分派写 handler_user_id 不覆盖 assigned_user_id

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `resolve_module_owner` 模块负责人查询

**Files:**
- Create: `backend/app/services/hub_issues/module_owner.py`
- Test: `backend/tests/unit/services/test_module_owner.py`

**Interfaces:**
- Produces: `resolve_module_owner(db: Session, product_line_code: str | None, module: str | None) -> User | None` — 查 `AssignmentScopeRepository.find_user_ids_by_module`，返回第一个有效（未删除、active）User；查不到或无有效返回 None。

- [ ] **Step 1: 写失败测试**

`backend/tests/unit/services/test_module_owner.py`：

```python
"""模块负责人查询单测。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import AssignmentScopeModule, ProductLine, User
from app.services.hub_issues.module_owner import resolve_module_owner


def _seed_user(db, name, active=True):
    u = User(name=name, email=f"{name}@x.com", feishu_uid=f"ou_{name}",
             role="assignee", is_active=active)
    db.add(u); db.flush(); return u


def _seed_scope(db, uid, pl="发票云", mod="开票"):
    if db.query(ProductLine).filter_by(code=pl).first() is None:
        db.add(ProductLine(code=pl, name=pl))
        db.flush()
    db.add(AssignmentScopeModule(user_id=uid, product_line_code=pl, module=mod))
    db.flush()


def test_resolve_module_owner_hit(db_session: Session) -> None:
    u = _seed_user(db_session, "owner1")
    _seed_scope(db_session, u.id)
    db_session.commit()
    got = resolve_module_owner(db_session, "发票云", "开票")
    assert got is not None and got.id == u.id


def test_resolve_module_owner_miss_returns_none(db_session: Session) -> None:
    assert resolve_module_owner(db_session, "发票云", "不存在模块") is None


def test_resolve_module_owner_none_inputs(db_session: Session) -> None:
    assert resolve_module_owner(db_session, None, None) is None


def test_resolve_module_owner_skips_inactive(db_session: Session) -> None:
    u = _seed_user(db_session, "owner_inactive", active=False)
    _seed_scope(db_session, u.id, mod="收票")
    db_session.commit()
    assert resolve_module_owner(db_session, "发票云", "收票") is None
```

（先 grep `User` 必填字段确认 `_seed_user` 构造对，如 feishu_uid/role 约束——按 test_hub_issue_creator_dispatch.py 里已验证过的 `_seed_user` 写法对齐。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_module_owner.py -v`
Expected: FAIL（module_owner 模块不存在）

- [ ] **Step 3: 实现**

`backend/app/services/hub_issues/module_owner.py`：

```python
"""模块负责人查询：产品线+模块 → assignment_scopes_module 的负责人 User。

推 Linear 时的默认 assignee 来源。查不到或无有效用户返回 None，由调用方回落。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import User
from app.repositories.assignment_scope import AssignmentScopeRepository


def resolve_module_owner(
    db: Session, product_line_code: str | None, module: str | None
) -> User | None:
    if not product_line_code or not module:
        return None
    uids = AssignmentScopeRepository(db).find_user_ids_by_module(product_line_code, module)
    for uid in uids:
        u = db.get(User, uid)
        if u is not None and u.deleted_at is None and u.is_active:
            return u
    return None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_module_owner.py -v`
Expected: 4 passed

- [ ] **Step 5: lint + commit**

Run: `cd backend && .venv/bin/ruff check app/services/hub_issues/module_owner.py tests/unit/services/test_module_owner.py && .venv/bin/ruff format app/services/hub_issues/module_owner.py tests/unit/services/test_module_owner.py`

```bash
git add backend/app/services/hub_issues/module_owner.py backend/tests/unit/services/test_module_owner.py
git commit -m "feat(hub): resolve_module_owner 查模块负责人

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: 闸门① 全类型分类确认 + auto 路径分流

**Files:**
- Modify: `backend/app/services/hub_issues/creator.py`（`create_hub_issue_for_ticket_auto` 约 227-241 行 + `_mark_pending_review`）
- Test: `backend/tests/unit/services/test_hub_issue_creator_dispatch.py`（追加）

**Interfaces:**
- Consumes: `Settings.gate_classify_enabled`（Task 1）、`HubIssueResult.dispatch_missed`。
- Produces: `gate_classify_enabled=True` 时，auto 路径**所有类型**（Operation/Bug_fix/Demand/Internal_task）毕业后置 `pending_review`，不自动分流（不答复、不推 Linear、不 dispatch_pending）。`gate_classify_enabled=False` 时保持现状分流。

- [ ] **Step 1: 写失败测试（闸门开：Operation 也停 pending_review 不进答复链）**

追加到 `test_hub_issue_creator_dispatch.py`（复用现有 monkeypatch make_session 模式）：

```python
def test_gate_classify_on_operation_stays_pending_review(db_session, monkeypatch) -> None:
    """闸门①开：Operation 毕业也停 pending_review，不进自动答复链。"""
    reporter = _seed_user(db_session, "rep_op_gate")
    t = _seed_classified_ticket(db_session, ptype="Operation", reporter_uid=reporter.id)
    db_session.commit()
    ticket_id = t.id
    monkeypatch.setattr("app.services.hub_issues.creator.make_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(
        "app.services.hub_issues.creator.get_settings",
        lambda: type("S", (), {"gate_classify_enabled": True, "hub_dedup_enabled": False,
                               "require_review_before_linear": True,
                               "gate_linear_push_enabled": True})(),
    )
    result = create_hub_issue_for_ticket_auto(ticket_id)
    from app.models import HubIssue
    hub = db_session.get(HubIssue, result.hub_issue_id)
    assert hub.status == "pending_review"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_hub_issue_creator_dispatch.py::test_gate_classify_on_operation_stays_pending_review -v`
Expected: FAIL（现状 Operation 不进 pending_review）

- [ ] **Step 3: 改 auto 路径闸门①**

`backend/app/services/hub_issues/creator.py` `create_hub_issue_for_ticket_auto`。当前逻辑只对 Bug_fix/Demand 分流。改为：闸门①开时**所有类型**先 pending_review，闸门关时才走现状分流。

先读该函数完整体（约 207-256 行）确认现有分支，然后改为：

```python
    if not result.created:
        return result
    settings = get_settings()
    if settings.gate_classify_enabled:
        # 闸门①：全类型毕业后停 pending_review 待处理人确认分类，不自动分流
        _mark_pending_review(result.hub_issue_id)
        return result
    # 闸门①关：现状自动分流
    if result.type in ("Bug_fix", "Demand"):
        if result.dispatch_missed:
            _mark_dispatch_pending(result.hub_issue_id)
        else:
            push_hub_issue_to_linear(result.hub_issue_id)
    # Operation 自动答复链由 drain 扫描（不在此处触发），Internal_task 无动作
    return result
```

注意：Operation 的自动答复本就由 Celery drain 扫 `op_status=processing/agent` 触发，不在 creator 里调。闸门①开时 hub 停 pending_review、op_status 未进 processing，drain 自然扫不到——符合预期。确认 `_mark_pending_review` 不依赖 type（现有实现只置 status，通用）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_hub_issue_creator_dispatch.py::test_gate_classify_on_operation_stays_pending_review -v`
Expected: PASS

- [ ] **Step 5: 写闸门关的回归测试（保持现状分流）**

追加：闸门①关 + 研发类命中 → push 被调；Operation 关 → 不置 pending_review（走 drain）。

```python
def test_gate_classify_off_devclass_pushes(db_session, monkeypatch) -> None:
    reporter = _seed_user(db_session, "rep_off"); handler = _seed_user(db_session, "h_off")
    _seed_dispatch_rule(db_session, handler.id)
    t = _seed_classified_ticket(db_session, ptype="Bug_fix", reporter_uid=reporter.id)
    db_session.commit(); ticket_id = t.id
    monkeypatch.setattr("app.services.hub_issues.creator.make_session", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(
        "app.services.hub_issues.creator.get_settings",
        lambda: type("S", (), {"gate_classify_enabled": False, "hub_dedup_enabled": False,
                               "require_review_before_linear": False,
                               "gate_linear_push_enabled": False})(),
    )
    from unittest.mock import patch
    with patch("app.services.hub_issues.linear_push.push_hub_issue_to_linear") as mp:
        create_hub_issue_for_ticket_auto(ticket_id)
    mp.assert_called_once()
```

- [ ] **Step 6: 跑全文件 + 确认无回归**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_hub_issue_creator_dispatch.py tests/unit/services/test_hub_issue_creator.py -v`
Expected: 全绿（现有 `test_auto_bugfix_gated_to_pending_review` 等仍过——它们此前基于 require_review_before_linear，现回落进 gate_classify_enabled，行为一致）

- [ ] **Step 7: lint + commit**

Run: `cd backend && .venv/bin/ruff check app/services/hub_issues/creator.py tests/unit/services/test_hub_issue_creator_dispatch.py && .venv/bin/ruff format app/services/hub_issues/creator.py tests/unit/services/test_hub_issue_creator_dispatch.py`

```bash
git add backend/app/services/hub_issues/creator.py backend/tests/unit/services/test_hub_issue_creator_dispatch.py
git commit -m "feat(gate): 闸门① 全类型毕业停 pending_review 待确认分类

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: confirm-classification 按类型分流 + 闸门③ 触发

**Files:**
- Modify: `backend/app/api/supervisor.py`（`confirm_classification` 约 1854-1883、`reclassify` 约 1886+、`list_pending_review` 约 1767）
- Test: `backend/tests/unit/api/test_supervisor_classification.py`（新建或追加现有 supervisor 测试文件）

**Interfaces:**
- Consumes: `Settings.gate_linear_push_enabled`（Task 1）、`push_hub_issue_to_linear`、`apply_op_status`。
- Produces: `confirm-classification` 确认后按 `hub.type` 分流——Operation → op_status=processing/agent（进答复链）；Bug_fix/Demand → 若 `gate_linear_push_enabled` 置 `pending_linear_review`（不推），否则直接 push；Internal_task → created。分类确认队列 `GET /pending-review` 去掉"仅研发类"过滤。

- [ ] **Step 1: 找现有 supervisor 分类测试骨架**

Run: `cd backend && rg -ln "confirm_classification|confirm-classification|pending_review" tests/`
读现有测试怎么造 authed supervisor client + pending_review hub。复用其骨架。

- [ ] **Step 2: 写失败测试（确认 Operation → 进答复链；确认 Bug_fix + 闸门③开 → pending_linear_review）**

在 supervisor 分类测试文件追加（按现有 client fixture 命名调整）：

```python
def test_confirm_operation_enters_answer_chain(client, supervisor_headers, seed_pending_review_hub):
    hub = seed_pending_review_hub(type="Operation")
    r = client.post("/api/supervisor/confirm-classification",
                    json={"hub_issue_id": hub.id}, headers=supervisor_headers)
    assert r.status_code == 200
    # Operation 确认后 op_status=processing/agent（进 drain 答复链）
    # 读回 hub 断言 op_status == 'processing'、op_handler == 'agent'、status == 'created'

def test_confirm_bugfix_gate_on_goes_pending_linear_review(client, supervisor_headers, seed_pending_review_hub, monkeypatch):
    monkeypatch.setattr("app.api.supervisor.get_settings",
        lambda: _settings_stub(gate_linear_push_enabled=True))
    hub = seed_pending_review_hub(type="Bug_fix")
    r = client.post("/api/supervisor/confirm-classification",
                    json={"hub_issue_id": hub.id}, headers=supervisor_headers)
    assert r.status_code == 200
    # 断言 hub.status == 'pending_linear_review'，未推 Linear（linear_uuid still None）
```

（`_settings_stub` / fixture 按现有测试实际写法适配；如现有测试直接改 settings singleton，用同法。）

- [ ] **Step 3: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/api/test_supervisor_classification.py -v`（文件名按实际）
Expected: FAIL

- [ ] **Step 4: 改 confirm_classification 按类型分流**

`backend/app/api/supervisor.py` `confirm_classification`（1854）当前无条件 `status='created'` + push Linear。改为按 type：

```python
    hub = _get_pending_review_hub(db, body.hub_issue_id)
    prev = hub.status
    settings = get_settings()
    if hub.type == "Operation":
        hub.status = "created"
        apply_op_status(db, hub, to_status=OP_PROCESSING, handler="agent",
                        reason="确认分类，进自动答复链")
        _record_history(db, hub, prev, "created", user, "主管确认分类(Operation)")
        db.commit()
    elif hub.type in ("Bug_fix", "Demand"):
        if settings.gate_linear_push_enabled:
            hub.status = "pending_linear_review"
            _record_history(db, hub, prev, "pending_linear_review", user, "确认分类，待确认推 Linear")
            db.commit()
        else:
            hub.status = "created"
            _record_history(db, hub, prev, "created", user, "确认分类，推 Linear")
            db.commit()
            background_tasks.add_task(push_hub_issue_to_linear, hub.id)
    else:  # Internal_task
        hub.status = "created"
        _record_history(db, hub, prev, "created", user, "确认分类(Internal_task)")
        db.commit()
```

（`_record_history` 是示意——复用现有 `StatusHistoryRepository(db).record(...)` + `record_ticket_action` 写法，别引入不存在的 helper。`OP_PROCESSING`/`apply_op_status` 已在 reclassify 里 import，确认顶部 import 齐。）

- [ ] **Step 5: 改 reclassify 同样按新类型分流**

`reclassify`（1886）现有：改判成 Operation 已回炉答复链。补 Bug_fix/Demand 分支——改判成研发类时，若 `gate_linear_push_enabled` 置 `pending_linear_review`，否则 created + push。读现有 reclassify 1921-1949 段落，在其类型分支里加研发类处理，镜像 Step 4 的分流。

- [ ] **Step 6: 分类确认队列去掉"仅研发类"过滤**

`list_pending_review`（约 1767）：现在注释说"研发类(Bug_fix/Demand)"。检查其 query 是否有 `type.in_(['Bug_fix','Demand'])` 过滤，若有则删除（只按 `status=='pending_review'` 过滤，全类型返回）。更新 docstring。

- [ ] **Step 7: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/api/test_supervisor_classification.py -v`
Expected: PASS

- [ ] **Step 8: lint + commit**

Run: `cd backend && .venv/bin/ruff check app/api/supervisor.py tests/unit/api/test_supervisor_classification.py && .venv/bin/ruff format app/api/supervisor.py tests/unit/api/test_supervisor_classification.py`

```bash
git add backend/app/api/supervisor.py backend/tests/
git commit -m "feat(gate): confirm-classification 按类型分流 + 研发类进 pending_linear_review

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: 闸门③ 待推 Linear 队列 + confirm-linear-push 端点

**Files:**
- Modify: `backend/app/api/supervisor.py`（新增两端点 + response models）
- Modify: `backend/app/services/hub_issues/linear_push.py`（assignee 来源优先级）
- Test: `backend/tests/unit/api/test_supervisor_linear_review.py`（新）+ `backend/tests/unit/services/test_linear_push.py`（追加）

**Interfaces:**
- Consumes: `resolve_module_owner`（Task 3）、`push_hub_issue_to_linear`（改造后接受 override assignee）。
- Produces:
  - `GET /api/supervisor/pending-linear-review` → 列 `status=='pending_linear_review'` 的研发类 hub，每条附默认模块负责人（`resolve_module_owner`）+ 其 Linear 映射状态。
  - `POST /api/supervisor/confirm-linear-push` body `{hub_issue_id, assignee_user_id?}` → 确认推送：assignee 用手选或模块负责人 → push。
  - `push_hub_issue_to_linear(hub_issue_id, db=None, *, client=None, assignee_override_user_id: int | None = None)` — 新增 override 参数。

- [ ] **Step 1: 改 linear_push assignee 来源优先级（先测）**

`backend/tests/unit/services/test_linear_push.py` 追加：override assignee 时用 override 的 linear 映射；无 override 时用模块负责人；都无回落责任人。复用现有 fake client 骨架。

```python
def test_push_uses_assignee_override(db_session, ...):
    override_u = _seed_user_with_linear(db_session, linear_user_id="lu-ovr", linear_team_id="team-ovr")
    hub = _make_hub(..., type="Bug_fix", product_line_code="发票云", module="开票")  # 责任人另设
    fake = _FakeLinearClient()
    push_hub_issue_to_linear(hub.id, db_session, client=fake, assignee_override_user_id=override_u.id)
    assert fake.requests[0].assignee_id == "lu-ovr"
    assert fake.requests[0].team_id == "team-ovr"
```

- [ ] **Step 2: 跑确认失败** → `push_hub_issue_to_linear` 无 `assignee_override_user_id` 参数。

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_linear_push.py -k override -v`
Expected: FAIL

- [ ] **Step 3: 实现 override 参数**

`backend/app/services/hub_issues/linear_push.py` `push_hub_issue_to_linear` 签名加 `assignee_override_user_id: int | None = None`。在解析 assignee 处（约 139-157 行），优先级改为：override_user_id > 现有 assigned_user_id 逻辑。即：

```python
        assignee_user = None
        if assignee_override_user_id is not None:
            assignee_user = db.get(User, assignee_override_user_id)
        elif hub.assigned_user_id is not None:
            assignee_user = db.get(User, hub.assigned_user_id)
        # 下面沿用现有 assignee_user 的 pending / team 路由逻辑（把 hub.assigned_user_id 读取
        # 换成 assignee_user 变量），保持"个人在 Linear 查无此人→pending"等行为不变
```

改造现有逻辑用 `assignee_user` 变量替代直接读 `hub.assigned_user_id`。保持 group（无 email）降级、individual 查无此人 pending 的行为。

- [ ] **Step 4: 跑确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_linear_push.py -v`
Expected: 全绿（含现有用例）

- [ ] **Step 5: 写队列 + 确认端点测试**

`backend/tests/unit/api/test_supervisor_linear_review.py`：
- `GET /pending-linear-review` 返回 pending_linear_review 的 hub + 默认模块负责人
- `POST /confirm-linear-push` 无 assignee → 用模块负责人 push；带 assignee_user_id → 用手选 push；push 后 hub.status 从 pending_linear_review → created（或 pending 若查无 Linear）

- [ ] **Step 6: 实现两端点**

`backend/app/api/supervisor.py` 加：

```python
class PendingLinearReviewItem(BaseModel):
    hub_issue_id: int
    short_code: str
    title: str
    type: str
    product_line_code: str | None
    module: str | None
    default_assignee_user_id: int | None
    default_assignee_name: str | None
    default_assignee_in_linear: bool

class PendingLinearReviewResponse(BaseModel):
    items: list[PendingLinearReviewItem]

@router.get("/pending-linear-review", response_model=PendingLinearReviewResponse)
def list_pending_linear_review(_user=Depends(require_supervisor), db=Depends(get_session), limit: int = 50):
    hubs = (db.query(HubIssue)
              .filter(HubIssue.deleted_at.is_(None),
                      HubIssue.status == "pending_linear_review",
                      HubIssue.type.in_(["Bug_fix", "Demand"]))
              .order_by(HubIssue.id.desc()).limit(min(limit, 100)).all())
    items = []
    for h in hubs:
        owner = resolve_module_owner(db, h.product_line_code, h.module)
        items.append(PendingLinearReviewItem(
            hub_issue_id=h.id, short_code=h.short_code, title=h.title, type=h.type,
            product_line_code=h.product_line_code, module=h.module,
            default_assignee_user_id=owner.id if owner else None,
            default_assignee_name=owner.name if owner else None,
            default_assignee_in_linear=bool(owner and owner.linear_user_id),
        ))
    return PendingLinearReviewResponse(items=items)

class ConfirmLinearPushBody(BaseModel):
    hub_issue_id: int
    assignee_user_id: int | None = None

@router.post("/confirm-linear-push", response_model=ClassificationActionResponse)
def confirm_linear_push(body: ConfirmLinearPushBody, background_tasks: BackgroundTasks,
                        user=Depends(require_supervisor), db=Depends(get_session)):
    hub = db.get(HubIssue, body.hub_issue_id)
    if hub is None or hub.status != "pending_linear_review":
        raise HTTPException(409, detail="hub 非 pending_linear_review，不可确认推送")
    assignee = body.assignee_user_id
    if assignee is None:
        owner = resolve_module_owner(db, hub.product_line_code, hub.module)
        assignee = owner.id if owner else None
    prev = hub.status
    hub.status = "created"
    StatusHistoryRepository(db).record(entity_type="hub_issue", entity_id=hub.id,
        from_status=prev, to_status="created", changed_by=f"user:{user.name}",
        reason="确认推送 Linear")
    record_ticket_action(db, hub, action="confirm_linear_push",
        changed_by=f"user:{user.name}", reason="确认推 Linear")
    db.commit()
    background_tasks.add_task(push_hub_issue_to_linear, hub.id,
                             assignee_override_user_id=assignee)
    return ClassificationActionResponse(hub_issue_id=hub.id, status=hub.status, type=hub.type)
```

确认顶部 import：`resolve_module_owner`、`ClassificationActionResponse`（已有）、`record_ticket_action`（已有）。`push_hub_issue_to_linear` 作为 background_task 传 kwarg——确认 BackgroundTasks 支持 kwargs（支持）。

- [ ] **Step 7: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/api/test_supervisor_linear_review.py -v`
Expected: PASS

- [ ] **Step 8: lint + commit**

Run: `cd backend && .venv/bin/ruff check app/api/supervisor.py app/services/hub_issues/linear_push.py tests/ && .venv/bin/ruff format app/api/supervisor.py app/services/hub_issues/linear_push.py`

```bash
git add backend/app/api/supervisor.py backend/app/services/hub_issues/linear_push.py backend/tests/
git commit -m "feat(gate): 闸门③ 待推Linear队列 + confirm-linear-push(默认模块负责人可改选)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: 后端全量验证 + gen-types

**Files:**
- Generate: `frontend/src/api/openapi.json` + `types.ts`

- [ ] **Step 1: 后端全量单测**

Run: `cd backend && .venv/bin/pytest -q`
Expected: 全绿（1 个预存在 GLM 网络 flake 除外——`test_glm_client::test_network_error`，与本改动无关；若有其它失败必须修）

- [ ] **Step 2: 全量 lint**

Run: `cd backend && .venv/bin/ruff check app/ tests/ && .venv/bin/ruff format --check app/services/hub_issues/ app/api/supervisor.py app/config.py`
Expected: All checks passed

- [ ] **Step 3: gen-types（API schema 变了：新增端点）**

Run: `cd /Users/junill/Documents/04_claude/01_ticket/hub-issue && make gen-types 2>&1 | tail -5`
Expected: openapi.json + types.ts 重新生成

- [ ] **Step 4: commit**

```bash
git add frontend/src/api/openapi.json frontend/src/api/types.ts
git commit -m "chore(api): gen-types 同步 pending-linear-review/confirm-linear-push 端点

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: 前端三 tab（分类确认扩全类型 + 待推 Linear 新队列）

**Files:**
- Modify: `frontend/src/pages/workbench/WorkbenchPage.tsx`
- Test: 若有前端测试则加；否则靠 type-check + build

**Interfaces:**
- Consumes: `GET /pending-review`（全类型）、`POST /confirm-classification`、`POST /reclassify`、`GET /pending-linear-review`、`POST /confirm-linear-push`。

- [ ] **Step 1: 读现有 WorkbenchPage 结构**

读 `frontend/src/pages/workbench/WorkbenchPage.tsx`，理解现有 reviewing 队列（Tab2 已有）+ pending_review 分类确认队列（Tab1 现有，需去研发类限制的文案/逻辑）的组织方式。确认现有 tab 机制（若无 tab，看是分 section 展示）。

- [ ] **Step 2: 三 tab 固定显示**

按现有组件风格实现三个固定 tab：
- Tab1「待确认分类」：拉 `/pending-review`，卡片显示 AI 建议类型 + 置信度，确认/改判按钮（复用现有分类确认卡片，去掉"仅研发类"文案，全类型展示）
- Tab2「待确认答复」：现有 reviewing 队列（不动）
- Tab3「待推 Linear」（新）：拉 `/pending-linear-review`，卡片显示 short_code/title/默认模块负责人 + 是否在 Linear 标识 + assignee 下拉（可改选，选项来自用户列表）+「确认推送」按钮 → `POST /confirm-linear-push`

tab 固定显示（闸门关时队列空也显示）。

- [ ] **Step 3: type-check**

Run: `cd frontend && npm run type-check`
Expected: 无错误

- [ ] **Step 4: build**

Run: `cd frontend && npm run build 2>&1 | tail -5`
Expected: built 成功

- [ ] **Step 5: 前端单测（若存在）**

Run: `cd frontend && npm run test 2>&1 | tail -10`
Expected: 通过（无相关测试则跳过，靠 type-check+build）

- [ ] **Step 6: commit**

```bash
git add frontend/src/pages/workbench/WorkbenchPage.tsx
git commit -m "feat(workbench): 三tab固定显示(分类确认全类型/答复确认/待推Linear)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- 闸门①全类型分类确认 → Task 4（后端）+ Task 5（分流）+ Task 8（前端 Tab1）✅
- 闸门②答复确认 → 已实现（review 模式），无任务 ✅
- 闸门③推 Linear 确认 + 模块负责人 + 改选 → Task 3（owner 查询）+ Task 6（队列+端点+push override）+ Task 8（Tab3）✅
- 三独立开关 → Task 1 ✅
- 分派写 handler 不覆盖责任人 → Task 2 ✅
- pending_linear_review 独立状态（无迁移，String 列）→ Task 5/6 ✅
- 三 tab 固定显示 → Task 8 ✅
- gen-types → Task 7 ✅

**Placeholder scan:** Task 5/6 的测试 fixture 名（client/supervisor_headers/seed_pending_review_hub）标注"按现有实际写法适配"并给了定位命令——是适配指令非占位符。Task 2 Step 3 有"先 grep 确认 HubIssue 是否有 handler_user_id 列"的条件分支，是必要的运行时确认。

**Type consistency:** `gate_classify_enabled`/`gate_linear_push_enabled` Task1 定义、Task4/5 消费一致；`resolve_module_owner` Task3 定义、Task6 消费签名一致；`push_hub_issue_to_linear` 的 `assignee_override_user_id` Task6 定义+调用一致；`pending_linear_review` 状态 Task5 写入、Task6 查询/流转一致。

## 部署提醒（实现完成后）

- 预计**无迁移**（status 是无约束 String 列；确认 HubIssue 无 handler_user_id 列的情况下 Task2 可能需确认字段来源）。
- API schema 变（新端点）→ 已在 Task7 gen-types。
- SIT 部署：git push → `git pull && docker compose -f deploy/docker-compose.sit.yml up -d --build` + `deploy/build-frontend.sh`。
- 开关默认值：`gate_classify_enabled` 回落 `require_review_before_linear`（SIT 现为 True）→ 上线即全类型分类确认全开；`gate_linear_push_enabled` 默认 True → 研发类推送确认全开。符合"现阶段人在中枢"。
- ⚠️ 闸门①全开后所有工单堆 pending_review，需处理人消化；`assignment_scopes_module` 模块负责人数据要尽量补全，否则 Tab3 默认负责人常空需手选。
