# 补料回流按源分流 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Operation 工单 AI 判需补料时留在「处理中」并把需补内容写进处理说明草稿；KSM 侧补料回流打通「补料中→客户重推→处理中→AI 重答」闭环并追加记录新描述/附件；前端按来源系统显示「提交答复」/「补充资料」按钮。

**Architecture:** 复用现有草稿槽（`reply_content`/`reply_is_draft`/`reply_authored_by`）承载处理说明，不新增字段不加迁移。后端改 3 处逻辑（AI 需补料分支、KSM 补料回流分支、content_refresh 建附件）+ 2 处 API（详情暴露 draft 标志、补料/答复守卫）。前端详情页合并补料入口为双按钮并按 `source_code` 分流。

**Tech Stack:** FastAPI + SQLAlchemy（Python 3.11）、pytest（SQLite in-memory）、React 18 + TypeScript + TanStack Query + Tailwind、vitest。

## Global Constraints

- op_status 合法值（`app/services/hub_issues/op_status.py`）：`processing` / `answered` / `closed` / `supplementing` / `reviewing` / `exception`。常量 `OP_PROCESSING` 等已导出。
- drain 口径（`drain_operation_auto_reply`）：只扫 `type=Operation` + 未删 + 非 ai_cs 源 + `status=='created'` + `op_status=processing` + `op_handler=='agent'`。**AI 需补料留处理中时 handler 必须≠agent（用人工名），否则会被立刻重扫无限重答；补料回流转回时 handler 必须=agent，才能被 drain 重新接管重答。**
- 不新增数据库迁移（复用草稿槽）。
- 后端改动后若动了 API schema，必须 `make gen-types` 并提交（CI `make check-types` 会卡）。
- 单测运行：`cd backend && .venv/bin/pytest <path> -v`。全量：`make unit`（覆盖率≥70%）。
- 提交信息用中文，尾行 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- 对客出站文本（答复/补料）不过 pii_lite 遮罩。

---

### Task 1: AI 需补料分支改为留处理中 + 写处理说明草稿

把 `operation_answer.py` branch C 从「置 supplementing + 转主管」改为「留 processing（handler=人工名）+ 把需补内容写进 `reply_content` 草稿」。这样前端处理说明框有稳定数据源，人工可直接改后提交答复或点补充资料。

**Files:**
- Modify: `backend/app/services/agents/operation_answer.py`（branch C，约 343-358 行）
- Test: `backend/tests/unit/services/test_operation_answer.py`

**Interfaces:**
- Consumes: `_save_draft_reply(db, hub, *, content: str)`（已存在，130 行）、`apply_op_status(db, hub, *, to_status, handler, reason)`、`resolve_op_handler(db, hub, settings) -> str`、`_record_decision(...)`、`OP_PROCESSING`。
- Produces: branch C 结束时 hub 状态 = `op_status=processing` + `op_handler=<人工名>` + `reply_content=<supply_note>` + `reply_is_draft=True`；仍写 `auto_reply` 审计（branch=C，保留 supply_note 供详情回填兼容）。

- [ ] **Step 1: 写失败测试**

在 `test_operation_answer.py` 加（参考文件内既有 fixture 构造 Operation hub + mock replay/router 的方式）：

```python
def test_branch_c_stays_processing_with_draft(db_session, monkeypatch):
    # 构造一个 Operation hub（op_status=processing, handler=agent），mock replay 返回答复，
    # mock _route_answer 返回 branch=C + supply_note="请提供完整报错截图"
    hub = _make_operation_hub(db_session)  # 复用本文件既有 helper
    _mock_replay(monkeypatch, answer="您好，需要更多信息")
    _mock_route(monkeypatch, branch="C", supply_note="请提供完整报错截图")

    result = auto_answer_operation(db_session, hub.id)

    db_session.refresh(hub)
    assert result is True
    assert hub.op_status == "processing"          # 不再是 supplementing
    assert hub.op_handler != "agent"              # 人工名，避免被 drain 重扫
    assert hub.reply_content == "请提供完整报错截图"  # 需补内容进草稿
    assert hub.reply_is_draft is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_operation_answer.py::test_branch_c_stays_processing_with_draft -v`
Expected: FAIL（当前 op_status 会是 supplementing）

- [ ] **Step 3: 改 branch C**

把 `operation_answer.py` 里 `if route.branch == "C":` 块替换为：

```python
    if route.branch == "C":
        # 需补料：不再直接置补料中。留处理中 + 把需补内容写进处理说明草稿
        # （reply_content + draft）。前端据此展示，人工改后可「提交答复」或
        # KSM 侧点「补充资料」。handler 用人工名（非 agent），避免 drain 口径
        # （processing+agent）把它当刚毕业未处理再次重答。
        note = (route.supply_note or "").strip()
        if not note:
            return _transfer("需补料但 supply_note 为空，降级留主管")
        _save_draft_reply(db, hub, content=note)
        apply_op_status(
            db,
            hub,
            to_status=OP_PROCESSING,
            handler=resolve_op_handler(db, hub, settings),
            reason="需补料，AI 建议写入处理说明待人工处理",
        )
        _record_decision(db, hub.id, branch="C", question=question, answer=answer, supply_note=note)
        db.commit()
        logger.info("operation_auto_supply_draft", hub_issue_id=hub.id)
        return True
```

注意：删掉原 branch C 里对 `OP_SUPPLEMENTING` 的引用；`_save_draft_reply` 不 commit，故此处补 `db.commit()`（原 branch C 靠 `_record_decision` 内部 commit，现顺序不变仍安全，但显式 commit 更清晰）。若 `OP_SUPPLEMENTING` 在文件内其他地方仍用则保留 import，否则移除未用 import（跑 ruff 确认）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_operation_answer.py -v`
Expected: PASS（含既有用例；若有旧用例断言 branch C → supplementing，需一并更新为 processing+draft）

- [ ] **Step 5: lint**

Run: `cd backend && .venv/bin/ruff check app/services/agents/operation_answer.py && .venv/bin/ruff format --check app/services/agents/operation_answer.py`
Expected: 无错误

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/agents/operation_answer.py backend/tests/unit/services/test_operation_answer.py
git commit -m "feat(supply): AI 判需补料改为留处理中+写处理说明草稿，不再直接置补料中

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: content_refresh 补建附件行（补料回流附件追加保留）

`apply_content_refresh` 现在只覆盖 `source_payload`，不建 Attachment 行。补料重推带的新截图看不到。补：把新 payload 的 `attachment_urls` 建成新附件行（追加，不删旧）。

**Files:**
- Modify: `backend/app/services/ingest/content_refresh.py`
- Test: `backend/tests/unit/services/test_content_refresh.py`

**Interfaces:**
- Consumes: `classify_attachment_kind(url) -> str`、`filename_from_url(url) -> str | None`（`app/core/storage/minio_store.py`）、`Attachment` 模型（字段：`ticket_id`/`source_url`/`filename`/`kind`/`vision_status`）。
- Produces: `apply_content_refresh` 每次调用时，为 `new_payload["attachment_urls"]` 里每个 URL 追加一条 `Attachment`（`vision_status='queued'`），挂到该 ticket。返回值仍为 `bool`（不变）。

- [ ] **Step 1: 写失败测试**

```python
def test_content_refresh_appends_attachments(db_session):
    from app.models import Attachment
    ticket = _make_ksm_ticket(db_session, attachment_urls=["http://x/a.png"])  # 复用既有 helper
    # 首次已有 1 张附件；补料重推带 2 张新图
    apply_content_refresh(
        db_session, ticket,
        {"content": "补充说明", "attachment_urls": ["http://x/b.png", "http://x/c.png"]},
    )
    db_session.flush()
    atts = db_session.query(Attachment).filter(Attachment.ticket_id == ticket.id).all()
    urls = {a.source_url for a in atts}
    assert "http://x/b.png" in urls and "http://x/c.png" in urls  # 新附件已建
    assert len(atts) >= 2  # 追加保留，未覆盖
```

若 `test_content_refresh.py` 无现成建 ticket helper，用文件内已有的构造方式（参照现有用例）。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_content_refresh.py::test_content_refresh_appends_attachments -v`
Expected: FAIL（附件未建）

- [ ] **Step 3: 实现**

在 `apply_content_refresh` 里，`ticket.source_payload = new_payload` 之后加建附件（import 放文件顶部）：

```python
from app.core.storage.minio_store import classify_attachment_kind, filename_from_url
from app.models import Attachment  # 已 import HubIssue, Ticket，追加 Attachment

# ...在函数内，写完 body、hub.canonical_body 之后：
    for url in new_payload.get("attachment_urls", []) or []:
        db.add(
            Attachment(
                ticket_id=ticket.id,
                source_url=url,
                filename=filename_from_url(url),
                kind=classify_attachment_kind(url),
                vision_status="queued",
            )
        )
```

追加式（不查重不删旧）——补料价值就在能对比前后批次。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_content_refresh.py -v`
Expected: PASS

- [ ] **Step 5: lint + 提交**

```bash
cd backend && .venv/bin/ruff check app/services/ingest/content_refresh.py
cd .. && git add backend/app/services/ingest/content_refresh.py backend/tests/unit/services/test_content_refresh.py
git commit -m "feat(supply): 补料回流追加建新附件行，保留历史批次

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: KSM 补料回流打通死胡同（补料中→客户重推→处理中）

`ksm_ingester.py` 命中补料中（supplementing）的分支现在只刷内容、op_status 不动，是死胡同。改成：刷内容（Task 2 已让 content_refresh 建附件）+ 转回 `processing`（handler=agent）+ 幂等，让 drain 重新扫到自动重答。

**Files:**
- Modify: `backend/app/services/ingest/ksm_ingester.py`（约 87-95 行 supplementing 分支）
- Test: `backend/tests/unit/services/test_ksm_ingester.py`

**Interfaces:**
- Consumes: `apply_content_refresh`、`apply_op_status`、`OP_PROCESSING`、`OP_SUPPLEMENTING`。
- Produces: KSM 重推命中 op_status=supplementing 的 hub → 刷内容 + `op_status=processing` + `op_handler='agent'`；已是 processing（重复重推）→ 只刷内容不重复转（幂等）。

- [ ] **Step 1: 写失败测试**

```python
def test_ksm_repush_on_supplementing_reopens_to_processing(db_session):
    # 构造一张 KSM ticket 已毕业 Operation hub，op_status=supplementing
    ingester = KSMIngester(db_session)
    payload = _make_ksm_payload(bill_id="B-100")  # 复用既有 helper
    # 首次入库 + 手动把 hub 置 supplementing（模拟人工已点补充资料）
    r1 = ingester.ingest(payload)
    hub = _graduate_and_set_supplementing(db_session, r1.ticket_id)  # helper：毕业+置态
    # 客户补料，重推同 billId 带新内容
    r2 = ingester.ingest({**payload, "content": "补充：完整截图见附件"})
    db_session.refresh(hub)
    assert r2.deduped is True
    assert hub.op_status == "processing"   # 死胡同打通
    assert hub.op_handler == "agent"       # 交回 agent，drain 会重新答

def test_ksm_repush_on_processing_is_idempotent(db_session):
    # 已 processing 再重推（短时重复）→ 只刷内容，不再重复转
    ingester = KSMIngester(db_session)
    payload = _make_ksm_payload(bill_id="B-101")
    r1 = ingester.ingest(payload)
    hub = _graduate_and_set_supplementing(db_session, r1.ticket_id)
    ingester.ingest({**payload, "content": "第一次补料"})   # → processing/agent
    db_session.refresh(hub)
    changed_at_1 = hub.op_status_changed_at
    ingester.ingest({**payload, "content": "又推一次"})      # 已 processing
    db_session.refresh(hub)
    assert hub.op_status == "processing"
    assert hub.op_status_changed_at == changed_at_1  # 未再次转态
```

若无 `_graduate_and_set_supplementing` helper，在测试文件内定义（毕业调 `ensure_hub_issue_for_ticket` 或直接建 hub 关联 + 置 op_status，参照本文件既有毕业/驳回用例的构造方式）。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_ksm_ingester.py -k "supplementing or idempotent" -v`
Expected: FAIL（supplementing 分支现在 op_status 不动）

- [ ] **Step 3: 改 supplementing 分支**

把 `ksm_ingester.py` 的 `if op == OP_SUPPLEMENTING:` 块替换为：

```python
            if op == OP_SUPPLEMENTING:
                # 客户补料重推同 billId：刷新内容+附件（content_refresh 建新附件行），
                # 并把工单转回 processing/agent，让 drain 重新扫到 → AI 自动重答
                # → 走全局审核闸门。转态幂等：已是 processing 则只刷内容不重复转
                # （防客户短时多次重推重复触发重答）。
                assert hub is not None
                apply_content_refresh(self._db, existing, payload)
                apply_op_status(
                    self._db,
                    hub,
                    to_status=OP_PROCESSING,
                    handler="agent",
                    reason="客户补料回流，交回 AI 重答",
                )
                logger.info(
                    "ksm_ingest_supplement_reopen", bill_id=bill_id, existing_ticket_id=existing.id
                )
                return self._dedup_result(existing)
```

`apply_op_status` 幂等由其内部保证（`hub.op_status == to_status and hub.op_handler == handler → return False`），无需额外判断。确认 `OP_PROCESSING` 已 import（文件顶部 import 块已有 `OP_ANSWERED, OP_PROCESSING, OP_SUPPLEMENTING`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_ksm_ingester.py -v`
Expected: PASS

- [ ] **Step 5: lint + 提交**

```bash
cd backend && .venv/bin/ruff check app/services/ingest/ksm_ingester.py
cd .. && git add backend/app/services/ingest/ksm_ingester.py backend/tests/unit/services/test_ksm_ingester.py
git commit -m "feat(supply): KSM 补料回流转回处理中交 AI 重答，打通死胡同

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: 详情响应暴露 reply_is_draft + 补料/答复守卫

前端要区分「AI 草稿待处理」和「已发答复」，详情响应加 `reply_is_draft`。同时给 request-supply 补「已完成/已关闭」守卫（reply 端点已有 closed 守卫，补 answered 一致性由业务定：answered 可再答属驳回场景不拦，仅补料拦 closed）。

**Files:**
- Modify: `backend/app/api/hub_issues.py`（`HubIssueDetail` 约 106-128 行；request-supply 端点约 415-439 行）
- Test: `backend/tests/unit/api/test_hub_issues_supply_note.py`

**Interfaces:**
- Consumes: `HubIssue.reply_is_draft`（模型已有）、`OP_CLOSED`。
- Produces: `GET /api/hub-issues/{id}` 响应含 `reply_is_draft: bool`；`POST /request-supply` 遇 op_status=closed 返回 409。

- [ ] **Step 1: 写失败测试**

```python
def test_detail_exposes_reply_is_draft(client, db_session, supervisor_token):
    hub = _make_operation_hub_with_draft(db_session)  # reply_is_draft=True
    resp = client.get(f"/api/hub-issues/{hub.id}", headers=_bearer(supervisor_token))
    assert resp.status_code == 200
    assert resp.json()["reply_is_draft"] is True

def test_request_supply_rejected_on_closed(client, db_session, supervisor_token):
    hub = _make_operation_hub(db_session, op_status="closed")
    resp = client.post(
        f"/api/hub-issues/{hub.id}/request-supply",
        json={"note": "请补充"}, headers=_bearer(supervisor_token),
    )
    assert resp.status_code == 409
```

用本文件既有 fixture 风格（`_bearer` / token fixture / 建 hub helper）。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/api/test_hub_issues_supply_note.py -k "reply_is_draft or closed" -v`
Expected: FAIL

- [ ] **Step 3: 实现**

在 `HubIssueDetail` 加字段（`reply_authored_by` 下一行）：

```python
    reply_authored_by: str | None
    reply_is_draft: bool = False  # True=AI 草稿（处理说明待人工处理/发送）
```

`get_hub_issue` 用 `model_validate(hub)`（`from_attributes`）会自动带上 `reply_is_draft`，无需手改组装。

在 request-supply 端点开头（`_authorize_hub_handler` 之后）加守卫：

```python
    hub_pre = db.get(HubIssue, hub_issue_id)
    if hub_pre is not None and hub_pre.type == "Operation" and hub_pre.op_status == OP_CLOSED:
        raise HTTPException(status_code=409, detail=f"hub_issue {hub_pre.short_code} 已关单，不允许再补料")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/api/test_hub_issues_supply_note.py -v`
Expected: PASS

- [ ] **Step 5: 重新生成前端类型**

Run: `make gen-types`
Expected: `frontend/src/api/types.ts` 里 `HubIssueDetail` 出现 `reply_is_draft`

- [ ] **Step 6: lint + 提交**

```bash
cd backend && .venv/bin/ruff check app/api/hub_issues.py
cd .. && git add backend/app/api/hub_issues.py backend/tests/unit/api/test_hub_issues_supply_note.py frontend/src/api/openapi.json frontend/src/api/types.ts
git commit -m "feat(supply): 详情暴露 reply_is_draft + request-supply 加已关闭守卫

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: 前端详情页——处理说明双按钮 + 入口合并 + 草稿提示

Operation 详情页：处理说明框读 `reply_content`（`reply_is_draft` 时给草稿提示）；按 `source_code` 显示按钮——KSM 显示「提交答复」+「补充资料」，智齿只「提交答复」；去掉独立的 `SupplyRequestSection`，补料入口统一到处理说明框下方；「补充资料」用处理说明框当前内容作为补料说明。

**Files:**
- Modify: `frontend/src/pages/hub-issues/HubIssueDetailPage.tsx`（处理说明区约 447-475 行、`SupplyRequestSection` 约 625+ 行）
- Test: `frontend/src/pages/hub-issues/HubIssueDetailPage.test.tsx`（无则新建，参照同目录既有测试风格）

**Interfaces:**
- Consumes: `data.reply_content`、`data.reply_is_draft`（Task 4 新增）、`data.linked_tickets[].source_code`、`postByPath("/api/hub-issues/{hub_issue_id}/reply", ...)`、`postByPath("/api/hub-issues/{hub_issue_id}/request-supply", ...)`。
- Produces: 处理说明编辑区组件，含两个提交动作。

- [ ] **Step 1: 写失败测试**

```tsx
// 渲染 KSM 来源 Operation 详情 → 有「提交答复」和「补充资料」两个按钮
it("KSM 来源显示提交答复+补充资料双按钮", async () => {
  renderDetail({ type: "Operation", op_status: "processing", reply_content: "请补充截图",
    reply_is_draft: true, linked_tickets: [{ source_code: "ksm" }] });
  expect(await screen.findByRole("button", { name: /提交答复/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /补充资料/ })).toBeInTheDocument();
});

// 智齿来源 → 只有「提交答复」，无「补充资料」
it("智齿来源只显示提交答复", async () => {
  renderDetail({ type: "Operation", op_status: "processing", reply_content: "",
    linked_tickets: [{ source_code: "zhichi" }] });
  expect(await screen.findByRole("button", { name: /提交答复/ })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /补充资料/ })).not.toBeInTheDocument();
});
```

`renderDetail` helper 参照同目录既有测试 mock query 的方式（mock `useQuery` 返回 `data`）。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npm run test -- HubIssueDetailPage`
Expected: FAIL

- [ ] **Step 3: 实现**

在处理说明编辑区：
1. 框内容源用 `reply_content`（既有 `drafts[i] ?? data.reply_content` 逻辑保留）。
2. `reply_is_draft` 为真时框上方渲染提示：`以下为 AI 生成的处理建议，请审核后提交`。
3. 计算 `hasKsmSource = data.linked_tickets.some(t => t.source_code === "ksm")`。
4. 按钮区：
   - 「提交答复」→ 调 reply 端点，body `{ content: <框当前值> }`，成功后刷新（invalidate query）。
   - `hasKsmSource` 为真时额外渲染「补充资料」→ 调 request-supply，`{ note: <框当前值> }`（复用同一框内容，不再单独 note 输入）。
5. 删除独立的 `<SupplyRequestSection>` 组件与其渲染点（补料入口已合并）。

保持既有 Tailwind class 风格（`text-xs`/`rounded-md` 等），提交答复用主色按钮，补充资料用琥珀色（沿用原补料区块配色 `bg-hub-amber`）。

- [ ] **Step 4: 跑测试确认通过 + 类型检查 + 构建**

Run: `cd frontend && npm run test -- HubIssueDetailPage && npm run type-check && npm run build`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/hub-issues/HubIssueDetailPage.tsx frontend/src/pages/hub-issues/HubIssueDetailPage.test.tsx
git commit -m "feat(supply): 详情页处理说明双按钮（KSM 补充资料/提交答复），合并补料入口

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: 时间轴渲染补料回流节点 + 附件追加展示（前端）

详情页左侧「处理节点」时间轴要能看到补料来回：状态流转、补料回流（新描述）、答复。附件区展示所有批次附件（Task 2 已追加建行，后端 linked ticket 附件应能取到）。

**Files:**
- Modify: `frontend/src/pages/hub-issues/HubIssueDetailPage.tsx`（时间轴/附件展示区）
- 可能 Modify: `backend/app/api/hub_issues.py`（若详情响应未带 ticket 附件列表，需补——先核查）

**Interfaces:**
- Consumes: 详情响应的 status_history / 附件数据（先核查现有响应带什么；若时间轴已有数据源则纯前端渲染）。

- [ ] **Step 1: 核查现状**

Run: `cd backend && grep -rn "status_history\|attachment\|timeline\|处理节点" app/api/hub_issues.py`
以及前端：`cd frontend && grep -n "处理节点\|timeline\|attachment\|附件" src/pages/hub-issues/HubIssueDetailPage.tsx`
判断时间轴和附件数据源是否已存在。**若已存在数据源 → 纯前端渲染；若缺 → 先在后端详情响应补附件列表（新增字段走 model_validate 或单独查询），再前端渲染。**

- [ ] **Step 2: 写失败测试**

按核查结果，测详情页渲染出补料回流描述段（`[内容更新 ...]`）和多批附件。若时间轴用 status_history：

```tsx
it("时间轴展示补料回流与状态流转节点", async () => {
  renderDetail({ /* 带含补料回流 reason 的 status_history / body 含 [内容更新] 段 */ });
  expect(await screen.findByText(/内容更新/)).toBeInTheDocument();
});
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd frontend && npm run test -- HubIssueDetailPage`
Expected: FAIL

- [ ] **Step 4: 实现渲染（+ 必要时后端补附件字段）**

按 Step 1 结论实现。附件追加展示：列出该 hub 关联 ticket 的所有附件（不去重，按建行顺序），补料的新截图排在后面。

- [ ] **Step 5: 跑测试 + 类型检查 + 构建**

Run: `cd frontend && npm run test -- HubIssueDetailPage && npm run type-check && npm run build`
若动了后端：`cd backend && .venv/bin/pytest tests/unit/api/test_hub_issues_supply_note.py -v && make gen-types`
Expected: 全 PASS

- [ ] **Step 6: 提交**

```bash
git add frontend/src/pages/hub-issues/HubIssueDetailPage.tsx
# 若动后端：git add backend/... frontend/src/api/openapi.json frontend/src/api/types.ts
git commit -m "feat(supply): 详情页时间轴渲染补料回流节点+附件追加展示

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: 端到端 seam 验证 + 全量回归

跑完整闭环 seam 测试，确认不破坏「待审核」闸门 drain 口径。

**Files:**
- Test: `backend/tests/unit/services/test_operation_answer.py`（加 drain 口径回归）或新建 seam 测试

**Interfaces:**
- Consumes: `drain_operation_auto_reply(db, settings=...)`、`auto_answer_operation`、`KSMIngester.ingest`。

- [ ] **Step 1: 写 seam 测试**

```python
def test_supply_refill_full_loop(db_session, monkeypatch):
    # 1. 判需补料 → 处理中 + 草稿（handler≠agent，drain 不扫）
    # 2. drain 一轮 → 该 hub 不被重答（断言 scanned 不含它 / answered==0）
    # 3. 模拟点补充资料 request_supply → supplementing
    # 4. KSM 重推 → processing/agent + 内容刷新
    # 5. drain 再一轮 → 被扫到重答（mock replay 返回可发答复，走全局闸门）
    ...
```

关键断言：branch C 后 handler≠agent 时 `drain_operation_auto_reply` 不选中它；补料回流转 agent 后 drain 选中它。

- [ ] **Step 2: 跑 seam 测试**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_operation_answer.py -k "full_loop" -v`
Expected: PASS

- [ ] **Step 3: 后端全量回归**

Run: `cd backend && make lint && make unit`
Expected: lint clean，unit 全过（GLM network test 若失败是 pre-existing 无关，其余须过），覆盖率≥70%

- [ ] **Step 4: 前端全量回归**

Run: `cd frontend && npm run type-check && npm run test && npm run build`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add backend/tests/unit/services/test_operation_answer.py
git commit -m "test(supply): 补料回流完整闭环 seam + drain 口径回归

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## 备注：后续（不在本计划内）

- 部署 SIT：`git pull` + 重启三容器 + 前端 `build-frontend.sh`（无迁移，不需 alembic upgrade）。
- KSM 回写真发依赖 `ksm_writeback_enabled=true` + `ksm_writeback_dry_run=false`（现有灰度阀，本计划不改）。
- 详情页 `supply_note` 旧字段回填逻辑（api 292-306 行）可保留兼容，不必删——Task 1 仍写 supply_note 审计。
