# 工单详情「工单处理」栏重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把工单详情页「工单处理」右侧详情区改成分类闸门驱动的处理流——未明确分类只显示改判、明确后按类型分流（研发推 Linear / 运营 AI 答复 + 处理意见三动作），子任务列表未拆分时回落显示当前工单本身。

**Architecture:** 纯前端，只改 `frontend/src/pages/tickets/TicketDetailPage.tsx`，复用现有 `POST /api/supervisor/create-hub-issue` 与 `POST /api/hub-issues/{hub_issue_id}/reply` 两接口。分类判定信号用 `hub_issue_id == null`（未毕业=未明确分类）。退回转单/拆分转单/出站附件均为前端占位「待后端」。

**Tech Stack:** React 18 + TypeScript + TanStack Query + Tailwind + Vitest + Testing Library + msw。

## Global Constraints

- 只改 `frontend/src/pages/tickets/TicketDetailPage.tsx` 及其新增测试文件；不改后端、不改 openapi 类型。
- API 调用用 `api.post(path, body)`（`@/api/client`）；带路径参数的用 `postByPath`。
- 类型/标签复用 `@/api/hubTypes`：`HUB_TYPES = ["Operation","Bug_fix","Demand","Internal_task"]`、`HUB_TYPE_LABELS`。
- 主管专属动作用 `isSupervisor()`（`@/api/auth`）门控，与文件既有用法一致。
- 分类判定信号：**未明确分类 = `d.hub_issue_id == null`**；已明确 = 非空，按 `d.predicted_type` 分流。
- 答复只带文本（reply 接口 body 仅 `{ content: string }`）；处理附件区仅展示占位，不参与发送。
- 中文文案、占位风格与文件既有「（执行逻辑待后端）」一致。
- 验证命令（在 `frontend/` 下）：`npm run test`、`npm run type-check`、`npm run build`。

---

### Task 1: 提取分类闸门判定 + 单测建档

**Files:**
- Modify: `frontend/src/pages/tickets/TicketDetailPage.tsx`
- Test: `frontend/src/pages/tickets/TicketDetailPage.test.tsx`（Create）

**Interfaces:**
- Consumes: `TicketDetailData`（已在文件内，`paths["/api/tickets/{ticket_id}"]...`）、`getByPath`。
- Produces: 组件在 `d.hub_issue_id == null` 时右侧详情区渲染「分类改判区」（含文案「确认分类」），且**不渲染**「处理建议」「处理说明」「处理附件 / 补充凭证」三块标题。后续任务依赖此分支存在。

建测试脚手架（照抄 `TicketsListPage.test.tsx` 的 QueryClientProvider + MemoryRouter + msw-server 套路），并加第一条闸门断言。

- [ ] **Step 1: 写失败测试（分类改判分支）**

Create `frontend/src/pages/tickets/TicketDetailPage.test.tsx`：

```tsx
import { describe, it, expect, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "../../../tests/msw-server";
import { TicketDetailPage } from "./TicketDetailPage";

function renderDetail() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/tickets/1"]}>
        <Routes>
          <Route path="/tickets/:ticketId" element={<TicketDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// 最小工单详情载荷（按需覆盖字段）
function ticket(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    short_code: "TKT-1",
    source_code: "ksm",
    source_ticket_id: "k1",
    type: "Raw",
    status: "received",
    title: "测试工单",
    predicted_type: "Bug_fix",
    hub_issue_id: null,
    op_status: null,
    children_ticket_ids: [],
    cached_reply_content: null,
    cached_reply_version: null,
    assigned_user_id: 1,
    assigned_user_name: "张三",
    attachments: [],
    source_payload: {},
    ...overrides,
  };
}

function mockTicket(t: Record<string, unknown>) {
  server.use(
    http.get("*/api/tickets/1", () => HttpResponse.json(t)),
    http.get("*/api/tickets/1/history", () =>
      HttpResponse.json({ items: [] }),
    ),
  );
}

afterEach(() => localStorage.clear());

describe("TicketDetailPage 工单处理栏", () => {
  it("未明确分类(hub_issue_id 为空)只显示分类改判，隐藏处理建议/说明/附件", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    mockTicket(ticket({ hub_issue_id: null }));
    renderDetail();

    expect(await screen.findByText("确认分类")).toBeInTheDocument();
    expect(screen.queryByText("处理建议")).not.toBeInTheDocument();
    expect(screen.queryByText("处理说明")).not.toBeInTheDocument();
    expect(screen.queryByText("处理附件 / 补充凭证")).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npm run test -- TicketDetailPage`
Expected: FAIL —「确认分类」找不到 / 「处理建议」仍在 DOM。

- [ ] **Step 3: 加闸门标志 + 抽出改判区（最小实现）**

在 `TicketDetailPage` 组件内、`const d = detail.data;` 之后加：

```tsx
  // 分类闸门：未毕业成 hub_issue = 分类未明确，只显示改判区
  const classified = d?.hub_issue_id != null;
  // 分类改判本地态：类型选择（默认 predicted_type，回落 Operation）
  const [classifyType, setClassifyType] = useState<string>("Operation");
  useEffect(() => {
    if (d?.predicted_type && HUB_TYPES.includes(d.predicted_type as (typeof HUB_TYPES)[number])) {
      setClassifyType(d.predicted_type);
    }
  }, [d?.predicted_type]);
```

把右侧详情区（`{/* 5.2 右：节点处理详情 */}` 那个 `<div className="space-y-5 ...">` 内部）改成条件渲染。将现有「处理建议 / 处理说明 / 处理附件」三块用 `{classified && (...)}` 包裹，并在 `{!classified && (...)}` 分支渲染改判区：

```tsx
                {!classified && (
                  <div>
                    <div className="text-[11px] font-bold text-hub-textMuted tracking-wide mb-1.5">
                      分类改判
                    </div>
                    <select
                      value={classifyType}
                      onChange={(e) => setClassifyType(e.target.value)}
                      className="text-[12.5px] border border-hub-border rounded-[7px] px-2.5 py-1.5 bg-hub-panel outline-none focus:border-hub-teal focus:bg-white"
                    >
                      {HUB_TYPES.map((t) => (
                        <option key={t} value={t}>
                          {HUB_TYPE_LABELS[t]}
                        </option>
                      ))}
                    </select>
                    <div className="mt-2">
                      <button
                        type="button"
                        onClick={() => graduate.mutate()}
                        disabled={graduate.isPending}
                        className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-hub-teal text-white hover:brightness-95 disabled:opacity-40"
                      >
                        {graduate.isPending ? "确认中…" : "确认分类"}
                      </button>
                      {gradErr && <span className="ml-2 text-[11px] text-hub-rose">{gradErr}</span>}
                    </div>
                    <div className="mt-1 text-[10.5px] text-hub-textFaint">
                      确认后：Bug 修复 / 需求 直接推送 Linear；运营 由 AI 答复后人工确认发出。
                    </div>
                  </div>
                )}
```

处理状态 `<Field label="处理状态">` 保持在两分支之外始终显示。`useEffect` 需确认已在文件顶部 import（现状已 `import { useEffect, useMemo, useRef, useState }`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npm run test -- TicketDetailPage`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/tickets/TicketDetailPage.tsx frontend/src/pages/tickets/TicketDetailPage.test.tsx
git commit -m "feat(ticket-detail): 分类闸门——未明确分类只显示改判区"
```

---

### Task 2: 改判用选中类型 + 明确分类后按类型分流展示

**Files:**
- Modify: `frontend/src/pages/tickets/TicketDetailPage.tsx`
- Test: `frontend/src/pages/tickets/TicketDetailPage.test.tsx`

**Interfaces:**
- Consumes: Task 1 的 `classified`、`classifyType`、`graduate` mutation。
- Produces: 明确分类且 `predicted_type ∈ {Bug_fix, Demand}` 时详情区显示「已推送 Linear」提示、不显示对客答复编辑；运营类显示处理建议/说明/附件三块。`graduate` mutation 用 `classifyType` 而非写死 `predicted_type`。

- [ ] **Step 1: 写失败测试（研发类分流 + 改判入参）**

在 `TicketDetailPage.test.tsx` 的 describe 内追加：

```tsx
  it("明确分类的 Bug_fix 显示已推送 Linear，不显示处理建议下拉", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    mockTicket(ticket({ hub_issue_id: 7, predicted_type: "Bug_fix" }));
    renderDetail();

    expect(await screen.findByText(/已推送 Linear/)).toBeInTheDocument();
    expect(screen.queryByText("处理建议")).not.toBeInTheDocument();
  });

  it("明确分类的 Operation 显示处理建议 + 处理说明", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    mockTicket(
      ticket({ hub_issue_id: 8, predicted_type: "Operation", op_status: "processing" }),
    );
    renderDetail();

    expect(await screen.findByText("处理建议")).toBeInTheDocument();
    expect(screen.getByText("处理说明")).toBeInTheDocument();
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npm run test -- TicketDetailPage`
Expected: FAIL —「已推送 Linear」不存在；Bug_fix 分支下「处理建议」仍显示。

- [ ] **Step 3: 实现类型分流**

改 `graduate` mutation body 用选中类型：

```tsx
  const graduate = useMutation({
    mutationFn: () =>
      api.post("/api/supervisor/create-hub-issue", {
        ticket_id: id,
        type: classifyType,
      }),
    onSuccess: () => {
      setGradErr(null);
      void qc.invalidateQueries({ queryKey: ["ticket-detail", id] });
      void qc.invalidateQueries({ queryKey: ["ticket-history", id] });
      void qc.invalidateQueries({ queryKey: ["tickets"] });
      void qc.invalidateQueries({ queryKey: ["hub-issues"] });
    },
    onError: (e) => setGradErr(e instanceof ApiError ? e.message : String(e)),
  });
```

在详情区加派生量与分流。把 Task 1 里包住三块的 `{classified && (...)}` 细分：

```tsx
  const isDevType = d?.predicted_type === "Bug_fix" || d?.predicted_type === "Demand";
  const isOperation = d?.predicted_type === "Operation";
```

- 研发类（`classified && isDevType`）显示：

```tsx
                {classified && isDevType && (
                  <div className="border border-hub-blue-border bg-hub-blue-light rounded-[8px] px-3 py-2.5 text-[12px] text-hub-blue-deep">
                    已推送 Linear（{HUB_TYPE_LABELS[d.predicted_type] ?? d.predicted_type} 类工单由研发在 Linear 跟进，无需在此对客答复）
                  </div>
                )}
```

- 运营类（`classified && isOperation`）显示处理建议 / 处理说明 / 处理附件三块（Task 1 里已存在的三块，改为受 `classified && isOperation` 控制）。
- 其余（Internal_task 等）明确分类但非运营/研发：显示中性提示，不显示对客答复：

```tsx
                {classified && !isDevType && !isOperation && (
                  <div className="border border-hub-borderLight rounded-[8px] px-3 py-2.5 text-[12px] text-hub-textFaint bg-hub-panel">
                    内部任务已建立，无对客答复流程。
                  </div>
                )}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npm run test -- TicketDetailPage`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/tickets/TicketDetailPage.tsx frontend/src/pages/tickets/TicketDetailPage.test.tsx
git commit -m "feat(ticket-detail): 明确分类按类型分流——研发推Linear/运营答复"
```

---

### Task 3: 处理意见下拉改造（正常跟进/退回转单/拆分转单）+ 确认答复

**Files:**
- Modify: `frontend/src/pages/tickets/TicketDetailPage.tsx`
- Test: `frontend/src/pages/tickets/TicketDetailPage.test.tsx`

**Interfaces:**
- Consumes: Task 2 的运营分支、文件内 `suggestion`/`setSuggestion` 状态、`noteDrafts`。
- Produces: 运营类详情区有「确认」类动作按钮，正常跟进时点击调用 `POST /api/hub-issues/{hub_issue_id}/reply { content }`；退回转单/拆分转单为前端占位不发请求。下拉三值：`normal`/`return`/`split`。

处理意见下拉现状已是 normal/return/split 三值（`option value` 正是这三个）。本任务补三值对应确认动作 + reply 调用。

- [ ] **Step 1: 写失败测试（正常跟进发答复）**

在 describe 内追加：

```tsx
  it("运营正常跟进——点提交答复调用 reply 接口", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    let replyBody: unknown = null;
    server.use(
      http.get("*/api/tickets/1", () =>
        HttpResponse.json(
          ticket({
            hub_issue_id: 8,
            predicted_type: "Operation",
            op_status: "processing",
            cached_reply_content: "AI 建议的答复内容",
          }),
        ),
      ),
      http.get("*/api/tickets/1/history", () => HttpResponse.json({ items: [] })),
      http.post("*/api/hub-issues/8/reply", async ({ request }) => {
        replyBody = await request.json();
        return HttpResponse.json({
          hub_issue_id: 8,
          version: 1,
          cascaded_ticket_count: 1,
          outbox_count: 1,
        });
      }),
    );
    const user = userEvent.setup();
    renderDetail();

    const btn = await screen.findByRole("button", { name: "提交答复" });
    await user.click(btn);
    await waitFor(() => expect(replyBody).not.toBeNull());
    expect((replyBody as { content: string }).content).toBe("AI 建议的答复内容");
  });
```

`waitFor` 需 import：`import { render, screen, waitFor } from "@testing-library/react";`。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npm run test -- TicketDetailPage`
Expected: FAIL —「提交答复」按钮不存在。

- [ ] **Step 3: 实现 reply mutation + 三动作确认**

加 mutation（`hub_issue_id` 非空时可用）：

```tsx
  const [replyErr, setReplyErr] = useState<string | null>(null);
  const reply = useMutation({
    mutationFn: (content: string) =>
      postByPath(
        "/api/hub-issues/{hub_issue_id}/reply",
        { hub_issue_id: d?.hub_issue_id ?? 0 },
        { content },
      ),
    onSuccess: () => {
      setReplyErr(null);
      void qc.invalidateQueries({ queryKey: ["ticket-detail", id] });
      void qc.invalidateQueries({ queryKey: ["ticket-history", id] });
      void qc.invalidateQueries({ queryKey: ["hub-issues"] });
    },
    onError: (e) => setReplyErr(e instanceof ApiError ? e.message : String(e)),
  });
```

`postByPath` 需从 `@/api/client` import（现状已 `import { api, ApiError, getByPath } from "@/api/client";` → 补 `postByPath`）。

在运营分支的处理建议区下方（或处理附件区之后）加动作按钮：

```tsx
                {classified && isOperation && (
                  <div>
                    <button
                      type="button"
                      disabled={reply.isPending || suggestion === "split"}
                      onClick={() => {
                        if (suggestion === "normal") {
                          const content = (noteDrafts[0] ?? d.cached_reply_content ?? "").trim();
                          if (!content) {
                            setReplyErr("处理说明为空，无法答复");
                            return;
                          }
                          reply.mutate(content);
                        } else if (suggestion === "return") {
                          setConfirmNotice("退回转单：打回工单逻辑待后端接口，暂未执行");
                        } else if (suggestion === "split") {
                          setConfirmNotice("拆分转单：拆分逻辑后续版本支持");
                        }
                      }}
                      className="px-3.5 py-1.5 text-[12px] font-semibold rounded-[7px] bg-hub-teal text-white hover:brightness-95 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      {suggestion === "normal"
                        ? reply.isPending
                          ? "提交中…"
                          : "提交答复"
                        : suggestion === "return"
                          ? "退回转单"
                          : "拆分转单（待后端）"}
                    </button>
                    {replyErr && <span className="ml-2 text-[11px] text-hub-rose">{replyErr}</span>}
                  </div>
                )}
```

`suggestion` 下拉已存在于运营分支内。`confirmNotice` 状态已在文件内。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npm run test -- TicketDetailPage`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/tickets/TicketDetailPage.tsx frontend/src/pages/tickets/TicketDetailPage.test.tsx
git commit -m "feat(ticket-detail): 处理意见三动作——正常跟进发答复/退回/拆分占位"
```

---

### Task 4: AI 草稿待审核提示（op_status=reviewing）

**Files:**
- Modify: `frontend/src/pages/tickets/TicketDetailPage.tsx`
- Test: `frontend/src/pages/tickets/TicketDetailPage.test.tsx`

**Interfaces:**
- Consumes: Task 2 运营分支、`d.op_status`。
- Produces: `op_status === "reviewing"` 时处理说明文本框上方显示黄色提示「AI 草稿待审核，确认后正式发出」。

- [ ] **Step 1: 写失败测试**

```tsx
  it("op_status=reviewing 显示 AI 草稿待审核提示", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    mockTicket(
      ticket({ hub_issue_id: 9, predicted_type: "Operation", op_status: "reviewing" }),
    );
    renderDetail();
    expect(await screen.findByText(/AI 草稿待审核/)).toBeInTheDocument();
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npm run test -- TicketDetailPage`
Expected: FAIL — 提示不存在。

- [ ] **Step 3: 实现提示**

在运营分支「处理说明」标题块之后、textarea 之前插入：

```tsx
                    {d.op_status === "reviewing" && (
                      <div className="mb-1.5 text-[11px] text-hub-amber-deep bg-hub-amber-light border border-hub-amber-border rounded px-2 py-1">
                        AI 草稿待审核，确认后正式发出
                      </div>
                    )}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npm run test -- TicketDetailPage`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/tickets/TicketDetailPage.tsx frontend/src/pages/tickets/TicketDetailPage.test.tsx
git commit -m "feat(ticket-detail): 运营 AI 草稿待审核提示"
```

---

### Task 5: 子任务列表未拆分时回落显示当前工单本身

**Files:**
- Modify: `frontend/src/pages/tickets/TicketDetailPage.tsx`（`SubTicketList` 组件）
- Test: `frontend/src/pages/tickets/TicketDetailPage.test.tsx`

**Interfaces:**
- Consumes: `SubTicketList({ childIds, drafts })`，新增一个「当前工单」入参用于回落。
- Produces: `childIds` 为空且无草稿时，列表显示当前工单本身一行（编号=当前 short_code、说明=title、类型=predicted_type、状态、处理人、解决方案=cached_reply_content），不再显示「无子任务」。

- [ ] **Step 1: 写失败测试**

```tsx
  it("未拆分时子任务列表回落显示当前工单本身", async () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
    mockTicket(
      ticket({
        hub_issue_id: 8,
        predicted_type: "Operation",
        op_status: "processing",
        children_ticket_ids: [],
        title: "测试工单标题",
      }),
    );
    renderDetail();
    // 「无子任务」不再出现；当前工单标题出现在子任务表中
    expect(await screen.findByText("子任务列表")).toBeInTheDocument();
    expect(screen.queryByText("无子任务")).not.toBeInTheDocument();
    // 当前工单标题在子任务表出现（工单描述区也有一处，用 getAllByText 容忍）
    expect(screen.getAllByText("测试工单标题").length).toBeGreaterThan(0);
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npm run test -- TicketDetailPage`
Expected: FAIL —「无子任务」仍在 DOM。

- [ ] **Step 3: 实现回落行**

给 `SubTicketList` 加 `self` 入参（当前工单快照），空时渲染 self 行。改签名：

```tsx
function SubTicketList({
  childIds,
  drafts,
  self,
}: {
  childIds: number[];
  drafts: { title: string; type: string }[];
  self: {
    short_code: string;
    title: string | null;
    predicted_type: string | null;
    status: string;
    assigned_user_name: string | null;
    assigned_user_id: number | null;
    cached_reply_content: string | null;
  };
}) {
```

把 `const empty = ...` 改为：当 `childIds.length === 0 && drafts.length === 0` 时渲染一行 self（不再是「无子任务」空态）。在 `<tbody>` 内，把原 `empty` 分支替换为：

```tsx
          {childIds.length === 0 && drafts.length === 0 && (
            <tr className="border-t border-hub-borderLight hover:bg-hub-panel">
              <td className="px-2.5 py-1.5 whitespace-nowrap">
                <span className="font-mono text-hub-textMuted">{self.short_code}</span>
              </td>
              <td className="px-2.5 py-1.5 max-w-[220px] truncate" title={self.title ?? ""}>
                {self.title ?? "—"}
              </td>
              <td className="px-2.5 py-1.5 whitespace-nowrap">
                {self.predicted_type ? <PredictedTypeBadge type={self.predicted_type} /> : "—"}
              </td>
              <td className="px-2.5 py-1.5 whitespace-nowrap">{self.status}</td>
              <td className="px-2.5 py-1.5 whitespace-nowrap">
                {self.assigned_user_name ?? (self.assigned_user_id ? `#${self.assigned_user_id}` : "—")}
              </td>
              <td className="px-2.5 py-1.5 max-w-[260px]">
                <span className="truncate block" title={self.cached_reply_content ?? ""}>
                  {self.cached_reply_content ?? "—"}
                </span>
              </td>
            </tr>
          )}
```

调用处补 `self`（在 `isCurrentNode` 分支的 `<SubTicketList ... />`）：

```tsx
                    <SubTicketList
                      childIds={d.children_ticket_ids ?? []}
                      drafts={subDrafts}
                      self={{
                        short_code: d.short_code,
                        title: d.title,
                        predicted_type: d.predicted_type,
                        status: d.status,
                        assigned_user_name: d.assigned_user_name,
                        assigned_user_id: d.assigned_user_id,
                        cached_reply_content: d.cached_reply_content,
                      }}
                    />
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npm run test -- TicketDetailPage`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/tickets/TicketDetailPage.tsx frontend/src/pages/tickets/TicketDetailPage.test.tsx
git commit -m "feat(ticket-detail): 子任务列表未拆分时回落显示当前工单本身"
```

---

### Task 6: 全量验证 + 清理

**Files:**
- 无新增；跑全套校验。

- [ ] **Step 1: 类型检查**

Run: `cd frontend && npm run type-check`
Expected: 无 error。若报未用变量（如原写死的 `PredictedTypeBadge` 引用/`suggestion` 提示文案冗余），就地修正。

- [ ] **Step 2: 全量单测**

Run: `cd frontend && npm run test`
Expected: 全绿（含既有 TicketsListPage + 新 TicketDetailPage 全部用例）。

- [ ] **Step 3: 生产构建**

Run: `cd frontend && npm run build`
Expected: tsc + vite build 成功。

- [ ] **Step 4: 提交（若有 lint/type 修正）**

```bash
git add -A frontend/
git commit -m "chore(ticket-detail): 通过 type-check/test/build 校验"
```

---

## Self-Review

**Spec coverage：**
- 未明确分类只显示改判、隐藏三块 → Task 1 ✅
- 改判记录处理人/时间/结果 + 左侧节点更新 → create-hub-issue 写审计 + invalidate ticket-history（Task 1/2）✅
- Bug/需求推 Linear、运营走 AI 答复回填 → Task 2 ✅
- 处理意见下拉三值 + 正常跟进发答复 / 退回占位 / 拆分占位 → Task 3 ✅
- AI 草稿待审核提示 → Task 4 ✅
- 答复纯文本（无附件）→ Task 3 reply body 仅 content ✅；附件区保留占位（文件既有，无需改）
- 子任务未拆分回落当前工单 → Task 5 ✅
- 验证 type-check/test/build → Task 6 ✅

**Placeholder scan：** 无 TBD/TODO；退回转单、拆分转单、出站附件的「待后端」是业务确认的产品占位，非计划占位，均有明确前端行为（toast/禁用）。

**Type consistency：** `graduate`/`reply` mutation、`classified`/`classifyType`/`isDevType`/`isOperation` 派生量、`SubTicketList` 的 `self` 入参签名跨任务一致；`postByPath` 路径模板 `/api/hub-issues/{hub_issue_id}/reply` 与后端一致。
