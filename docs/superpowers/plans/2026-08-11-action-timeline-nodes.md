# 操作留痕进处理节点时间轴 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** 详情页各操作按钮触发后在左侧「处理节点」时间轴留一条节点(操作内容+处理人+时间)并能查看历史。答复/确认分类/改判/误报=后端补写 ticket 维度审计(真实留痕);退回/拆分转单=前端本地占位节点(后端动作待做)。

**Architecture:** 时间轴由 `GET /api/tickets/{id}/history` 驱动,只查 `entity_type="ticket"` 的 status_history。hub 维度操作(reply/确认分类三动作)当前只写 hub 维度审计,时间轴看不到。方案:加一个公共 helper 给 hub 的每条关联 ticket 补写一条 `entity_type="ticket"` 审计行(纯操作事件,from==to==ticket 当前状态,操作说明放 reason),从 reply / confirm / reclassify / dismiss 端点调用。前端时间轴对 from==to 的审计事件优先渲染 reason 作为节点标题。退回/拆分在前端点击时插入本地占位节点。

**Tech Stack:** FastAPI + SQLAlchemy(后端);React + TanStack Query + Vitest(前端)。

## Global Constraints

- status_history 写入口 `StatusHistoryRepository.record(entity_type, entity_id, from_status, to_status, changed_by, reason, metadata)`。
- 审计事件约定:操作不改 ticket 状态 → `from_status == to_status == ticket.status`(与现有 assign/supply 一致),操作说明放 `reason`,`metadata={"action": "<slug>"}`。
- 后端补写只给 hub 的关联 ticket(`TicketRepository.list_for_hub_issue`),每条一行;不 commit(复用端点已有事务),或在端点 commit 前调用。
- 改后端 API 若改响应 schema 要 `make gen-types`;本计划不改 history 响应 schema(复用现有字段),故无需 gen-types。
- 前端只改 `frontend/src/pages/tickets/TicketDetailPage.tsx` + `frontend/tests/TicketDetailPage.test.tsx`。
- 验证:后端 `cd backend && .venv/bin/pytest tests/unit -k "..." -v`;前端 `npm run test / type-check / build`。

---

### Task 1: 后端公共 helper — 给 hub 关联 ticket 补写操作审计

**Files:**
- Modify: `backend/app/services/hub_issues/op_status.py`(加 `record_ticket_action`)
- Test: `backend/tests/unit/services/test_ticket_action_audit.py`(Create)

**Interfaces:**
- Produces: `record_ticket_action(db, hub, *, action: str, changed_by: str, reason: str) -> int` — 给 hub 的每条未删除关联 ticket 写一行 `entity_type="ticket"` status_history(from==to==ticket.status, metadata={"action": action}),返回写入条数。不 commit。

- [ ] **Step 1: 写失败测试**

```python
from app.models import HubIssue, Ticket, StatusHistory
from app.services.hub_issues.op_status import record_ticket_action

def test_record_ticket_action_writes_row_per_linked_ticket(db_session):
    hub = HubIssue(id=200, short_code="HUB-000200", type="Operation", title="t", status="created", op_status="answered")
    db_session.add(hub); db_session.flush()
    db_session.add(Ticket(id=500, short_code="TKT-000500", type="Raw", status="in_progress", title="x", hub_issue_id=200))
    db_session.add(Ticket(id=501, short_code="TKT-000501", type="Raw", status="linked", title="y", hub_issue_id=200))
    db_session.commit()

    n = record_ticket_action(db_session, hub, action="reply", changed_by="user:张三", reason="已答复客户")
    db_session.commit()
    assert n == 2
    rows = db_session.query(StatusHistory).filter(StatusHistory.entity_type == "ticket", StatusHistory.entity_id == 500).all()
    assert len(rows) == 1
    assert rows[0].to_status == "in_progress" and rows[0].from_status == "in_progress"  # 不改状态
    assert rows[0].reason == "已答复客户"
    assert rows[0].changed_by == "user:张三"
    assert rows[0].metadata_ == {"action": "reply"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_ticket_action_audit.py -v`
Expected: FAIL(ImportError record_ticket_action)。

- [ ] **Step 3: 实现**

在 `op_status.py` 加(import `TicketRepository` 用现成的 `list_for_hub_issue`;或直接 query Ticket 避免循环 import):
```python
def record_ticket_action(
    db: Session,
    hub: HubIssue,
    *,
    action: str,
    changed_by: str,
    reason: str,
) -> int:
    """给 hub 的每条关联 ticket 补写一条操作审计（entity_type='ticket'）。
    纯操作事件：不改 ticket 状态（from==to==当前状态），操作说明放 reason，
    metadata.action 标操作类型。用于把 hub 维度操作(答复/分类动作)投影到工单时间轴。
    不 commit。返回写入条数。
    """
    from app.models import Ticket

    tickets = (
        db.query(Ticket)
        .filter(Ticket.hub_issue_id == hub.id, Ticket.deleted_at.is_(None))
        .all()
    )
    repo = StatusHistoryRepository(db)
    for t in tickets:
        repo.record(
            entity_type="ticket",
            entity_id=t.id,
            from_status=t.status,
            to_status=t.status,
            changed_by=changed_by,
            reason=reason,
            metadata={"action": action},
        )
    return len(tickets)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_ticket_action_audit.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/hub_issues/op_status.py backend/tests/unit/services/test_ticket_action_audit.py
git commit -m "feat(op-status): record_ticket_action 把 hub 操作投影到工单审计"
```

---

### Task 2: reply 端点调用 — 答复留痕

**Files:**
- Modify: `backend/app/api/hub_issues.py`(author_reply_endpoint,reply 后)
- Test: `backend/tests/unit/test_hub_issue_reply_api.py`(追加)

**Interfaces:**
- Consumes: `record_ticket_action`(Task 1)。
- Produces: POST /reply 成功后,每条关联 ticket 多一条 `metadata.action=="reply"`、reason="主管答复客户"、changed_by="user:{name}" 的 ticket status_history。

- [ ] **Step 1: 写失败测试**

在 reply api 测试文件加(参考现有 reply 测试建 hub+ticket 的写法):
```python
def test_reply_records_ticket_action_node(app_client, reply_world):
    # reply_world: Operation hub(id=H) + 关联 ticket(id=T, source ksm)
    r = app_client.post(f"/api/hub-issues/{H}/reply", json={"content": "您好,已处理"}, headers=_bearer())
    assert r.status_code == 200
    rows = db.query(StatusHistory).filter(
        StatusHistory.entity_type == "ticket", StatusHistory.entity_id == T,
        StatusHistory.metadata_["action"].astext == "reply",  # 或取 all 后 python 过滤
    ).all()
    assert len(rows) == 1
```
(JSON metadata 查询在 SQLite 上用 python 过滤更稳:取该 ticket 所有 status_history,断言存在一条 metadata_=={"action":"reply"}。)

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/test_hub_issue_reply_api.py -k action -v`
Expected: FAIL(无 reply 审计行)。

- [ ] **Step 3: 实现**

`author_reply_endpoint`(hub_issues.py:~322-365)在 `apply_op_status(... answered)` 之后、commit 之前加:
```python
    record_ticket_action(
        db, hub, action="reply", changed_by=f"user:{user.name}", reason="主管答复客户"
    )
```
import `record_ticket_action`(from app.services.hub_issues.op_status import ...,该模块已 import apply_op_status)。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/test_hub_issue_reply_api.py -v`
Expected: PASS(含既有用例不回归)。

- [ ] **Step 5: 提交**

```bash
git add backend/app/api/hub_issues.py backend/tests/unit/test_hub_issue_reply_api.py
git commit -m "feat(reply): 答复后给关联工单写时间轴节点"
```

---

### Task 3: 分类三动作端点调用 — 确认/改判/误报留痕

**Files:**
- Modify: `backend/app/api/supervisor.py`(confirm/reclassify/dismiss 三处)
- Test: `backend/tests/unit/test_supervisor_classification_actions.py`(追加)

**Interfaces:**
- Consumes: `record_ticket_action`。
- Produces: 三端点各在 commit 前给关联 ticket 写审计:confirm→action="confirm_classification" reason="确认分类,推送 Linear";reclassify→action="reclassify" reason=f"改判为 {new_type}";dismiss→action="dismiss_classification" reason="误报关闭"。changed_by=f"user:{user.name}"。

- [ ] **Step 1: 写失败测试**

对三动作各加一条:操作后关联 ticket 存在对应 metadata.action 审计行(python 过滤断言)。参考现有 classification_actions 测试的 pending_review hub + 关联 ticket fixture。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/test_supervisor_classification_actions.py -k action -v`
Expected: FAIL。

- [ ] **Step 3: 实现**

三个 handler 各在 `db.commit()` 前加对应 `record_ticket_action(...)`。import 顶部补 `record_ticket_action`。reclassify 的 reason 用 `f"改判为 {body.new_type}"`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/test_supervisor_classification_actions.py -v`
Expected: PASS(不回归)。

- [ ] **Step 5: 提交**

```bash
git add backend/app/api/supervisor.py backend/tests/unit/test_supervisor_classification_actions.py
git commit -m "feat(classification): 确认/改判/误报给关联工单写时间轴节点"
```

---

### Task 4: 前端时间轴渲染操作节点 + 各 mutation 刷新

**Files:**
- Modify: `frontend/src/pages/tickets/TicketDetailPage.tsx`
- Test: `frontend/tests/TicketDetailPage.test.tsx`

**Interfaces:**
- Consumes: history 事件(status kind, from==to 且带 reason 的审计行)。
- Produces: VerticalTimeline 对 `from_status==to_status && reason` 的 status 事件渲染 reason 作为节点标题(而非 `x → x`);graduate mutation onSuccess 补 invalidate ticket-history。

- [ ] **Step 1: 写失败测试**

```tsx
  it("时间轴把操作审计事件(from==to+reason)渲染为操作说明", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubOperationTicket(350);
    server.use(
      http.get("*/api/hub-issues/88", () =>
        HttpResponse.json({ id: 88, short_code: "HUB-88", type: "Operation", status: "created", op_status: "answered" }),
      ),
      http.get("*/api/tickets/350/history", () =>
        HttpResponse.json({ ticket_id: 350, items: [
          { kind: "status", occurred_at: "2026-08-11T10:00:00Z", from_status: "in_progress", to_status: "in_progress", changed_by: "user:张三", reason: "主管答复客户", metadata_: { action: "reply" }, hub_issue_id: null, effective_to: null, change_reason: null, human_confirmed: null },
        ] }),
      ),
    );
    renderPage(350);
    await screen.findByRole("heading", { name: "TKT-350" });
    expect(await screen.findByText("主管答复客户")).toBeInTheDocument();
    // 不再渲染成 in_progress → in_progress
    expect(screen.queryByText(/in_progress → in_progress/)).not.toBeInTheDocument();
    localStorage.clear();
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run TicketDetailPage -t 操作审计`
Expected: FAIL(渲染成 in_progress → in_progress)。

- [ ] **Step 3: 实现**

VerticalTimeline 的 label 计算改为:status 事件若 `from_status===to_status && reason` → 用 reason;否则维持 `from → to`:
```tsx
        const label =
          ev.kind === "status"
            ? ev.from_status === ev.to_status && ev.reason
              ? ev.reason
              : `${ev.from_status ?? "∅"} → ${ev.to_status ?? ""}`
            : ev.effective_to !== null
              ? `关联关闭 HUB-${ev.hub_issue_id}`
              : `关联建立 HUB-${ev.hub_issue_id}`;
```
graduate mutation onSuccess 补 `void qc.invalidateQueries({ queryKey: ["ticket-history", id] });`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run TicketDetailPage -t 操作审计`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/tickets/TicketDetailPage.tsx frontend/tests/TicketDetailPage.test.tsx
git commit -m "feat(ticket-detail): 时间轴渲染操作审计节点为操作说明 + 毕业刷新时间轴"
```

---

### Task 5: 退回/拆分转单前端本地占位节点

**Files:**
- Modify: `frontend/src/pages/tickets/TicketDetailPage.tsx`
- Test: `frontend/tests/TicketDetailPage.test.tsx`

**Interfaces:**
- Produces: 点退回/拆分转单时,除现有 confirmNotice 外,往时间轴插一条前端本地操作节点(标注「待后端」)。用本地 state `localActions: {label,at}[]`,合并进 VerticalTimeline events 顶部。

- [ ] **Step 1: 写失败测试**

```tsx
  it("点退回转单在时间轴插入本地占位节点", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubOperationTicket(351);
    server.use(
      http.get("*/api/hub-issues/88", () =>
        HttpResponse.json({ id: 88, short_code: "HUB-88", type: "Operation", status: "created", op_status: "processing" }),
      ),
    );
    renderPage(351);
    await screen.findByRole("heading", { name: "TKT-351" });
    // 选退回转单
    await userEvent.selectOptions(screen.getByRole("combobox"), "return");
    await userEvent.click(screen.getByRole("button", { name: "退回转单" }));
    expect(await screen.findByText(/退回转单（待后端）/)).toBeInTheDocument();
    localStorage.clear();
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run TicketDetailPage -t 本地占位`
Expected: FAIL。

- [ ] **Step 3: 实现**

加 state `const [localActions, setLocalActions] = useState<{label: string}[]>([]);`。退回/拆分分支点击时 `setLocalActions(p => [{label: "退回转单（待后端）"}, ...p])` / `"拆分转单（待后端）"`。VerticalTimeline events 前置合并这些本地节点(渲染为普通节点,处理人="本地操作·待后端",label 用 localAction.label)。实现:把 localActions 映射成 pseudo HistoryEvent(kind="status", from==to, reason=label, changed_by="本地操作·待后端")拼到 `[...history.data.items].reverse()` 前面。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run TicketDetailPage -t 本地占位`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/tickets/TicketDetailPage.tsx frontend/tests/TicketDetailPage.test.tsx
git commit -m "feat(ticket-detail): 退回/拆分转单插入前端本地占位节点(待后端)"
```

---

### Task 6: 全量验证

- [ ] **Step 1: 后端**

Run: `cd backend && .venv/bin/ruff check app/services/hub_issues/op_status.py app/api/hub_issues.py app/api/supervisor.py && .venv/bin/pytest tests/unit -k "action or reply or classification or ticket_action" -q`
Expected: clean + pass。

- [ ] **Step 2: 类型同步**

Run: `make check-types`(根)
Expected: 无 drift(本计划未改响应 schema)。

- [ ] **Step 3: 前端全量**

Run: `cd frontend && npm run type-check && npm run test && npm run build`
Expected: 全绿。

- [ ] **Step 4: 提交(若有修正)**

```bash
git add -A && git commit -m "chore: 操作留痕时间轴 通过全量校验"
```

---

## Self-Review

**Spec coverage:**
- 答复留痕 → Task 2 ✅;确认分类/改判/误报留痕 → Task 3 ✅(经 Task 1 helper 投影到 ticket 时间轴)
- 退回/拆分转单留痕 → Task 5(前端本地占位,后端动作待做)✅
- 时间轴展示操作节点(操作内容+处理人+时间)+ 历史 → Task 4 渲染 reason 为节点标题;转派/补料/关联建立已有 ✅
- graduate 毕业后时间轴刷新 → Task 4 补 invalidate ✅

**Placeholder scan:** 退回/拆分是产品占位(后端动作未做),Task 5 明确用本地节点 + 「待后端」标注,非计划占位。

**Type consistency:** `record_ticket_action(db, hub, *, action, changed_by, reason)` 签名在 Task 1 定义、Task 2/3 一致调用;前端 label 计算改动在 Task 4/5 一致。
