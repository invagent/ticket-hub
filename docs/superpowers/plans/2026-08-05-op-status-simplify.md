# Operation 状态机简化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除 Operation `op_status` 的 `resupplied` 状态（6→5），把「需补料」从「打回客户」改为「转兜底主管线下收集」，人工写答复推进状态机到 `answered`。

**Architecture:** 三处业务改动 + 一处状态常量/约束收紧 + 迁移。agent 判需补料时不再调 `request_supply` 打回客户，改置 `supplementing`/兜底主管；`POST /reply` 端点成功后追加 `apply_op_status(→answered)`；KSM 重推 supplementing 单只刷内容不转态。手动补料入口（`/request-supply`、`batch_request_supply`）全部保留。

**Tech Stack:** FastAPI + SQLAlchemy + Alembic + pytest（SQLite in-memory / StaticPool）。测试命令 `.venv/bin/pytest`，lint `make lint`。

**Spec:** `docs/superpowers/specs/2026-08-05-op-status-simplify-design.md`

## Global Constraints

- 所有命令在 `backend/` 目录下执行；测试用 `.venv/bin/pytest <path> -v`。
- 单测走 SQLite in-memory（`db_session` fixture），不需要 Docker。
- 用户已确认**生产库无 `resupplied` 存量数据**——迁移只改 CheckConstraint，不迁数据。
- op_status 变更一律经 `apply_op_status`（唯一入口），不直接赋值 `hub.op_status`。
- `apply_op_status` 不 commit，调用方负责事务边界。
- 兜底主管名统一用 `resolve_supervisor_name(db, settings)`（未配 default_pool 时返回 `"主管"`）。
- 保留的手动补料入口：`POST /request-supply`（`hub_issues.py`）、`batch_request_supply`（`supervisor.py`）——本次不动。

---

### Task 1: 收紧状态常量与 CheckConstraint + 迁移

删除 `OP_RESUPPLIED` 常量、约束去掉 `'resupplied'`、新建迁移。这是所有后续任务的地基（其他任务的 import 依赖此常量已删）。

**Files:**
- Modify: `backend/app/services/hub_issues/op_status.py:22-31`
- Modify: `backend/app/models.py:501-503`
- Create: `backend/migrations/versions/0026_op_status_drop_resupplied.py`
- Test: `backend/tests/unit/test_models_op_status.py`

**Interfaces:**
- Produces: `op_status.py` 不再导出 `OP_RESUPPLIED`；`_VALID = {processing, answered, closed, supplementing, exception}`。

- [ ] **Step 1: 改约束单测（先让它反映新约束）**

打开 `backend/tests/unit/test_models_op_status.py`，确认/新增一条断言 `resupplied` 被拒。若文件已有合法值集合测试，把 `resupplied` 从「合法」挪到「非法」。新增：

```python
def test_resupplied_rejected_by_constraint(db_session) -> None:
    """迁移后 op_status='resupplied' 应被 CheckConstraint 拒绝。"""
    import pytest
    from sqlalchemy.exc import IntegrityError

    from app.models import HubIssue

    hub = HubIssue(
        short_code="HUB-RSP-X", type="Operation", title="t",
        status="created", op_status="resupplied", op_handler="agent",
    )
    db_session.add(hub)
    with pytest.raises(IntegrityError):
        db_session.flush()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest tests/unit/test_models_op_status.py::test_resupplied_rejected_by_constraint -v`
Expected: FAIL（当前约束仍允许 resupplied，不抛 IntegrityError）

- [ ] **Step 3: 删常量**

`backend/app/services/hub_issues/op_status.py`：删除 `OP_RESUPPLIED = "resupplied"` 行；`_VALID` frozenset 去掉 `OP_RESUPPLIED`：

```python
_VALID = frozenset(
    {OP_PROCESSING, OP_ANSWERED, OP_CLOSED, OP_SUPPLEMENTING, OP_EXCEPTION}
)
```

- [ ] **Step 4: 改 model 约束**

`backend/app/models.py:501-503`：

```python
        CheckConstraint(
            "op_status IS NULL OR op_status IN "
            "('processing','answered','closed','supplementing','exception')",
            name="ck_hub_issues_op_status",
        ),
```

- [ ] **Step 5: 写迁移**

`backend/migrations/versions/0026_op_status_drop_resupplied.py`：

```python
"""drop resupplied from op_status check constraint

Revision ID: 0026_op_status_drop_resupplied
Revises: 0025_outbox_hub_issue_nullable
"""

from __future__ import annotations

from alembic import op

revision: str = "0026_op_status_drop_resupplied"
down_revision: str | None = "0025_outbox_hub_issue_nullable"
branch_labels: str | None = None
depends_on: str | None = None

_NEW = (
    "op_status IS NULL OR op_status IN "
    "('processing','answered','closed','supplementing','exception')"
)
_OLD = (
    "op_status IS NULL OR op_status IN "
    "('processing','answered','closed','supplementing','resupplied','exception')"
)


def upgrade() -> None:
    op.drop_constraint("ck_hub_issues_op_status", "hub_issues", type_="check")
    op.create_check_constraint("ck_hub_issues_op_status", "hub_issues", _NEW)


def downgrade() -> None:
    op.drop_constraint("ck_hub_issues_op_status", "hub_issues", type_="check")
    op.create_check_constraint("ck_hub_issues_op_status", "hub_issues", _OLD)
```

- [ ] **Step 6: 跑测试确认通过**

Run: `.venv/bin/pytest tests/unit/test_models_op_status.py -v`
Expected: PASS（约束现在拒绝 resupplied）

注：单测建表走 `models.py` metadata（非跑迁移），故 Step 4 的 model 约束改动即让测试通过；迁移用于生产库。

- [ ] **Step 7: 提交**

```bash
git add app/services/hub_issues/op_status.py app/models.py \
        migrations/versions/0026_op_status_drop_resupplied.py \
        tests/unit/test_models_op_status.py
git commit -m "feat(op_status): 删除 resupplied 状态 + 迁移 0026 收紧约束"
```

---

### Task 2: agent 判需补料转主管（不打回客户）+ drain 去 resupplied

`operation_answer.py` branch C：删 `request_supply` 调用，handler 改兜底主管；drain 扫描去掉 resupplied 分支；清理 import。

**Files:**
- Modify: `backend/app/services/agents/operation_answer.py:22`（import）, `:27`（OP_RESUPPLIED import）, `:253-267`（branch C）, `:288-320`（drain 扫描）
- Test: `backend/tests/unit/services/test_operation_answer.py:121-141`（改）, `:305-320`（改）

**Interfaces:**
- Consumes: `resolve_supervisor_name(db, settings)`（Task 1 未动，仍在 `op_status.py`）。
- Produces: branch C 后 `hub.op_status == "supplementing"`、`hub.op_handler == 兜底主管名`、**无 supply outbox 行**。

- [ ] **Step 1: 改 branch C 单测**

`test_operation_answer.py:121-141` 的 `test_auto_answer_c_requests_supply` 改名 + 改断言（不再打回客户）：

```python
def test_auto_answer_c_transfers_to_supervisor(db_session: Session) -> None:
    """route 判 C（需补料）→ 转兜底主管线下收集，不打回客户（无 supply outbox）。"""
    from app.models import SyncOutbox

    hub, _t = _seed_op_hub(db_session)
    db_session.commit()
    fake = _FakeClient(answer="需要更多信息才能定位")
    with (
        patch("app.services.agents.operation_answer.build_client", return_value=fake),
        patch(
            "app.services.agents.operation_answer._route_answer",
            return_value=AnswerRoute(branch="C", supply_note="请提供开票报错截图"),
        ),
    ):
        ok = auto_answer_operation(db_session, hub.id, settings=_S())
    assert ok is True
    db_session.refresh(hub)
    assert hub.reply_content_version == 0  # 没答复
    assert hub.op_status == "supplementing"
    assert hub.op_handler != "agent"  # 已转主管
    # 不再打回客户 → 无 supply outbox
    ob = db_session.query(SyncOutbox).filter_by(hub_issue_id=hub.id, kind="supply").first()
    assert ob is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest tests/unit/services/test_operation_answer.py::test_auto_answer_c_transfers_to_supervisor -v`
Expected: FAIL（当前代码仍调 request_supply 产生 supply outbox，`ob is None` 断言失败）

- [ ] **Step 3: 改 branch C 实现**

`operation_answer.py:253-267`，改为：

```python
    if route.branch == "C":
        # 需补料：不再打回客户。转兜底主管线下联系客户收集资料，主管拿到后
        # 人工 POST /reply 答复。supply_note 写审计供主管参考「缺什么」。
        note = (route.supply_note or "").strip()
        if not note:
            return _transfer("需补料但 supply_note 为空，降级留主管")
        apply_op_status(
            db,
            hub,
            to_status=OP_SUPPLEMENTING,
            handler=resolve_supervisor_name(db, settings),
            reason="需补料，转主管线下收集",
        )
        _record_decision(db, hub.id, branch="C", question=question, answer=answer, supply_note=note)
        logger.info("operation_auto_supply_transfer", hub_issue_id=hub.id)
        return True
```

- [ ] **Step 4: 删 request_supply import**

`operation_answer.py:22` 删除 `from app.services.cascade.supply_sync import SupplySyncError, request_supply`（确认本文件已无其他 `request_supply`/`SupplySyncError` 引用）。

- [ ] **Step 5: 改 drain 扫描单测**

`test_operation_answer.py:305` 的 `test_drain_scans_processing_agent_and_resupplied` 改名 + 去 resupplied 断言。改为只验证扫到「processing+agent」，且不扫 supplementing：

```python
def test_drain_scans_only_unprocessed_processing_agent(db_session: Session) -> None:
    """drain 只捞 op_status=processing 且 op_handler=agent（刚毕业未处理）；
    supplementing（主管收集中）不被捞。"""
    from app.services.hub_issues.op_status import OP_SUPPLEMENTING

    hub_p, _ = _seed_op_hub(db_session, op_status="processing", op_handler="agent")
    hub_s, _ = _seed_op_hub(db_session, op_status=OP_SUPPLEMENTING, op_handler="主管")
    db_session.commit()
    with (
        patch("app.services.agents.operation_answer.build_client", return_value=_FakeClient(answer="ok")),
        patch(
            "app.services.agents.operation_answer._route_answer",
            return_value=AnswerRoute(branch="D", supply_note=""),
        ),
    ):
        report = drain_operation_auto_reply(db_session, settings=_S())
    assert report.scanned == 1  # 只有 hub_p
```

注：确认 `_seed_op_hub` 支持 `op_status`/`op_handler` 关键字参数；若签名不符，按文件现有 helper 实际签名调整（读 `test_operation_answer.py` 顶部 helper 定义）。`drain_operation_auto_reply` 导入名以文件顶部实际为准。

- [ ] **Step 6: 跑测试确认失败**

Run: `.venv/bin/pytest tests/unit/services/test_operation_answer.py::test_drain_scans_only_unprocessed_processing_agent -v`
Expected: FAIL（drain 仍含 resupplied 分支，但因无 resupplied 数据可能 scanned 仍=1 通过——若通过则直接进 Step 7 改实现后回归其余测试）

- [ ] **Step 7: 改 drain 实现**

`operation_answer.py:307-320` 的 `stmt`，去掉 `or_(...)` 中的 resupplied 分支：

```python
    stmt = (
        select(HubIssue.id)
        .where(
            HubIssue.type == "Operation",
            HubIssue.deleted_at.is_(None),
            ~ai_cs_ticket,
            HubIssue.op_status == OP_PROCESSING,
            HubIssue.op_handler == "agent",
        )
        .order_by(HubIssue.id)
        .limit(settings.operation_auto_reply_batch)
    )
```

删除 `OP_RESUPPLIED` import（`operation_answer.py:27`）与 `or_`/`and_` 中已不需要的部分（若 `and_`/`or_` 无其他用处则从 `:13` import 移除）。更新 `:288-292` docstring 去掉 resupplied 描述。

- [ ] **Step 8: 跑全文件测试确认通过**

Run: `.venv/bin/pytest tests/unit/services/test_operation_answer.py -v`
Expected: PASS（含改动的两条 + 其余回归）

- [ ] **Step 9: 提交**

```bash
git add app/services/agents/operation_answer.py tests/unit/services/test_operation_answer.py
git commit -m "feat(operation): agent 判需补料转兜底主管（不打回客户）+ drain 去 resupplied"
```

---

### Task 3: KSM 重推 supplementing 单只刷内容不转态

`ksm_ingester.py`：supplementing 态收到同 billId 重推，只 `apply_content_refresh`，op_status 保持不变（不再转 resupplied）。answered 驳回分支不动。

**Files:**
- Modify: `backend/app/services/ingest/ksm_ingester.py:34`（import）, `:87-97`（supplementing 分支）
- Test: `backend/tests/unit/services/test_ksm_ingester.py:295-327`（改）

**Interfaces:**
- Consumes: `apply_content_refresh`（未动）。
- Produces: supplementing 态重推后 `hub.op_status` 仍为 `supplementing`，内容已刷新，`result.deduped is True`。

- [ ] **Step 1: 改 supplementing 重推单测**

`test_ksm_ingester.py:295` 的 `test_ingest_resupply_on_supplementing` 改名 + 改断言：

```python
def test_ingest_supplement_refresh_keeps_status(db_session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """已存在 ticket 且 hub.op_status=supplementing → 客户主动重推：只 content_refresh，op_status 保持 supplementing。"""
    from app.services.hub_issues.op_status import OP_SUPPLEMENTING
    from app.services.ingest import ksm_ingester as mod

    existing, hub = _seed_existing_with_hub(
        db_session,
        op_status=OP_SUPPLEMENTING,
        bill_id="bill-supp-1",
        short_code="TKT-SP-1",
        hub_short_code="HUB-SP-1",
    )
    prev_handler = hub.op_handler

    called: dict = {}

    def fake_refresh(db, ticket, payload):
        called["ticket_id"] = ticket.id
        called["payload"] = payload
        return True

    monkeypatch.setattr(mod, "apply_content_refresh", fake_refresh)
    ing = mod.KSMIngester(db_session, default_pool_user_id=None)
    result = ing.ingest({"billId": "bill-supp-1", "content": "新补料"})
    db_session.commit()

    assert called["ticket_id"] == existing.id
    assert called["payload"]["content"] == "新补料"
    assert result.deduped is True
    assert result.ticket_id == existing.id

    db_session.refresh(hub)
    assert hub.op_status == OP_SUPPLEMENTING  # 状态不变
    assert hub.op_handler == prev_handler     # 处理人不变
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest tests/unit/services/test_ksm_ingester.py::test_ingest_supplement_refresh_keeps_status -v`
Expected: FAIL（当前代码转 resupplied，`hub.op_status == OP_SUPPLEMENTING` 断言失败）

- [ ] **Step 3: 改实现**

`ksm_ingester.py:87-97`，去掉 `apply_op_status(→OP_RESUPPLIED)`：

```python
            if op == OP_SUPPLEMENTING:
                # 主管收集补料期间客户主动重推同 billId：只刷新内容供主管参考，
                # op_status 保持 supplementing 不变（不再转 resupplied，该状态已删）。
                assert hub is not None
                apply_content_refresh(self._db, existing, payload)
                logger.info(
                    "ksm_ingest_supplement_refresh", bill_id=bill_id, existing_ticket_id=existing.id
                )
                return self._dedup_result(existing)
```

删除 `OP_RESUPPLIED` import（`ksm_ingester.py:34`）。确认 `OP_SUPPLEMENTING`/`OP_PROCESSING`/`apply_op_status`/`resolve_supervisor_name` 仍被 answered 驳回分支使用，保留。

- [ ] **Step 4: 跑全文件测试确认通过**

Run: `.venv/bin/pytest tests/unit/services/test_ksm_ingester.py -v`
Expected: PASS（改动条 + 驳回分支 `test_ingest_reject_on_answered` 回归不变）

- [ ] **Step 5: 提交**

```bash
git add app/services/ingest/ksm_ingester.py tests/unit/services/test_ksm_ingester.py
git commit -m "feat(ksm): supplementing 单重推只刷内容不转态（resupplied 已删）"
```

---

### Task 4: 人工写答复推进 op_status → answered

`POST /{hub_issue_id}/reply` 端点：`author_reply()` 成功后追加 `apply_op_status(→answered, handler=user)`，并 commit。

**Files:**
- Modify: `backend/app/api/hub_issues.py:26`（import）, `:199-225`（端点）
- Test: `backend/tests/unit/test_hub_issue_reply_api.py:56-79`（改 e2e 断言）

**Interfaces:**
- Consumes: `author_reply(db, id, content, authored_by)`（commit 后返回 `ReplyResult`）；`apply_op_status`, `OP_ANSWERED`（Task 1 后仍存在）。
- Produces: `/reply` 成功后 `hub.op_status == "answered"`、`hub.op_handler == f"user:{name}"`。

- [ ] **Step 1: 改 e2e 单测断言**

`test_hub_issue_reply_api.py:56` 的 `test_reply_e2e`，在现有断言后追加 op_status 检查：

```python
    hub = reply_world.get(HubIssue, 90)
    reply_world.refresh(hub)
    assert hub.reply_content == "请在发票云-红字确认单中操作"
    assert hub.op_status == "answered"           # 人工答复推进状态机
    assert hub.op_handler == "user:carol"        # _bearer 默认 name=carol
    t = reply_world.get(Ticket, 300)
    reply_world.refresh(t)
    assert t.cached_reply_version == 1
    assert reply_world.query(SyncOutbox).filter_by(kind="reply").count() == 1
```

注：`reply_world` fixture 里 HubIssue 90 未设 op_status（默认 NULL）。`apply_op_status` 对 `op_status IS NULL → answered` 是有效变更，通过。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest tests/unit/test_hub_issue_reply_api.py::test_reply_e2e -v`
Expected: FAIL（当前 `/reply` 不改 op_status，`hub.op_status == "answered"` 断言失败）

- [ ] **Step 3: 改端点实现**

`hub_issues.py:26` import 补 `OP_ANSWERED, apply_op_status`：

```python
from app.services.hub_issues.op_status import (
    OP_ANSWERED,
    OP_EXCEPTION,
    OP_PROCESSING,
    apply_op_status,
)
```

`hub_issues.py:199-225` 端点，在 `author_reply(...)` 成功后、`return` 前追加：

```python
    try:
        result = author_reply(
            db, hub_issue_id, content=body.content, authored_by=f"user:{user.name}"
        )
    except ReplySyncError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    # 人工答复推进 op_status → answered（补齐与 agent 答复的对称性）。
    # author_reply 已 commit；apply_op_status 不 commit，故此处单独 commit。
    hub = db.get(HubIssue, hub_issue_id)
    if hub is not None and hub.type == "Operation":
        apply_op_status(
            db, hub, to_status=OP_ANSWERED, handler=f"user:{user.name}", reason="主管人工答复"
        )
        db.commit()

    logger.info(
        "hub_issue_reply_authored",
        ...
```

（`logger.info` 及 `return AuthorReplyResponse(...)` 保持原样。确认 `HubIssue` 已在文件顶部 import；若无则补。）

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest tests/unit/test_hub_issue_reply_api.py -v`
Expected: PASS（`test_reply_e2e` 通过 + 其余 reply/supply 测试回归不变）

- [ ] **Step 5: 提交**

```bash
git add app/api/hub_issues.py tests/unit/test_hub_issue_reply_api.py
git commit -m "feat(hub-issue): 人工写答复推进 op_status→answered"
```

---

### Task 5: 清理过时注释 + 前端徽章

后端一处过时注释；前端删 resupplied 徽章、改 supplementing 文案。

**Files:**
- Modify: `backend/app/services/ksm/writeback.py:241`（注释）
- Modify: `frontend/src/components/OpStatusBadge.tsx:12`
- 检查: 工单列表 / hub_issue 详情页 op_status 筛选项（若硬编码含 resupplied）

**Interfaces:**
- 纯清理，无新接口。

- [ ] **Step 1: 改 writeback 过时注释**

`backend/app/services/ksm/writeback.py:241`，把「由 auto_answer request_supply 入队时置」改为符合新流程的描述，例如：

```python
        # supplementing 由 auto_answer 判需补料时置（转主管线下收集，不再打回客户）。
```

（仅注释，不改逻辑。）

- [ ] **Step 2: 改前端徽章**

`frontend/src/components/OpStatusBadge.tsx:12`：删除 `resupplied` 行；`supplementing` 文案从「补充资料/等客户」类改为「补料中」（贴合「主管收集补料」语义）。示例：

```tsx
  supplementing: { label: "补料中", bg: "#fbe9d4", fg: "#a05a10", bd: "#eec99a" },
  // resupplied 行删除
```

- [ ] **Step 3: 搜前端硬编码 resupplied 选项**

Run（在仓库根）: `grep -rn "resupplied" frontend/src`
Expected: 除已删的徽章外无残留；若筛选下拉硬编码含 resupplied 选项，一并删除。

- [ ] **Step 4: 前端类型检查**

Run（在 `frontend/`）: `npm run type-check`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/services/ksm/writeback.py ../frontend/src/components/OpStatusBadge.tsx
# 若改了筛选项，一并 add
git commit -m "chore: 清理 resupplied 残留（注释 + 前端徽章）"
```

---

### Task 6: 全量回归 + lint

**Files:** 无（验证任务）

- [ ] **Step 1: 后端全量单测**

Run（在 `backend/`）: `.venv/bin/pytest -v`
Expected: 全绿。重点关注 `test_operation_answer.py` / `test_ksm_ingester.py` / `test_hub_issue_reply_api.py` / `test_op_status.py` / `test_models_op_status.py`。

- [ ] **Step 2: grep 确认无 resupplied 残留（后端）**

Run（在 `backend/`）: `grep -rn "resupplied\|RESUPPLIED" app/`
Expected: 无输出。

- [ ] **Step 3: lint**

Run（在 `backend/`）: `make lint`
Expected: PASS（ruff + mypy，确认无未用 import——尤其 `operation_answer.py` 的 `and_`/`or_`、`ksm_ingester.py` 的 `OP_RESUPPLIED` 已清）。

- [ ] **Step 4: 迁移可跑性验证**

Run（在 `backend/`，需本地 PG）: `.venv/bin/alembic upgrade head && .venv/bin/alembic downgrade -1 && .venv/bin/alembic upgrade head`
Expected: 0026 up/down/up 均无错。若本地无 PG，跳过并在交付说明中标注需上线前验证。

- [ ] **Step 5: 提交（若有 lint 自动修复产生的改动）**

```bash
git add -A
git commit -m "chore: op_status 简化全量回归 + lint" || echo "无额外改动"
```

---

## Self-Review

**Spec coverage:**
- 删 resupplied 状态 → Task 1 ✅
- supplementing 重定义（agent 转主管不打回）→ Task 2 ✅
- 人工 /reply 推进 answered → Task 4 ✅
- 驳回 reject+1 保持 → Task 3 明确不动 answered 分支（回归验证）✅
- 补料回写机制保留（agent 不自动触发）→ Task 2 删调用、手动入口未动，Global Constraints 声明 ✅
- supplementing 客户主动重推只刷内容 → Task 3 ✅
- 迁移只改约束不迁数据 → Task 1 Step 5 ✅
- 过时注释 + 前端徽章 → Task 5 ✅

**Placeholder scan:** 无 TBD/TODO；测试代码均为实际可运行片段；helper 签名不确定处已标注「以文件实际为准」并给出核对方法。

**Type consistency:** `OP_ANSWERED`/`OP_SUPPLEMENTING`/`OP_PROCESSING`/`apply_op_status`/`resolve_supervisor_name` 全程一致；迁移 revision id `0026_op_status_drop_resupplied`，down_revision `0025_outbox_hub_issue_nullable`（经 `ls migrations/versions` 确认为当前 head）。
