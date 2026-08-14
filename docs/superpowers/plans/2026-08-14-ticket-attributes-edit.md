# 工单参数编辑迁移到 ticket 详情页 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把工单参数编辑（类型/产品线/模块）从 hub-issue 详情页迁到 ticket 详情页：撤掉 hub 页 UI（后端保留），ticket 页新增按毕业状态分流的编辑区——未毕业改完点「确认分类」一步毕业（带产品线/模块），已毕业脏检测「保存」打 hub 端点 + pending_review「确认推送」。

**Architecture:** create-hub-issue 扩展接收 product_line_code/module 覆盖 + 权限放宽处理人。前端 hub 页回退到上轮之前（恢复 ClassificationReviewPanel）；ticket 页新增 TicketAttributesEditor 按 hub_issue_id/hub.status 分流。

**Tech Stack:** FastAPI + SQLAlchemy（Python 3.11）、pytest、React 18 + TS + TanStack Query + Tailwind、vitest + msw。

## Global Constraints

- 已毕业单参数复用 `PATCH /api/hub-issues/{hub_id}/attributes`（上轮已实现，只改数据不联动）。
- pending_review 确认推送复用 `POST /api/supervisor/confirm-classification`（上轮已放宽处理人）。
- 模块下拉复用 `GET /api/hub-issues/catalog/modules?product_line_code=`；产品线 `GET /api/admin/product-lines`。
- 前端 auth `currentUserId()` + client `patchByPath` 上轮已加，保留。
- HUB_TYPES（`@/api/hubTypes`）只有 4 类型（Operation/Bug_fix/Demand/Internal_task，无 Complaint）；create-hub-issue 的 type pattern 同样只这 4 类。ticket 参数编辑类型下拉用 HUB_TYPES。
- 后端改 API schema 后 `make gen-types` 并提交。
- 单测：`cd backend && .venv/bin/pytest <path> -v`；前端：`cd frontend && npx vitest run <file>`。
- 提交信息中文，尾行 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。

---

### Task 1: create-hub-issue 扩展产品线/模块 + 权限放宽处理人

**Files:**
- Modify: `backend/app/services/hub_issues/creator.py`（ensure_hub_issue_for_ticket 加参）
- Modify: `backend/app/api/supervisor.py`（CreateHubIssueBody + 端点权限）
- Test: `backend/tests/unit/test_create_hub_issue_api.py`

**Interfaces:**
- `ensure_hub_issue_for_ticket(ticket_id, *, created_by, type_override=None, product_line_code=None, module=None, db)` — 新增两个可选参，非 None 时覆盖 hub 的产品线/模块（并同步 ticket + upsert_catalog）。
- `CreateHubIssueBody{ticket_id, type?, product_line_code?, module?}`；端点 require_user + 处理人授权。

- [ ] **Step 1: 写失败测试**

追加到 `test_create_hub_issue_api.py`：

```python
def test_create_with_product_line_and_module_override(app_client, hub_world):
    resp = app_client.post(
        "/api/supervisor/create-hub-issue",
        json={"ticket_id": 300, "type": "Bug_fix", "product_line_code": "cloud-fapiao", "module": "开票管理"},
        headers=_bearer(2),
    )
    assert resp.status_code == 200, resp.text
    from app.models import HubIssue, Module, ProductLine
    hub = hub_world.get(HubIssue, resp.json()["hub_issue_id"])
    assert hub.product_line_code == "cloud-fapiao" and hub.module == "开票管理"
    assert hub_world.query(ProductLine).filter_by(code="cloud-fapiao").first() is not None
    assert hub_world.query(Module).filter_by(product_line_code="cloud-fapiao", name="开票管理").first() is not None


def test_create_by_handler_allowed(app_client, db_session):
    from app.models import Source, Ticket, User
    db_session.add(Source(code="ksm", name="KSM"))
    db_session.add(User(id=7, feishu_uid="ou_h7", name="h7", role="assignee"))
    db_session.add(Ticket(id=301, short_code="TKT-000301", source_code="ksm", source_ticket_id="c-301",
                          type="Raw", status="received", title="t", body="b",
                          predicted_type="Bug_fix", handler_user_id=7))
    db_session.commit()
    resp = app_client.post("/api/supervisor/create-hub-issue", json={"ticket_id": 301},
                           headers=_bearer(7, name="h7", role="assignee"))
    assert resp.status_code == 200, resp.text
```

（现有 `test_requires_supervisor`：bob=uid1 assignee，ticket 300 无 handler_user_id → 仍 403，不受影响，保留。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/test_create_hub_issue_api.py -k "override or by_handler" -v`
Expected: FAIL

- [ ] **Step 3: 改 creator.py**

`ensure_hub_issue_for_ticket` 签名加参：
```python
def ensure_hub_issue_for_ticket(
    ticket_id: int,
    *,
    created_by: str,
    type_override: str | None = None,
    product_line_code: str | None = None,
    module: str | None = None,
    db: Session,
) -> HubIssueResult:
```
在解析出 ticket 后、构造 hub 前，确定生效值并同步回 ticket + 建目录：
```python
    eff_plc = product_line_code if product_line_code is not None else ticket.product_line_code
    eff_module = module if module is not None else ticket.module
    if product_line_code is not None or module is not None:
        from app.services.ingest.catalog_upsert import upsert_catalog
        upsert_catalog(db, product_line_code=eff_plc, module=eff_module)
        ticket.product_line_code = eff_plc
        ticket.module = eff_module
```
hub 构造处 `product_line_code=eff_plc, module=eff_module`（替换原 `ticket.product_line_code`/`ticket.module`）。

- [ ] **Step 4: 改 supervisor.py 端点**

CreateHubIssueBody 加字段：
```python
class CreateHubIssueBody(BaseModel):
    ticket_id: int
    type: str | None = Field(default=None, pattern="^(Operation|Bug_fix|Demand|Internal_task)$")
    product_line_code: str | None = Field(default=None, max_length=64)
    module: str | None = Field(default=None, max_length=128)
```
端点权限 require_supervisor → require_user + 内联处理人校验（ticket 无 hub，不用 _authorize_hub_handler）：
```python
def create_hub_issue_endpoint(
    body: CreateHubIssueBody,
    background_tasks: BackgroundTasks,
    user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> CreateHubIssueResponse:
    if user.role not in ("supervisor", "admin"):
        t = db.get(Ticket, body.ticket_id)
        if t is None or t.handler_user_id != user.user_id:
            raise HTTPException(status_code=403, detail="需要主管/管理员，或本工单处理人才能确认分类")
    ...
    result = ensure_hub_issue_for_ticket(
        body.ticket_id, created_by=f"user:{user.name}", type_override=body.type,
        product_line_code=body.product_line_code, module=body.module, db=db,
    )
```
确认 `Ticket` 已 import 到 supervisor.py（已有，见顶部 from app.models import ... Ticket）。

- [ ] **Step 5: 跑测试通过**

Run: `cd backend && .venv/bin/pytest tests/unit/test_create_hub_issue_api.py tests/unit/services/test_hub_issue_creator.py -v`
Expected: PASS（含现有回归）

- [ ] **Step 6: lint + gen-types + 提交**

```bash
cd backend && .venv/bin/ruff check app/api/supervisor.py app/services/hub_issues/creator.py && .venv/bin/mypy app/api/supervisor.py app/services/hub_issues/creator.py
cd .. && make gen-types
git add backend/app/api/supervisor.py backend/app/services/hub_issues/creator.py backend/tests/unit/test_create_hub_issue_api.py frontend/src/api/openapi.json frontend/src/api/types.ts
git commit -m "feat(ticket-attr): create-hub-issue 加产品线/模块覆盖 + 权限放宽处理人

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: 前端撤销 hub 页参数编辑，恢复 ClassificationReviewPanel

**Files:**
- Modify: `frontend/src/pages/hub-issues/HubIssueDetailPage.tsx`
- Modify: `frontend/src/pages/hub-issues/HubIssueDetailPage.test.tsx`（删/改 attributes 相关用例）

**Interfaces:**
- 恢复 `ClassificationReviewPanel`（confirm/reclassify/dismiss），渲染条件 `研发类 && pending_review`。

- [ ] **Step 1: 取回上轮之前的 ClassificationReviewPanel**

从 git 取本轮 hub-attributes 合并前的版本作参考：
```bash
git show 45684e2~1:frontend/src/pages/hub-issues/HubIssueDetailPage.tsx > /tmp/hub_prev.tsx
grep -n "ClassificationReviewPanel\|_RECLASSIFY_TYPES" /tmp/hub_prev.tsx
```
（45684e2 是 hub-attributes 的 merge commit；`~1` 是其前一状态。用它取回 ClassificationReviewPanel 组件全文 + `_RECLASSIFY_TYPES` 常量。）

- [ ] **Step 2: 替换**

在当前 `HubIssueDetailPage.tsx`：
1. 删除 `HubAttributesEditor` 组件定义 + `type ProductLineOut`/`CatalogModuleOut` + `_TYPE_OPTIONS`（若仅其用）。
2. 粘回 `ClassificationReviewPanel` 组件 + `_RECLASSIFY_TYPES`（从 /tmp/hub_prev.tsx）。
3. 渲染点：`<HubAttributesEditor data={detail.data} />` 改回
```tsx
{(detail.data.type === "Bug_fix" || detail.data.type === "Demand") &&
  detail.data.status === "pending_review" && (
    <ClassificationReviewPanel data={detail.data} />
  )}
```
4. 清理不再使用的 import（patchByPath / currentUserId 若 hub 页不再用则删该文件的 import——但它们在 client.ts/auth.ts 里保留）。

- [ ] **Step 3: 改测试**

`HubIssueDetailPage.test.tsx`：删除本轮加的「工单参数编辑」「精简闸门面板」两个 describe 块（那是 HubAttributesEditor 的）。保留补料回流双按钮相关用例（那是更早的功能，不受影响）。renderDetail 里 op_handler_user_id/product-lines/catalog mock 可留（无害）。

- [ ] **Step 4: 跑测试 + type-check**

Run: `cd frontend && npx vitest run src/pages/hub-issues/HubIssueDetailPage.test.tsx && npm run type-check`
Expected: PASS（无 HubAttributesEditor 残留引用报错）

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/hub-issues/HubIssueDetailPage.tsx frontend/src/pages/hub-issues/HubIssueDetailPage.test.tsx
git commit -m "revert(hub-attr): hub 页移除工单参数编辑，恢复 ClassificationReviewPanel（迁到 ticket 页）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: ticket 页 TicketAttributesEditor（未毕业分支）

**Files:**
- Modify: `frontend/src/pages/tickets/TicketDetailPage.tsx`
- Test: `frontend/src/pages/tickets/TicketDetailPage.test.tsx`（无则新建，仿 hub 页 test 风格）

**Interfaces:**
- Consumes: `d.hub_issue_id`/`d.predicted_type`/`d.product_line_code`/`d.module`/`d.handler_user_id`、`currentUserId`/`isSupervisor`、`GET /api/admin/product-lines`、`GET /api/hub-issues/catalog/modules`、`POST /api/supervisor/create-hub-issue`。

- [ ] **Step 1: 写失败测试**

新建 `TicketDetailPage.test.tsx`（仿 hub 页 renderDetail：mock `/api/tickets/{id}`、`/api/tickets/{id}/history`、product-lines、catalog/modules、admin/users）。核心用例：

```tsx
it("未毕业工单显示三下拉+确认分类，无保存按钮", async () => {
  renderTicket({ hub_issue_id: null, predicted_type: "Bug_fix", product_line_code: "pl-1", module: "m-1" });
  expect(await screen.findByLabelText("工单类型")).toBeInTheDocument();
  expect(screen.getByLabelText("产品线")).toBeInTheDocument();
  expect(screen.getByLabelText("模块")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "确认分类" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run src/pages/tickets/TicketDetailPage.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现 TicketAttributesEditor + 接入未毕业分支**

新增组件 `TicketAttributesEditor({ ticket, hub })`（hub 为已毕业时的 hub detail query 结果，未毕业为 null）：
- `canEdit = ticket.status 非终态 && (isSupervisor() || currentUserId() === ticket.handler_user_id)`。
- 本地 state：type（初始 predicted_type，回落 Operation）、plc（product_line_code ?? ""）、module。
- 产品线 useQuery `/api/admin/product-lines`（enabled: canEdit）；模块 useQuery `catalog/modules?product_line_code=plc`（enabled: canEdit && !!plc）；改产品线清空 module。
- 三下拉带 `aria-label`「工单类型」「产品线」「模块」；类型用 HUB_TYPES。
- **未毕业分支（ticket.hub_issue_id == null）**：单按钮「确认分类」→ `create-hub-issue{ticket_id, type, product_line_code: plc||null, module: module||null}`，成功 invalidate ticket-detail/ticket-history/tickets/hub-issues。无「保存」。
- 无权限/终态 → 只读展示类型/产品线/模块。

接入：把现有「分类未明确（未毕业 hub_issue）」块（TicketDetailPage.tsx 约 468-499 的「分类改判 select + 确认分类」）替换为 `<TicketAttributesEditor ticket={d} hub={null} />`。graduate mutation 逻辑并入组件（或组件调用现有 graduate——建议组件内自带 mutation，删旧 graduate/classifyType 相关本地态）。

- [ ] **Step 4: 跑测试 + type-check + build**

Run: `cd frontend && npx vitest run src/pages/tickets/TicketDetailPage.test.tsx && npm run type-check && npm run build`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/tickets/TicketDetailPage.tsx frontend/src/pages/tickets/TicketDetailPage.test.tsx
git commit -m "feat(ticket-attr): ticket 页工单参数编辑（未毕业：三下拉+确认分类一步毕业）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: ticket 页 已毕业分支（保存 + pending_review 确认推送）

**Files:**
- Modify: `frontend/src/pages/tickets/TicketDetailPage.tsx`（TicketAttributesEditor 补已毕业分支）
- Test: `frontend/src/pages/tickets/TicketDetailPage.test.tsx`

**Interfaces:**
- Consumes: `patchByPath` `/api/hub-issues/{hub_issue_id}/attributes`、`confirm-classification`、hub detail query。

- [ ] **Step 1: 写失败测试**

```tsx
it("已毕业单显示保存，脏检测；pending_review 选研发有确认推送、选运营隐藏", async () => {
  const { fireEvent } = await import("@testing-library/react");
  renderTicket(
    { hub_issue_id: 55, predicted_type: "Bug_fix", product_line_code: "pl-1" },
    { id: 55, type: "Bug_fix", status: "pending_review", product_line_code: "pl-1", module: null, op_status: null },  // hub detail
  );
  expect(await screen.findByRole("button", { name: "保存" })).toBeDisabled(); // 未改动
  expect(screen.getByRole("button", { name: "确认推送" })).toBeInTheDocument();
  const sel = screen.getByLabelText("工单类型");
  fireEvent.change(sel, { target: { value: "Operation" } });
  expect(screen.queryByRole("button", { name: "确认推送" })).not.toBeInTheDocument(); // 选运营隐藏
  expect(screen.getByRole("button", { name: "保存" })).not.toBeDisabled(); // 改动后亮
});
```

renderTicket 需支持传入 hub detail（mock `/api/hub-issues/55`）。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run src/pages/tickets/TicketDetailPage.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现已毕业分支**

`TicketAttributesEditor` 加 `hub` 非 null 分支：
- state 初始化自 hub（hub.type/product_line_code/module）。
- dirty = 任一 ≠ hub 原值。「保存」`disabled={!dirty||busy}` → `patchByPath("/api/hub-issues/{hub_issue_id}/attributes", {hub_issue_id: hub.id}, {type, product_line_code: plc||null, module: module||null})`。
- pending_review（hub.status === "pending_review"）且 type !== "Operation" → 「确认推送」：dirty 时先 await 保存，再 `api.post("/api/supervisor/confirm-classification", {hub_issue_id: hub.id})`。
- 成功 invalidate ticket-detail/ticket-history/hub-issue-detail/tickets/hub-issues/pending-classification。

接入：把现有 `ClassificationReviewInline`（pending_review 分支，约 458-465）替换为 `<TicketAttributesEditor ticket={d} hub={hub.data} />`。**移除** ClassificationReviewInline 的 reclassify/dismiss（改判/误报关闭）——整个组件可删，功能并入 TicketAttributesEditor。已毕业已确认（非 pending_review）也渲染 TicketAttributesEditor（只有保存，无确认推送）。

- [ ] **Step 4: 跑测试 + type-check + build**

Run: `cd frontend && npx vitest run src/pages/tickets/TicketDetailPage.test.tsx && npm run type-check && npm run build`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/tickets/TicketDetailPage.tsx frontend/src/pages/tickets/TicketDetailPage.test.tsx
git commit -m "feat(ticket-attr): ticket 页已毕业单参数保存 + pending_review 确认推送（精简改判/误报关闭）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: 全量回归

- [ ] **Step 1: 后端全量**

Run: `cd backend && .venv/bin/ruff check app tests && .venv/bin/mypy app && .venv/bin/pytest -q`
Expected: ruff clean，pytest 全过（GLM network test pre-existing 失败无关；metrics.py 5 个 mypy 错误 pre-existing 无关）。

- [ ] **Step 2: 前端全量**

Run: `cd frontend && npm run type-check && npx vitest run && npm run build`
Expected: 全 PASS。

- [ ] **Step 3: 更新破的旧测试**

若 ticket 页旧测试断言 ClassificationReviewInline 的改判/误报关闭按钮 → 更新/删除。

- [ ] **Step 4: 提交（若有回归修复）**

```bash
git add -A && git commit -m "test(ticket-attr): 回归修复

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## 备注：后续（不在本计划内）

- 部署 SIT：无迁移，git pull + `up -d --build`（aliyun 源已配，冷构建也快）+ `build-frontend.sh`。
- reclassify / dismiss-classification 后端端点**保留且仍被使用**：Task 2 恢复的 hub 页
  ClassificationReviewPanel 继续调这两个端点（改判/误报关闭）。**端点不能删。** ticket 页新组件
  不用它们（未毕业走 create-hub-issue，已毕业走 hub attributes + confirm-classification）。
