# 工单手动毕业 + Operation 回复权限 gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** #5 OperationReplySection 加 isSupervisor gate（非主管只读回复）；#3 工单详情页加「毕业为 hub_issue」按钮（supervisor-only，未毕业时显示）。纯前端，后端就绪。

**Architecture:** 新建 `src/api/auth.ts` 共享 `currentRole`/`isSupervisor`。HubIssueDetailPage 换用共享版 + OperationReplySection 加 gate。TicketDetailPage 加毕业 section（用共享 auth + create-hub-issue mutation，照搬 WorkbenchPage convertComplaint）。渐进抽取——WorkbenchPage/HubIssuesListPage 本地版先留。

**Tech Stack:** React 18 + TS + TanStack Query + vitest。复用 api.post/HUB_TYPES/create-hub-issue。

## Global Constraints

- 渐进抽取：只新代码 + HubIssueDetailPage 用 `src/api/auth.ts`；WorkbenchPage/HubIssuesListPage 本地 currentRole 先不动。
- #5 gate 照 owner-split（HubIssueDetailPage.tsx:368）：组件体 `const supervisor = isSupervisor()` + `{supervisor && ...}`。
- #3 mutation 照 WorkbenchPage convertComplaint（:491）：`api.post("/api/supervisor/create-hub-issue", { ticket_id, type })`。
- #3 显示条件：isSupervisor 且 `detail.hub_issue_id == null`。
- 前端全量测试不回归（现 20 passed）。
- 分支 `feat/ticket-graduate-reply-gate`（已建，spec 已提交）。每 Task commit。

---

### Task 1: 共享 auth helper + 单测

**Files:**
- Create: `frontend/src/api/auth.ts`
- Test: `frontend/tests/auth.test.ts`

**Interfaces:**
- Produces: `currentRole(): string`、`isSupervisor(): boolean`

- [ ] **Step 1: 写失败测试**

```ts
// frontend/tests/auth.test.ts
import { describe, it, expect, afterEach } from "vitest";
import { currentRole, isSupervisor } from "@/api/auth";

describe("auth helpers", () => {
  afterEach(() => localStorage.clear());

  it("currentRole 读 auth_user.role", () => {
    localStorage.setItem("auth_user", JSON.stringify({ name: "u", role: "supervisor" }));
    expect(currentRole()).toBe("supervisor");
  });

  it("无 auth_user 返回空串", () => {
    expect(currentRole()).toBe("");
  });

  it("坏 JSON 不抛，返回空串", () => {
    localStorage.setItem("auth_user", "not-json");
    expect(currentRole()).toBe("");
  });

  it("isSupervisor：supervisor/admin 为真，其余假", () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "admin" }));
    expect(isSupervisor()).toBe(true);
    localStorage.setItem("auth_user", JSON.stringify({ role: "member" }));
    expect(isSupervisor()).toBe(false);
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run tests/auth.test.ts 2>&1 | tail -8`
Expected: FAIL（@/api/auth 不存在）

- [ ] **Step 3: 建 auth.ts**

```ts
// frontend/src/api/auth.ts
/** 从 localStorage.auth_user 读当前角色（跨页面共享，渐进替代各页面局部版）。 */
export function currentRole(): string {
  try {
    return (JSON.parse(localStorage.getItem("auth_user") ?? "null") as { role?: string } | null)?.role ?? "";
  } catch {
    return "";
  }
}

export function isSupervisor(): boolean {
  const r = currentRole();
  return r === "supervisor" || r === "admin";
}
```

- [ ] **Step 4: 运行确认通过**

Run: `cd frontend && npx vitest run tests/auth.test.ts 2>&1 | tail -8`
Expected: PASS（4 tests）

- [ ] **Step 5: 提交**

```bash
git add frontend/src/api/auth.ts frontend/tests/auth.test.ts
git commit -m "feat(auth): 共享 currentRole/isSupervisor helper"
```

---

### Task 2: #5 OperationReplySection 权限 gate

**Files:**
- Modify: `frontend/src/pages/hub-issues/HubIssueDetailPage.tsx`
- Test: `frontend/tests/HubIssueDetailPage.test.tsx`（追加）

**Interfaces:**
- Consumes: `isSupervisor`（Task 1）
- Produces: OperationReplySection 非主管隐藏编辑入口

- [ ] **Step 1: 写失败测试**

```tsx
// 追加到 tests/HubIssueDetailPage.test.tsx（仿现有渲染方式，mock role + Operation hub）
// 参照文件现有测试的 render/msw 写法；核心断言：
// supervisor 看到「撰写回复」/「修改回复」按钮；member 看不到。
// 若现有测试已有 renderDetail helper 复用之，给 Operation 类型 + reply_content 空/非空各测一次。
```
> 实现时先读 tests/HubIssueDetailPage.test.tsx 现有结构（msw mock hub-issue detail 的方式），追加两个 case：role=supervisor 断言 `screen.getByText(/撰写回复|修改回复/)` 存在；role=member 断言 `queryByText` 为 null。Operation hub 需 msw 返回 `type:"Operation"`。

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run tests/HubIssueDetailPage.test.tsx 2>&1 | tail -10`
Expected: member 用例 FAIL（当前按钮对所有人渲染）

- [ ] **Step 3: 改 HubIssueDetailPage**

- 顶部 import：`import { isSupervisor } from "@/api/auth";`
- 删除本文件局部 `isSupervisor()` 定义（:11-18）——改用共享版（渐进抽取里 HubIssueDetailPage 是要换的那个）。owner-split 处（:368）`const supervisor = isSupervisor();` 不变（现在指向 import 版）。
- `OperationReplySection`（:192）体内加 `const supervisor = isSupervisor();`
- 编辑按钮块（:222）：`{!editing && (...)}` → `{supervisor && !editing && (...)}`

> 注意：删本地 isSupervisor 后，确认文件内所有调用点（:368 等）都还能解析到 import 版。若本地版签名与共享版一致（无参、返回 bool），直接删即可。

- [ ] **Step 4: 运行确认通过**

Run: `cd frontend && npx vitest run tests/HubIssueDetailPage.test.tsx 2>&1 | tail -8`
Expected: PASS

- [ ] **Step 5: type-check + 提交**

```bash
cd frontend && npm run type-check 2>&1 | tail -3
cd .. && git add frontend/src/pages/hub-issues/HubIssueDetailPage.tsx frontend/tests/HubIssueDetailPage.test.tsx
git commit -m "feat(hub-issue): Operation 回复编辑器加 isSupervisor gate（换用共享 auth）"
```

---

### Task 3: #3 工单手动毕业按钮

**Files:**
- Modify: `frontend/src/pages/tickets/TicketDetailPage.tsx`
- Create: `frontend/src/api/hubTypes.ts`
- Test: `frontend/tests/TicketDetailPage.test.tsx`（追加）

**Interfaces:**
- Consumes: `isSupervisor`（Task 1）、`HUB_TYPES`/`HUB_TYPE_LABELS`（新）、`api.post`
- Produces: 未毕业工单 + supervisor 显示「毕业为 hub_issue」按钮

- [ ] **Step 1: 建 hubTypes.ts**

```ts
// frontend/src/api/hubTypes.ts
export const HUB_TYPES = ["Operation", "Bug_fix", "Demand", "Internal_task"] as const;
export const HUB_TYPE_LABELS: Record<string, string> = {
  Operation: "运营", Bug_fix: "Bug 修复", Demand: "需求", Internal_task: "内部任务",
};
```

- [ ] **Step 2: 写失败测试**

```tsx
// 追加到 tests/TicketDetailPage.test.tsx（仿现有 render + msw）
// 断言：
// - role=supervisor + 工单 hub_issue_id=null → 「毕业为 hub_issue」按钮存在
// - role=supervisor + hub_issue_id 非空 → 按钮不存在（显示 HUB 链接）
// - role=member → 按钮不存在
```
> 实现时读现有 TicketDetailPage.test.tsx 的 msw mock 方式（mock `/api/tickets/{id}` 返回带/不带 hub_issue_id）。追加上述三 case。点按钮 case 可 mock api.post 断言 body。

- [ ] **Step 3: 运行确认失败**

Run: `cd frontend && npx vitest run tests/TicketDetailPage.test.tsx 2>&1 | tail -10`
Expected: supervisor 未毕业用例 FAIL（无按钮）

- [ ] **Step 4: 改 TicketDetailPage**

- import 补：`useMutation, useQueryClient`（现只 useQuery）、`api`（现只 getByPath——加 `import { api, getByPath } from "@/api/client"` 或按现有导入形式）、`isSupervisor`（@/api/auth）、`HUB_TYPES, HUB_TYPE_LABELS`（@/api/hubTypes）、`useState`
- 基本信息 section 后加 supervisor-only 毕业 section：
  ```tsx
  {isSupervisor() && detail.data && detail.data.hub_issue_id == null && (
    <section className="...">
      <select value={gradType} onChange={...}>
        {HUB_TYPES.map((t) => <option key={t} value={t}>{HUB_TYPE_LABELS[t]}</option>)}
      </select>
      <button disabled={grad.isPending} onClick={() => grad.mutate()}>毕业为 hub_issue</button>
      {grad.isError && <p className="text-hub-rose">{errMsg(grad.error)}</p>}
    </section>
  )}
  ```
  - `gradType` state 默认 `detail.data.predicted_type ?? "Operation"`
  - mutation：`api.post("/api/supervisor/create-hub-issue", { ticket_id: Number(id), type: gradType })`，onSuccess `qc.invalidateQueries({ queryKey: ["ticket-detail", id] })`（核对现有 queryKey 名）
  - errMsg：TicketDetailPage 无——内联 `e instanceof Error ? e.message : "失败"` 或从 client 引入

> 核对：TicketDetailPage 现有 query 的 queryKey（Task4 invalidate 要对）、detail 数据的访问路径（`detail.data.xxx` vs 直接 `detail.xxx`）、id 来源（useParams）。

- [ ] **Step 5: 运行确认通过**

Run: `cd frontend && npx vitest run tests/TicketDetailPage.test.tsx 2>&1 | tail -8`
Expected: PASS

- [ ] **Step 6: type-check + 提交**

```bash
cd frontend && npm run type-check 2>&1 | tail -3
cd .. && git add frontend/src/pages/tickets/TicketDetailPage.tsx frontend/src/api/hubTypes.ts frontend/tests/TicketDetailPage.test.tsx
git commit -m "feat(ticket): 工单详情页手动毕业为 hub_issue（supervisor-only）"
```

---

### Task 4: 全量验证 + 合并部署

- [ ] **Step 1: 全量前端 vitest + type-check**

Run: `cd frontend && npm run type-check && npx vitest run 2>&1 | tail -12`
Expected: 全绿（含既有 20 + 新增，不回归）

- [ ] **Step 2: build**

Run: `cd frontend && npm run build 2>&1 | tail -5`
Expected: 成功

- [ ] **Step 3: 合并 main + push + SIT 前端部署**

```bash
git checkout main && git merge --no-ff feat/ticket-graduate-reply-gate
git push origin main
ssh root@sit "cd /data/hub-issue && git pull && deploy/build-frontend.sh /data/hub-issue/frontend-dist"
```

- [ ] **Step 4: 验证**

浏览器 SIT：supervisor 登录 → Operation hub 详情页看到「修改回复」；member 看不到。未毕业工单详情页看到「毕业为 hub_issue」，点击后毕业成功显示 HUB 链接。

---

## Self-Review

**Spec 覆盖**：§3 #5 gate→Task2；§4 #3 毕业→Task3；§5 共享 helper→Task1（auth.ts）+Task3（hubTypes.ts）；测试→各 Task+Task4。全覆盖。

**占位扫描**：Task2/Task3 的测试 Step 标注"实现时读现有测试结构追加"——因现有 HubIssueDetailPage.test/TicketDetailPage.test 的 msw mock 细节需读后确定，属合理的实现时确认，非空占位（断言目标明确）。

**类型一致性**：`isSupervisor(): boolean`/`currentRole(): string` Task1 定义、Task2/3 用一致；create-hub-issue body `{ ticket_id, type }` 与后端一致；HUB_TYPES 值域与后端 CHECK 一致。

**实现时校验点**：
1. HubIssueDetailPage 删本地 isSupervisor 后，文件内所有调用点解析到 import 版（Task2 Step3）
2. TicketDetailPage 的 queryKey 名、detail 数据访问路径、id 来源（Task3 Step4）
3. 现有 HubIssueDetailPage.test / TicketDetailPage.test 的 msw mock 结构（Task2/3 测试追加需对齐）
4. errMsg 在 TicketDetailPage 无定义——内联或引入
