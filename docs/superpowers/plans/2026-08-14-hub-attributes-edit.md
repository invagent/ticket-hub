# 工单参数编辑（类型/产品线/模块）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 详情页新增通用「工单参数」编辑区（类型/产品线/模块，任意非关闭状态可改、只改数据不联动、脏检测保存），处理人本人也能改；精简 pending_review 闸门面板（确认推送按选中类型显隐+自动先保存，移除改判/误报关闭），确认推送放宽到处理人。

**Architecture:** 新增只改数据端点 `PATCH /api/hub-issues/{id}/attributes` + 处理人可读模块下拉端点，与会联动的 reclassify 分开。confirm-classification 权限放宽到 `_authorize_hub_handler`。前端把「工单参数」做成可编辑区，与闸门确认共享选中类型 state。

**Tech Stack:** FastAPI + SQLAlchemy（Python 3.11）、pytest（SQLite in-memory）、React 18 + TS + TanStack Query + Tailwind、vitest + msw。

## Global Constraints

- 权限 helper `_authorize_hub_handler(db, hub_issue_id, user, *, base_roles=("supervisor","admin"))` 在 `app/api/hub_issues.py`（module-level，可 import）：base_roles 角色直接放行，否则要求 user 是该 hub 的 op_handler_user_id 或任一关联 ticket 的 handler_user_id，都不满足 403。
- 工单类型合法值：`Operation|Bug_fix|Demand|Internal_task|Complaint`。
- 「只改数据不联动」：attributes 端点绝不改 hub.status/op_status、不推 Linear、不重分派、不重答。
- 已关闭守卫：hub.status=='closed' 或（type=='Operation' 且 op_status=='closed'）→ 409。
- upsert_catalog 签名：`upsert_catalog(db, *, product_line_code: str|None, module: str|None, product_line_name=None)`，None/空 no-op。
- 改 type 要同步所有关联未删 ticket 的 predicted_type + 每条写 classify_type AgentDecision（`skill="manual", human_confirmed=True, changed_by=f"user:{name}"`），与 reclassify 一致。
- 后端改 API schema 后必须 `make gen-types` 并提交（CI check-types 卡）。
- 单测：`cd backend && .venv/bin/pytest <path> -v`；前端：`cd frontend && npx vitest run <file>`。
- 提交信息中文，尾行 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。

---

### Task 1: 后端 PATCH /attributes 端点（只改数据）

**Files:**
- Modify: `backend/app/api/hub_issues.py`（新增 body 模型 + 端点，放在 request-supply 端点之后）
- Test: `backend/tests/unit/api/test_hub_issues_attributes.py`（新建）

**Interfaces:**
- Consumes: `_authorize_hub_handler`、`upsert_catalog`（`from app.services.ingest.catalog_upsert import upsert_catalog`）、`record_ticket_action`、`OP_CLOSED`、`AgentDecision`/`Ticket`/`HubIssue` 模型、`StatusHistoryRepository`。
- Produces: `PATCH /api/hub-issues/{hub_issue_id}/attributes`，body `{type?, product_line_code?, module?}`，返回 `{hub_issue_id, type, product_line_code, module, updated_ticket_count}`。

- [ ] **Step 1: 写失败测试**

新建 `test_hub_issues_attributes.py`（仿 `test_hub_issues_supply_note.py` 的 `_bearer`/`app_client`/直建 HubIssue 风格）：

```python
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import AgentDecision, HubIssue, Ticket


def _bearer(user_id=1, *, name="carol", role="supervisor"):
    token, _ = issue_jwt(sub=str(user_id), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


def _mk(db, hub_id=200, *, type_="Bug_fix", status="pending_review", op_status=None, handler_uid=None):
    db.add(HubIssue(id=hub_id, short_code=f"HUB-{hub_id}", type=type_, title="t",
                    canonical_body="b", status=status, op_status=op_status,
                    op_handler_user_id=handler_uid, product_line_code="pl-old", module="m-old"))
    db.flush()
    t = Ticket(id=hub_id, short_code=f"TKT-{hub_id}", source_code="ksm",
               source_ticket_id=f"k{hub_id}", type="Raw", status="received",
               hub_issue_id=hub_id, predicted_type=type_)
    db.add(t)
    db.commit()
    return hub_id


def test_attributes_change_type_syncs_ticket_and_audits(app_client: TestClient, db_session: Session):
    hid = _mk(db_session)
    r = app_client.patch(f"/api/hub-issues/{hid}/attributes", json={"type": "Demand"}, headers=_bearer())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["type"] == "Demand" and body["updated_ticket_count"] == 1
    db_session.expire_all()
    assert db_session.get(HubIssue, hid).type == "Demand"
    t = db_session.query(Ticket).filter(Ticket.hub_issue_id == hid).first()
    assert t.predicted_type == "Demand"
    dec = db_session.query(AgentDecision).filter(AgentDecision.subject_type == "ticket", AgentDecision.subject_id == t.id, AgentDecision.decision_type == "classify_type").first()
    assert dec is not None and dec.proposal["human_confirmed"] is True


def test_attributes_change_product_line_and_module(app_client: TestClient, db_session: Session):
    hid = _mk(db_session, hub_id=201)
    r = app_client.patch(f"/api/hub-issues/{hid}/attributes",
                         json={"product_line_code": "cloud-fapiao", "module": "开票管理"}, headers=_bearer())
    assert r.status_code == 200, r.text
    db_session.expire_all()
    h = db_session.get(HubIssue, hid)
    assert h.product_line_code == "cloud-fapiao" and h.module == "开票管理"
    from app.models import Module, ProductLine
    assert db_session.query(ProductLine).filter_by(code="cloud-fapiao").first() is not None
    assert db_session.query(Module).filter_by(product_line_code="cloud-fapiao", name="开票管理").first() is not None


def test_attributes_no_linkage(app_client: TestClient, db_session: Session):
    """只改数据不联动：改 type 后 hub.status/op_status 不变，无 outbox。"""
    from app.models import SyncOutbox
    hid = _mk(db_session, hub_id=202, type_="Bug_fix", status="pending_review")
    app_client.patch(f"/api/hub-issues/{hid}/attributes", json={"type": "Operation"}, headers=_bearer())
    db_session.expire_all()
    h = db_session.get(HubIssue, hid)
    assert h.status == "pending_review"  # 不联动，status 不变
    assert h.op_status is None  # 未触发 op_status 机
    assert db_session.query(SyncOutbox).filter_by(hub_issue_id=hid).count() == 0


def test_attributes_rejected_on_closed(app_client: TestClient, db_session: Session):
    hid = _mk(db_session, hub_id=203, type_="Operation", status="created", op_status="closed")
    r = app_client.patch(f"/api/hub-issues/{hid}/attributes", json={"type": "Demand"}, headers=_bearer())
    assert r.status_code == 409, r.text


def test_attributes_handler_allowed_stranger_403(app_client: TestClient, db_session: Session):
    hid = _mk(db_session, hub_id=204, type_="Operation", status="created", op_status="processing", handler_uid=7)
    # 处理人本人（uid=7）放行
    r_ok = app_client.patch(f"/api/hub-issues/{hid}/attributes", json={"module": "x"}, headers=_bearer(7, name="handler", role="assignee"))
    assert r_ok.status_code == 200, r_ok.text
    # 路人（uid=8, member）403
    r_no = app_client.patch(f"/api/hub-issues/{hid}/attributes", json={"module": "y"}, headers=_bearer(8, name="stranger", role="member"))
    assert r_no.status_code == 403, r_no.text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/api/test_hub_issues_attributes.py -v`
Expected: FAIL（端点 404 not found）

- [ ] **Step 3: 实现端点**

在 `app/api/hub_issues.py` 加（request-supply 端点之后）。确认顶部已 import：`OP_CLOSED`（已在）、`AgentDecision`（已在）、`Ticket`（已在）。新增 `from app.services.ingest.catalog_upsert import upsert_catalog`。

```python
class UpdateAttributesBody(BaseModel):
    type: str | None = Field(None, pattern="^(Operation|Bug_fix|Demand|Internal_task|Complaint)$")
    product_line_code: str | None = Field(None, max_length=64)
    module: str | None = Field(None, max_length=128)


class UpdateAttributesResponse(BaseModel):
    hub_issue_id: int
    type: str
    product_line_code: str | None
    module: str | None
    updated_ticket_count: int


@router.patch("/{hub_issue_id}/attributes", response_model=UpdateAttributesResponse)
def update_hub_attributes(
    hub_issue_id: int,
    body: UpdateAttributesBody,
    user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> UpdateAttributesResponse:
    """只改数据（type/product_line_code/module），不联动下游。处理人本人/主管/管理员可改。"""
    _authorize_hub_handler(db, hub_issue_id, user)
    hub = db.get(HubIssue, hub_issue_id)
    if hub is None or hub.deleted_at is not None:
        raise HTTPException(status_code=404, detail="hub_issue not found")
    if hub.status == "closed" or (hub.type == "Operation" and hub.op_status == OP_CLOSED):
        raise HTTPException(status_code=409, detail=f"hub_issue {hub.short_code} 已关闭，不可修改参数")

    changes: list[str] = []
    updated_tickets = 0
    if body.type is not None and body.type != hub.type:
        old = hub.type
        hub.type = body.type
        linked = db.query(Ticket).filter(Ticket.hub_issue_id == hub.id, Ticket.deleted_at.is_(None)).all()
        for tk in linked:
            tk.predicted_type = body.type
            db.add(AgentDecision(
                decision_type="classify_type", subject_type="ticket", subject_id=tk.id,
                proposal={"predicted_type": body.type, "reason": f"手动修改 {old}→{body.type}",
                          "skill": "manual", "human_confirmed": True, "changed_by": f"user:{user.name}"},
            ))
        updated_tickets = len(linked)
        changes.append(f"类型 {old}→{body.type}")
    if body.product_line_code is not None and body.product_line_code != hub.product_line_code:
        changes.append(f"产品线 {hub.product_line_code}→{body.product_line_code}")
        hub.product_line_code = body.product_line_code
    if body.module is not None and body.module != hub.module:
        changes.append(f"模块 {hub.module}→{body.module}")
        hub.module = body.module

    if body.product_line_code or body.module:
        upsert_catalog(db, product_line_code=hub.product_line_code, module=hub.module)

    if changes:
        StatusHistoryRepository(db).record(
            entity_type="hub_issue", entity_id=hub.id,
            from_status=hub.status, to_status=hub.status,
            changed_by=f"user:{user.name}", reason="修改工单参数: " + "; ".join(changes),
        )
        record_ticket_action(db, hub, action="edit_attributes", changed_by=f"user:{user.name}", reason="; ".join(changes))
    db.commit()
    return UpdateAttributesResponse(
        hub_issue_id=hub.id, type=hub.type,
        product_line_code=hub.product_line_code, module=hub.module,
        updated_ticket_count=updated_tickets,
    )
```

注意 `record_ticket_action` 已在 hub_issues.py import（Task 5 补料用过）；若未 import 则从 `app.services.hub_issues.op_status` 补。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/api/test_hub_issues_attributes.py -v`
Expected: PASS（5 个）

- [ ] **Step 5: lint + 提交**

```bash
cd backend && .venv/bin/ruff check app/api/hub_issues.py && .venv/bin/mypy app/api/hub_issues.py
cd .. && git add backend/app/api/hub_issues.py backend/tests/unit/api/test_hub_issues_attributes.py
git commit -m "feat(hub-attr): PATCH /attributes 只改数据端点（类型/产品线/模块，不联动）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: 后端 处理人可读模块下拉端点 + 详情暴露 op_handler_user_id

**Files:**
- Modify: `backend/app/api/hub_issues.py`（新增 GET catalog/modules + HubIssueDetail 加字段）
- Test: `backend/tests/unit/api/test_hub_issues_attributes.py`（追加）

**Interfaces:**
- Produces: `GET /api/hub-issues/catalog/modules?product_line_code=` → `list[{code, name}]`（require_user）；`HubIssueDetail.op_handler_user_id: int | None`。

- [ ] **Step 1: 写失败测试**

追加到 `test_hub_issues_attributes.py`：

```python
def test_catalog_modules_readable_by_user(app_client: TestClient, db_session: Session):
    from app.models import Module, ProductLine
    db_session.add(ProductLine(code="pl-1", name="产品线1", is_active=True))
    db_session.add(Module(product_line_code="pl-1", name="模块A", is_active=True))
    db_session.add(Module(product_line_code="pl-2", name="模块B", is_active=True))
    db_session.commit()
    r = app_client.get("/api/hub-issues/catalog/modules?product_line_code=pl-1", headers=_bearer(9, name="u", role="member"))
    assert r.status_code == 200, r.text
    names = [m["name"] for m in r.json()]
    assert "模块A" in names and "模块B" not in names  # 按产品线过滤


def test_detail_exposes_op_handler_user_id(app_client: TestClient, db_session: Session):
    hid = _mk(db_session, hub_id=205, type_="Operation", status="created", op_status="processing", handler_uid=42)
    r = app_client.get(f"/api/hub-issues/{hid}", headers=_bearer())
    assert r.status_code == 200, r.text
    assert r.json()["op_handler_user_id"] == 42
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/api/test_hub_issues_attributes.py -k "catalog_modules or op_handler_user_id" -v`
Expected: FAIL

- [ ] **Step 3: 实现**

在 `HubIssueDetail`（`reply_is_draft` 附近）加字段：
```python
    op_handler_user_id: int | None = None
```
（`HubIssueDetail` 继承 `HubIssueSummary`，用 `model_validate(hub)` 自动带上——确认 HubIssue 模型有 `op_handler_user_id` 列，已有。放在 HubIssueDetail 或 Summary 均可；放 Detail 即可。）

新增模块只读端点（放 list 端点附近，注意路由顺序：`/catalog/modules` 静态段在 `/{hub_issue_id}` 之前不冲突，因为 hub_issue_id 是 int 路径，"catalog" 不会被解析成 int；但保险起见放在 `/{hub_issue_id}` 路由**之前**声明）：
```python
class CatalogModuleOut(BaseModel):
    code: str
    name: str


@router.get("/catalog/modules", response_model=list[CatalogModuleOut])
def list_catalog_modules(
    product_line_code: str | None = Query(None),
    _user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> list[CatalogModuleOut]:
    """处理人可读的模块下拉（require_user）。按 product_line_code 过滤，仅 active。"""
    from app.models import Module

    stmt = select(Module).where(Module.is_active.is_(True))
    if product_line_code:
        stmt = stmt.where(Module.product_line_code == product_line_code)
    stmt = stmt.order_by(Module.name)
    return [CatalogModuleOut(code=m.name, name=m.name) for m in db.scalars(stmt).all()]
```
（Module 无独立 code 列，用 name 作为 code+name；确认 `select` 已 import，未则补 `from sqlalchemy import select`。若 `/{hub_issue_id}` 端点用 int 类型注解，FastAPI 不会把 "catalog" 匹配进去，但仍建议 catalog 路由声明在前。）

- [ ] **Step 4: 跑测试 + 全 attributes 文件**

Run: `cd backend && .venv/bin/pytest tests/unit/api/test_hub_issues_attributes.py -v`
Expected: PASS（7 个）

- [ ] **Step 5: gen-types + lint + 提交**

```bash
cd backend && .venv/bin/ruff check app/api/hub_issues.py && .venv/bin/mypy app/api/hub_issues.py
cd .. && make gen-types
grep -c "op_handler_user_id" frontend/src/api/types.ts  # 应 ≥1
git add backend/app/api/hub_issues.py backend/tests/unit/api/test_hub_issues_attributes.py frontend/src/api/openapi.json frontend/src/api/types.ts
git commit -m "feat(hub-attr): 处理人可读模块下拉端点 + 详情暴露 op_handler_user_id

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: confirm-classification 权限放宽到处理人

**Files:**
- Modify: `backend/app/api/supervisor.py`（confirm_classification 端点）
- Test: `backend/tests/unit/api/test_hub_issues_reanswer.py` 或新建 `test_confirm_classification_perm.py`

**Interfaces:**
- Consumes: `_authorize_hub_handler`（`from app.api.hub_issues import _authorize_hub_handler`）、`require_user`。
- Produces: `confirm-classification` 接受处理人本人（op_handler/ticket handler）+ 主管/管理员。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/unit/api/test_confirm_classification_perm.py`：

```python
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import HubIssue, Ticket


def _bearer(user_id, *, name, role):
    token, _ = issue_jwt(sub=str(user_id), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


def _mk_pending(db, hub_id=300, handler_uid=7):
    db.add(HubIssue(id=hub_id, short_code=f"HUB-{hub_id}", type="Bug_fix", title="t",
                    canonical_body="b", status="pending_review", op_handler_user_id=handler_uid))
    db.add(Ticket(id=hub_id, short_code=f"TKT-{hub_id}", source_code="ksm", source_ticket_id=f"k{hub_id}",
                  type="Raw", status="received", hub_issue_id=hub_id, handler_user_id=handler_uid))
    db.commit()
    return hub_id


def test_confirm_by_handler_allowed(app_client: TestClient, db_session: Session):
    hid = _mk_pending(db_session)
    r = app_client.post("/api/supervisor/confirm-classification", json={"hub_issue_id": hid},
                        headers=_bearer(7, name="handler", role="assignee"))
    assert r.status_code == 200, r.text


def test_confirm_by_stranger_403(app_client: TestClient, db_session: Session):
    hid = _mk_pending(db_session, hub_id=301, handler_uid=7)
    r = app_client.post("/api/supervisor/confirm-classification", json={"hub_issue_id": hid},
                        headers=_bearer(8, name="stranger", role="member"))
    assert r.status_code == 403, r.text
```

（gate_linear_push_enabled 默认值下 Bug_fix 会停 pending_linear_review 或推 Linear；确认返回 200 即可，不验证下游。若测试环境 push 开关会真发 Linear，用 monkeypatch 挡 `push_hub_issue_to_linear`——参照现有 supervisor 测试是否已 stub。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/api/test_confirm_classification_perm.py -v`
Expected: FAIL（handler 收到 403，因现在还是 require_supervisor）

- [ ] **Step 3: 实现**

`app/api/supervisor.py`：
1. 顶部 import：`from app.api.hub_issues import _authorize_hub_handler` + 确保 `require_user` 已 import（`from app.api.deps.auth import AuthedUser, require_knowledge_op, require_supervisor, require_user`）。
2. `confirm_classification` 端点依赖 `user: AuthedUser = Depends(require_supervisor)` → `Depends(require_user)`，函数体第一行（`_get_pending_review_hub` 之前）加 `_authorize_hub_handler(db, body.hub_issue_id, user)`。

```python
def confirm_classification(
    body: ConfirmClassificationBody,
    background_tasks: BackgroundTasks,
    user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> ClassificationActionResponse:
    _authorize_hub_handler(db, body.hub_issue_id, user)
    ...  # 其余不变
```

reclassify / dismiss-classification **不动**（保持 require_supervisor）。

- [ ] **Step 4: 跑测试通过 + 回归 supervisor 测试**

Run: `cd backend && .venv/bin/pytest tests/unit/api/test_confirm_classification_perm.py tests/unit/api/ -k "classif" -v`
Expected: PASS（含现有分类测试不回归）

- [ ] **Step 5: lint + 提交**

```bash
cd backend && .venv/bin/ruff check app/api/supervisor.py && .venv/bin/mypy app/api/supervisor.py
cd .. && git add backend/app/api/supervisor.py backend/tests/unit/api/test_confirm_classification_perm.py
git commit -m "feat(hub-attr): confirm-classification 权限放宽到处理人本人

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: 前端 auth 加 currentUserId + 工单参数可编辑区

**Files:**
- Modify: `frontend/src/api/auth.ts`（加 currentUserId）
- Modify: `frontend/src/pages/hub-issues/HubIssueDetailPage.tsx`（TaskInfoCard 的产品分类/类型改可编辑，或新增 HubAttributesCard）
- Test: `frontend/src/pages/hub-issues/HubIssueDetailPage.test.tsx`（追加）

**Interfaces:**
- Consumes: `currentUserId()`、`isSupervisor()`、`data.op_handler_user_id`、`GET /api/admin/product-lines`、`GET /api/hub-issues/catalog/modules`、`PATCH /api/hub-issues/{id}/attributes`。
- Produces: 可编辑「工单参数」区（类型/产品线/模块下拉 + 脏检测保存）。

- [ ] **Step 1: auth 加 currentUserId**

`frontend/src/api/auth.ts` 追加：
```typescript
/** 当前用户 id（localStorage.auth_user.id），未登录返回 null。 */
export function currentUserId(): number | null {
  try {
    const u = JSON.parse(localStorage.getItem("auth_user") ?? "null") as { id?: number } | null;
    return typeof u?.id === "number" ? u.id : null;
  } catch {
    return null;
  }
}
```

- [ ] **Step 2: 写失败测试**

追加到 `HubIssueDetailPage.test.tsx`（renderDetail helper 已有，base 需补 `op_handler_user_id: null`）：

```tsx
it("有权限时工单参数区可编辑（主管）", async () => {
  renderDetail({ type: "Bug_fix", status: "created", product_line_code: "pl-1", module: "m-1" });
  // 类型下拉可见（可编辑）
  expect(await screen.findByLabelText("工单类型")).toBeInTheDocument();
  // 未改动时保存按钮 disabled
  const save = screen.getByRole("button", { name: "保存" });
  expect(save).toBeDisabled();
});

it("已关闭工单参数区只读（无下拉无保存）", async () => {
  renderDetail({ type: "Operation", status: "created", op_status: "closed", product_line_code: "pl-1" });
  expect(await screen.findByText("HUB-000001")).toBeInTheDocument();
  expect(screen.queryByLabelText("工单类型")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
});
```

需要给 renderDetail 的 server.use 补 product-lines / catalog/modules 的 mock（返回空数组即可，下拉不崩）：
```tsx
server.use(
  http.get("*/api/admin/product-lines", () => HttpResponse.json([])),
  http.get("*/api/hub-issues/catalog/modules", () => HttpResponse.json([])),
);
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd frontend && npx vitest run src/pages/hub-issues/HubIssueDetailPage.test.tsx`
Expected: FAIL

- [ ] **Step 4: 实现工单参数可编辑区**

在 `HubIssueDetailPage.tsx`：
1. import `currentUserId`。
2. 新增组件 `HubAttributesCard`（或改造 TaskInfoCard 里「任务类型」「产品分类」两块为可编辑；建议独立组件避免 TaskInfoCard 过重）。
3. 权限判定：
```tsx
const canEditAttr =
  data.status !== "closed" &&
  !(data.type === "Operation" && data.op_status === "closed") &&
  (isSupervisor() || (data.op_handler_user_id != null && currentUserId() === data.op_handler_user_id));
```
4. 有权限 → 渲染三个受控下拉（类型固定 5 项；产品线 useQuery `/api/admin/product-lines`；模块 useQuery `/api/hub-issues/catalog/modules?product_line_code=<当前选中产品线>`，产品线变时 refetch）；本地 state `{type, plc, module}` 初始化自 data；改产品线时清空 module（`setModule("")`）。
5. 脏检测：`dirty = type!==data.type || plc!==data.product_line_code || module!==(data.module ?? "")`；「保存」`disabled={!dirty || saving}`。
6. 保存：client.ts 无 PATCH helper（只有 post/put/deleteByPath），**先加 `patchByPath`**（照 `putByPath` 复制，`method: "PATCH"`，返回类型 `ResponseOf<paths[P], "patch">`）：
```typescript
export async function patchByPath<P extends keyof paths>(
  templatePath: P,
  params: Record<string, string | number>,
  body?: unknown,
): Promise<ResponseOf<paths[P], "patch">> {
  let actual = templatePath as string;
  for (const [k, v] of Object.entries(params)) {
    actual = actual.replaceAll(`{${k}}`, encodeURIComponent(String(v)));
  }
  return request(actual, {
    method: "PATCH",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
}
```
   保存调 `patchByPath("/api/hub-issues/{hub_issue_id}/attributes", { hub_issue_id: data.id }, {type, product_line_code, module})`，成功 invalidate `["hub-issue-detail", data.id]`。
7. 无权限 → 只读文本（复用 TaskInfoCard 现有「产品分类」展示风格）。

**注意**：类型下拉加 `aria-label="工单类型"`（测试用 getByLabelText）。

- [ ] **Step 5: 跑测试 + type-check + build**

Run: `cd frontend && npx vitest run src/pages/hub-issues/HubIssueDetailPage.test.tsx && npm run type-check && npm run build`
Expected: 全 PASS

- [ ] **Step 6: 提交**

```bash
git add frontend/src/api/auth.ts frontend/src/api/client.ts frontend/src/pages/hub-issues/HubIssueDetailPage.tsx frontend/src/pages/hub-issues/HubIssueDetailPage.test.tsx
git commit -m "feat(hub-attr): 详情页工单参数可编辑区（类型/产品线/模块，脏检测保存）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: 前端 精简 pending_review 闸门面板

**Files:**
- Modify: `frontend/src/pages/hub-issues/HubIssueDetailPage.tsx`（ClassificationReviewPanel + 父组件共享选中类型）
- Test: `frontend/src/pages/hub-issues/HubIssueDetailPage.test.tsx`（追加）

**Interfaces:**
- Consumes: Task 4 的选中类型 state（提升到父级，A/B 共享）、`confirm-classification` 端点。

- [ ] **Step 1: 写失败测试**

```tsx
it("pending_review 选研发显示确认推送，选运营隐藏", async () => {
  renderDetail({ type: "Bug_fix", status: "pending_review", product_line_code: "pl-1" });
  // 默认 Bug_fix → 有确认推送
  expect(await screen.findByRole("button", { name: "确认推送" })).toBeInTheDocument();
  // 把类型下拉改成 Operation → 确认推送消失
  const sel = screen.getByLabelText("工单类型");
  fireEvent.change(sel, { target: { value: "Operation" } });
  expect(screen.queryByRole("button", { name: "确认推送" })).not.toBeInTheDocument();
});

it("pending_review 无改判/误报关闭按钮", async () => {
  renderDetail({ type: "Bug_fix", status: "pending_review", product_line_code: "pl-1" });
  expect(await screen.findByText("HUB-000001")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "改判" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "误报关闭" })).not.toBeInTheDocument();
});
```

（import `fireEvent`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run src/pages/hub-issues/HubIssueDetailPage.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现**

1. 把选中类型 state 提升到父级（渲染 `HubAttributesCard` 和 `ClassificationReviewPanel` 的共同父组件——即详情页主体），传给两者。或用一个包裹组件同时管两块。
2. 改造 `ClassificationReviewPanel`：
   - 移除 `_RECLASSIFY_TYPES` select + 「改判」按钮 + 「误报关闭」按钮 + `reclassify`/`dismiss` mutation。
   - 「确认推送」按钮：`{selectedType !== "Operation" && <button>确认推送</button>}`（selectedType 来自共享 state）。
   - 点确认推送：若 Task 4 表单 dirty → 先 `await saveAttributes()` 再 `confirm.mutate()`（自动先保存，spec 决策 7）。
   - 保留无权限只读提示（现在放宽到处理人，判定同 canEditAttr；非授权显示「待处理人/主管确认分类」提示）。
3. `_RECLASSIFY_TYPES` 常量若无其他引用则删除。

- [ ] **Step 4: 跑测试 + type-check + build**

Run: `cd frontend && npx vitest run src/pages/hub-issues/HubIssueDetailPage.test.tsx && npm run type-check && npm run build`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/hub-issues/HubIssueDetailPage.tsx frontend/src/pages/hub-issues/HubIssueDetailPage.test.tsx
git commit -m "feat(hub-attr): 精简 pending_review 面板（确认推送按选中类型显隐+自动先保存，移除改判/误报关闭）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: 全量回归

- [ ] **Step 1: 后端全量**

Run: `cd backend && .venv/bin/ruff check . && .venv/bin/mypy app && .venv/bin/pytest -q`
Expected: lint/mypy clean（仅本次改动文件），pytest 全过（GLM network test pre-existing 失败无关）。

- [ ] **Step 2: 前端全量**

Run: `cd frontend && npm run type-check && npx vitest run && npm run build`
Expected: 全 PASS。

- [ ] **Step 3: 更新旧测试（如破）**

若 `frontend/tests/HubIssueDetailPage.test.tsx` 有断言旧「改判」「误报关闭」按钮的用例 → 更新到新 UI。若 `backend` 有断言 confirm-classification require_supervisor 的用例 → 更新。

- [ ] **Step 4: 提交（若有回归修复）**

```bash
git add -A && git commit -m "test(hub-attr): 更新旧测试到工单参数编辑新 UI/权限

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## 备注：后续（不在本计划内）

- 部署 SIT：无迁移，git pull + `up -d --build` 三容器 + `build-frontend.sh`。
- reclassify / dismiss-classification 端点保留但前端不再调用（未来若无引用可清理）。
