# 补料清单回填 + 答复后只读 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** (1) 补料工单(op_status=supplementing)的处理说明默认填 AI 生成的「需补充资料」清单(supply_note);(2) 提交答复后 op_status=answered/closed 时,处理说明、处理建议、提交按钮全部只读禁用。

**Architecture:** 全栈。后端在 HubIssueDetail 加只读派生字段 `supply_note`,从最新 auto_reply agent_decision 的 proposal.supply_note 取(仅 supplementing 态)。前端消费 hub 详情的 supply_note 作为补料态 textarea 默认值,并把只读判据从 ticket 的 DONE_STATUSES 扩展到 op_status answered/closed。

**Tech Stack:** FastAPI + Pydantic + SQLAlchemy(后端);React + TanStack Query + Vitest(前端);openapi 类型经 `make gen-types` 重新生成。

## Global Constraints

- 后端改 `backend/app/api/hub_issues.py`(HubIssueDetail schema + get_hub_issue handler)。
- op_status 权威常量 `app/services/hub_issues/op_status.py`:answered/supplementing/closed 等。
- supply_note 存处:`AgentDecision(decision_type="auto_reply", subject_type="hub_issue", subject_id=hub_id, proposal["supply_note"])`,取最新一条(id desc)。
- 改后端 API 后**必须** `make gen-types` 重新生成 `frontend/src/api/openapi.json` + `types.ts` 并提交,否则 CI `make check-types` 失败。
- 前端只改 `frontend/src/pages/tickets/TicketDetailPage.tsx` + `frontend/tests/TicketDetailPage.test.tsx`。
- 后端测试:`cd backend && .venv/bin/pytest tests/unit/... -v`;前端:`cd frontend && npm run test / type-check / build`。

---

### Task 1: 后端 HubIssueDetail 暴露 supply_note

**Files:**
- Modify: `backend/app/api/hub_issues.py`(HubIssueDetail :104-124 + get_hub_issue :266-287)
- Test: `backend/tests/unit/api/test_hub_issues_api.py`(定位实际路径,若无则找现有 hub_issues 测试文件追加)

**Interfaces:**
- Produces: `HubIssueDetail.supply_note: str | None`。当 hub.op_status=="supplementing" 且存在 auto_reply decision 带非空 supply_note 时返回该文本,否则 None。

- [ ] **Step 1: 写失败测试**

在 hub_issues API 测试文件加(参考现有 get_hub_issue 测试的建 hub + client fixture 写法):

```python
def test_get_hub_issue_exposes_supply_note_when_supplementing(client, db_session):
    # 建一个 Operation hub,op_status=supplementing,并写一条 auto_reply decision 带 supply_note
    hub = _make_operation_hub(db_session, op_status="supplementing")
    db_session.add(AgentDecision(
        decision_type="auto_reply", subject_type="hub_issue", subject_id=hub.id,
        proposal={"branch": "C", "supply_note": "请提供:1) 报错截图 2) 操作步骤 3) 单据编号"},
    ))
    db_session.commit()
    r = client.get(f"/api/hub-issues/{hub.id}", headers=_auth())
    assert r.status_code == 200
    assert r.json()["supply_note"] == "请提供:1) 报错截图 2) 操作步骤 3) 单据编号"


def test_get_hub_issue_supply_note_none_when_not_supplementing(client, db_session):
    hub = _make_operation_hub(db_session, op_status="answered")
    db_session.add(AgentDecision(
        decision_type="auto_reply", subject_type="hub_issue", subject_id=hub.id,
        proposal={"branch": "C", "supply_note": "不应返回"},
    ))
    db_session.commit()
    r = client.get(f"/api/hub-issues/{hub.id}", headers=_auth())
    assert r.json()["supply_note"] is None
```

用现有测试文件里既有的 hub 构造 helper(先读文件看命名,别硬造 `_make_operation_hub`——匹配现有 fixture)。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit -k supply_note -v`
Expected: FAIL(响应无 supply_note 字段 / KeyError)。

- [ ] **Step 3: 实现**

`HubIssueDetail` 加字段(在 :124 sub_issues 后):
```python
    # 补料清单:AI 判定需补料时生成的「需补充资料」文本(仅 op_status=supplementing 回填)
    supply_note: str | None = None
```

`get_hub_issue` handler 在 `return detail` 前加:
```python
    if hub.op_status == "supplementing":
        dec = (
            db.query(AgentDecision)
            .filter(
                AgentDecision.subject_type == "hub_issue",
                AgentDecision.subject_id == hub_issue_id,
                AgentDecision.decision_type == "auto_reply",
            )
            .order_by(AgentDecision.id.desc())
            .first()
        )
        if dec and dec.proposal:
            note = dec.proposal.get("supply_note")
            if note:
                detail.supply_note = str(note)
```
确认 `AgentDecision` 已 import(文件顶部;若无则 `from app.models import AgentDecision`)。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit -k supply_note -v`
Expected: PASS(2 passed)。

- [ ] **Step 5: 重新生成 openapi 类型**

Run: `make gen-types`(仓库根)
Expected: `frontend/src/api/openapi.json` + `frontend/src/api/types.ts` 更新,含 HubIssueDetail.supply_note。

- [ ] **Step 6: 提交**

```bash
git add backend/app/api/hub_issues.py backend/tests frontend/src/api/openapi.json frontend/src/api/types.ts
git commit -m "feat(hub-issues): HubIssueDetail 暴露补料清单 supply_note(supplementing 态)"
```

---

### Task 2: 前端补料态 textarea 默认填 supply_note

**Files:**
- Modify: `frontend/src/pages/tickets/TicketDetailPage.tsx`
- Test: `frontend/tests/TicketDetailPage.test.tsx`

**Interfaces:**
- Consumes: `hub.data.supply_note`(Task 1)、`hub.data.op_status`。
- Produces: op_status=="supplementing" 时处理说明 textarea 认值取 supply_note(而非空的 cached_reply_content);其它态维持 `cached_reply_content`。

- [ ] **Step 1: 写失败测试**

在 TicketDetailPage.test.tsx 加(hub mock 带 supply_note + op_status=supplementing):

```tsx
  it("补料态处理说明默认填 AI 的补充资料清单(supply_note)", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    server.use(
      http.get("*/api/tickets/340", () =>
        HttpResponse.json({
          id: 340, short_code: "TKT-340", source_code: "ksm", source_ticket_id: "ksm-340",
          type: "Raw", status: "in_progress", title: "补料工单", module: null, assigned_user_id: null,
          predicted_type: "Operation", ...baseTicket, hub_issue_id: 95, op_status: "supplementing",
          cached_reply_content: null,
        }),
      ),
      http.get("*/api/tickets/340/history", () => HttpResponse.json({ ticket_id: 340, items: [] })),
      http.get("*/api/hub-issues/95", () =>
        HttpResponse.json({
          id: 95, short_code: "HUB-95", type: "Operation", status: "created",
          op_status: "supplementing", supply_note: "请提供:1) 报错截图 2) 操作步骤",
        }),
      ),
    );
    renderPage(340);
    await screen.findByRole("heading", { name: "TKT-340" });
    const ta = document.querySelector("textarea") as HTMLTextAreaElement;
    expect(ta.value).toContain("请提供:1) 报错截图 2) 操作步骤");
    localStorage.clear();
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run TicketDetailPage -t 补料态`
Expected: FAIL(textarea 值为空,不含 supply_note)。

- [ ] **Step 3: 实现**

在 textarea 默认值逻辑(现 `const val = noteDrafts[0] ?? (d.cached_reply_content ?? "");`)改为补料态优先 supply_note:
```tsx
                        const supplyNote =
                          d.op_status === "supplementing" ? (hub.data?.supply_note ?? "") : "";
                        const val = noteDrafts[0] ?? (d.cached_reply_content || supplyNote || "");
```
(注意用 `||` 让空串回落到 supplyNote;cached_reply_content 补料态为空。)

同理提交答复取值处(`const content = (noteDrafts[0] ?? d.cached_reply_content ?? "").trim();`)保持不变——补料态一般不直接提交答复,但若主管编辑后提交,noteDrafts[0] 已有值,不受影响。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run TicketDetailPage -t 补料态`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/tickets/TicketDetailPage.tsx frontend/tests/TicketDetailPage.test.tsx
git commit -m "feat(ticket-detail): 补料态处理说明默认填 AI 补充资料清单"
```

---

### Task 3: 答复后(answered/closed)处理说明+处理建议+提交按钮只读

**Files:**
- Modify: `frontend/src/pages/tickets/TicketDetailPage.tsx`
- Test: `frontend/tests/TicketDetailPage.test.tsx`

**Interfaces:**
- Consumes: `d.op_status`。
- Produces: op_status ∈ {answered, closed} 时:处理说明 textarea readOnly;处理建议下拉 disabled;提交答复按钮 disabled。

- [ ] **Step 1: 写失败测试**

```tsx
  it("答复后(op_status=answered)处理说明只读、处理建议禁用、提交按钮禁用", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    stubOperationTicket(341, { op_status: "answered", cached_reply_content: "已发出的答复" });
    server.use(
      http.get("*/api/hub-issues/88", () =>
        HttpResponse.json({ id: 88, short_code: "HUB-88", type: "Operation", status: "created", op_status: "answered" }),
      ),
    );
    renderPage(341);
    await screen.findByRole("heading", { name: "TKT-341" });
    const ta = document.querySelector("textarea") as HTMLTextAreaElement;
    expect(ta.readOnly).toBe(true);
    // 处理建议下拉禁用
    const sel = screen.getByDisplayValue("常跟进") as HTMLSelectElement;
    expect(sel.disabled).toBe(true);
    // 提交答复按钮禁用
    expect(screen.getByRole("button", { name: "提交答复" })).toBeDisabled();
    localStorage.clear();
  });
```

注意 stubOperationTicket 的 ticket op_status 也要传 answered(它 overrides 生效);hub mock 88 覆盖成 answered。ticket.status 保持 in_progress(证明只读靠 op_status 而非 ticket status)。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run TicketDetailPage -t 答复后`
Expected: FAIL(textarea 可编辑 / 下拉未禁用 / 按钮未禁用)。

- [ ] **Step 3: 实现**

加派生量(在 isOperation 附近):
```tsx
  // 答复完成(answered)或已关单(closed):处理区只读,不可再编辑/提交
  const opDone = d?.op_status === "answered" || d?.op_status === "closed";
```

改三处:
1. textarea editable 判据(现 `const editable = !DONE_STATUSES.includes(d.status);`)→ `const editable = !DONE_STATUSES.includes(d.status) && !opDone;`
2. 处理建议 select 加 `disabled={opDone}`。
3. 提交答复按钮 disabled 现为 `reply.isPending || suggestion === "split"` → 加 `|| opDone`。补一行只读提示文案(opDone 时):`{opDone && <span className="ml-2 text-[10.5px] text-hub-textFaint">已{d.op_status === "closed" ? "关单" : "答复完成"},不可再编辑</span>}`

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run TicketDetailPage -t 答复后`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/tickets/TicketDetailPage.tsx frontend/tests/TicketDetailPage.test.tsx
git commit -m "feat(ticket-detail): 答复完成/关单后处理区只读禁用"
```

---

### Task 4: 全量验证

- [ ] **Step 1: 后端 lint + 相关单测**

Run: `cd backend && make lint && .venv/bin/pytest tests/unit -k "hub_issue" -v`
Expected: clean + pass。

- [ ] **Step 2: 类型同步校验**

Run: `make check-types`(根)
Expected: openapi.json / types.ts 与后端同步(无 diff)。

- [ ] **Step 3: 前端全量**

Run: `cd frontend && npm run type-check && npm run test && npm run build`
Expected: 全绿。

- [ ] **Step 4: 提交(若有修正)**

```bash
git add -A && git commit -m "chore: 补料回填+答复只读 通过全量校验"
```

---

## Self-Review

**Spec coverage:**
- 补料工单处理说明默认填 AI 补充资料清单 → Task 1(后端暴露 supply_note)+ Task 2(前端回填)✅
- 提交答复后 answered/closed 处理说明+处理建议+提交只读 → Task 3 ✅

**Placeholder scan:** 测试 helper `_make_operation_hub` 需匹配现有 fixture 命名(Step 1 已注明先读文件)。

**Type consistency:** `supply_note` 后端 schema 字段 ↔ 前端 `hub.data.supply_note`(经 gen-types 类型化);`opDone` 派生量在 Task 3 内一致使用。
