# 主管运维面板（出站回写 drain）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 工作台加 `OpsPanel`（supervisor-only），主管一键 drain KSM/智齿出站回写，内联看 scanned/sent/skipped/failed + enabled/dry_run 状态。

**Architecture:** WorkbenchPage.tsx 内新增 `OpsPanel` 组件（仿 ConfigWarningBar/executeSplit），`ConfigWarningBar` 后接入。两个 useMutation 调 `api.post` drain 端点（无 body），结果存局部 state 内联渲染。

**Tech Stack:** React 18 + TS + TanStack Query + Tailwind + vitest/msw。复用 `api.post`、`errMsg`、`currentRole`、`SectionHeader`。

## Global Constraints

- 权限：复用 `isSupervisor`（role in supervisor/admin），非主管不渲染 OpsPanel。
- mutation 仿 `executeSplit`（WorkbenchPage.tsx:436）：`api.post(path)` 无 body + onSuccess 存结果 + onError setError。
- 结果类型：`paths["/api/supervisor/drain-ksm-writeback"]["post"]["responses"]["200"]["content"]["application/json"]`（KSM/智齿同构，共用）。
- 反馈：局部 state 内联条（无全局 toast），复用 errMsg。
- 徽标方案 B：enabled/dry_run 点 drain 后才显示（响应带）；dry_run=true 标「仅组装未真发」；enabled=false 标「未启用」。
- 分支 `feat/ops-panel-drain`（已建，spec 已提交）。每 Task commit。

---

### Task 1: OpsPanel 组件 + 接入 WorkbenchPage

**Files:**
- Modify: `frontend/src/pages/workbench/WorkbenchPage.tsx`
- Test: `frontend/tests/WorkbenchOpsPanel.test.tsx`（新建）

**Interfaces:**
- Consumes: `api.post`（client）、`errMsg`/`currentRole`/`SectionHeader`（WorkbenchPage 内已有）
- Produces: `OpsPanel` 组件（supervisor-only，drain KSM/智齿）

- [ ] **Step 1: 写失败测试**

```tsx
// frontend/tests/WorkbenchOpsPanel.test.tsx
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import WorkbenchPage from "@/pages/workbench/WorkbenchPage";
import * as client from "@/api/client";

function renderAs(role: string) {
  localStorage.setItem("auth_user", JSON.stringify({ name: "u", role }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><WorkbenchPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("OpsPanel", () => {
  afterEach(() => { localStorage.clear(); vi.restoreAllMocks(); });

  it("member 看不到出站回写运维", () => {
    renderAs("member");
    expect(screen.queryByText("出站回写运维")).not.toBeInTheDocument();
  });

  it("supervisor 看到运维面板 + KSM/智齿 drain 按钮", () => {
    renderAs("supervisor");
    expect(screen.getByText("出站回写运维")).toBeInTheDocument();
    expect(screen.getAllByText("立即 drain").length).toBeGreaterThanOrEqual(2);
  });

  it("点 KSM drain 调端点并显示结果", async () => {
    const spy = vi.spyOn(client.api, "post").mockResolvedValue({
      enabled: true, dry_run: false, scanned: 3, sent: 2, skipped: 0, deferred: 0, failed: 1, errors: ["x"],
    } as never);
    renderAs("supervisor");
    fireEvent.click(screen.getAllByText("立即 drain")[0]);
    await waitFor(() => expect(spy).toHaveBeenCalledWith("/api/supervisor/drain-ksm-writeback"));
    await waitFor(() => expect(screen.getByText(/失败\s*1/)).toBeInTheDocument());
  });

  it("dry_run 结果显示未真发提示", async () => {
    vi.spyOn(client.api, "post").mockResolvedValue({
      enabled: true, dry_run: true, scanned: 1, sent: 0, skipped: 1, deferred: 0, failed: 0, errors: [],
    } as never);
    renderAs("supervisor");
    fireEvent.click(screen.getAllByText("立即 drain")[0]);
    await waitFor(() => expect(screen.getByText(/仅组装未真发/)).toBeInTheDocument());
  });
});
```

> 注：WorkbenchPage 默认还会请求 workbench/queue 数据——测试里这些 useQuery 会 fetch。用 msw（tests/msw-server.ts 已有）给 `/api/metrics/workbench` 等返回空，或 mock。若测试因未 mock 的 query 报错，在 test 顶部补 msw handler 返回 200 空对象。参照现有 tests/TicketsListPage.test.tsx 的 msw 用法。

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run tests/WorkbenchOpsPanel.test.tsx 2>&1 | tail -15`
Expected: FAIL（OpsPanel 不存在，"出站回写运维" 找不到）

- [ ] **Step 3: 实现 OpsPanel**

在 WorkbenchPage.tsx 加组件（放 ConfigWarningBar 定义附近）：

```tsx
type DrainResp =
  paths["/api/supervisor/drain-ksm-writeback"]["post"]["responses"]["200"]["content"]["application/json"];

function DrainRow({
  label, path, qc,
}: { label: string; path: "/api/supervisor/drain-ksm-writeback" | "/api/supervisor/drain-zhichi-writeback"; qc: QueryClient }) {
  const [result, setResult] = useState<DrainResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const mut = useMutation({
    mutationFn: () => api.post(path),
    onSuccess: (d) => { setResult(d as DrainResp); setErr(null); void qc.invalidateQueries({ queryKey: ["workbench"] }); },
    onError: (e) => setErr(errMsg(e)),
  });
  return (
    <div className="flex flex-col gap-1 py-2 border-b border-hub-border last:border-0">
      <div className="flex items-center gap-3">
        <span className="font-semibold text-[13px] w-12">{label}</span>
        {result && (
          <>
            <span className={`text-[11px] px-1.5 py-0.5 rounded ${result.enabled ? "bg-hub-teal/10 text-hub-teal" : "bg-gray-100 text-gray-500"}`}>
              {result.enabled ? "已启用" : "未启用"}
            </span>
            {result.dry_run && <span className="text-[11px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-700">dry_run</span>}
          </>
        )}
        <button
          onClick={() => mut.mutate()}
          disabled={mut.isPending}
          className="ml-auto text-[11.5px] font-semibold px-[11px] py-[4.5px] rounded-md bg-hub-teal text-white border border-hub-teal disabled:opacity-50 hover:brightness-95"
        >
          {mut.isPending ? "drain 中…" : "立即 drain"}
        </button>
      </div>
      {result && (
        <div className="text-[11.5px] text-hub-muted">
          扫描 {result.scanned} · 发送 {result.sent} · 跳过 {result.skipped} · <span className={result.failed > 0 ? "text-hub-rose font-semibold" : ""}>失败 {result.failed}</span>
          {result.dry_run && <span className="text-amber-700">（仅组装未真发）</span>}
          {!result.enabled && <span className="text-gray-500">（出站回写未启用）</span>}
          {result.errors.length > 0 && (
            <details className="mt-0.5"><summary className="cursor-pointer text-hub-rose">错误 {result.errors.length}</summary>
              <ul className="list-disc pl-4">{result.errors.slice(0, 5).map((e, i) => <li key={i}>{e}</li>)}</ul>
            </details>
          )}
        </div>
      )}
      {err && <p className="text-[11.5px] text-hub-rose">{err}</p>}
    </div>
  );
}

function OpsPanel() {
  const qc = useQueryClient();
  return (
    <section className="bg-white border border-hub-border rounded-[10px] p-4 mb-6">
      <SectionHeader n={0} title="出站回写运维" note="手动 flush 出站队列，尊重灰度开关" />
      <DrainRow label="KSM" path="/api/supervisor/drain-ksm-writeback" qc={qc} />
      <DrainRow label="智齿" path="/api/supervisor/drain-zhichi-writeback" qc={qc} />
    </section>
  );
}
```

> `SectionHeader` 若 `n` 是必填序号且不接受 0/装饰用，改为不传 n 或加可选。核对 SectionHeader 签名（WorkbenchPage.tsx:296），必要时用普通 `<h2>` 标题替代避免打乱看板的 ①② 编号。
> 确认 `paths` 类型已从 `@/api/types` import（文件顶部）；`useMutation`/`useQueryClient` 从 `@tanstack/react-query`；`useState` 从 react。

接入（ConfigWarningBar 之后）：
```tsx
{isSupervisor && <ConfigWarningBar />}
{isSupervisor && <OpsPanel />}
```

- [ ] **Step 4: 运行确认通过**

Run: `cd frontend && npx vitest run tests/WorkbenchOpsPanel.test.tsx 2>&1 | tail -15`
Expected: PASS（4 tests）

- [ ] **Step 5: type-check**

Run: `cd frontend && npm run type-check 2>&1 | tail -5`
Expected: 无错误

- [ ] **Step 6: 提交**

```bash
git add frontend/src/pages/workbench/WorkbenchPage.tsx frontend/tests/WorkbenchOpsPanel.test.tsx
git commit -m "feat(ops-panel): 工作台出站回写运维面板 — KSM/智齿一键 drain"
```

---

### Task 2: 全量前端验证 + 合并部署

- [ ] **Step 1: 全量前端测试 + type-check + lint**

Run: `cd frontend && npm run type-check && npx vitest run 2>&1 | tail -15`
Expected: 全绿（含既有 Layout/TicketsList 等测试不回归）

- [ ] **Step 2: 前端 build 验证**

Run: `cd frontend && npm run build 2>&1 | tail -8`
Expected: build 成功（tsc + vite build）

- [ ] **Step 3: 合并 main + push**

```bash
git checkout main && git merge --no-ff feat/ops-panel-drain
git push origin main
```

- [ ] **Step 4: SIT 部署前端**

前端走 docker 构建到 nginx 静态目录（deploy/build-frontend.sh）：
```bash
ssh root@sit "cd /data/hub-issue && git pull && deploy/build-frontend.sh /data/hub-issue/frontend-dist"
```
（若 build-frontend.sh 路径/用法不同，核对 DEPLOY-SIT.md 的前端部署命令）

- [ ] **Step 5: 验证**

浏览器访问 SIT 工作台（supervisor 登录），确认「出站回写运维」卡片出现、点 drain 有响应。或 curl 确认前端资源更新。

---

## Self-Review

**Spec 覆盖**：§3 UI（DrainRow 徽标+结果+dry_run提示）→Task1 Step3；§4 组件→Task1；§5 接入→Task1 Step3；§6 测试→Task1 Step1+Task2。全覆盖。

**占位扫描**：无 TODO，组件代码完整。

**类型一致性**：DrainResp 用 paths 类型（KSM/智齿同构）；path 参数字面量联合类型与端点一致；api.post 无 body 与后端 requestBody:never 一致。

**实现时校验点**：
1. `SectionHeader` 的 `n` 参数是否必填/接受装饰值（Task1 Step3 注，必要时用 h2 替代）
2. WorkbenchPage 是 default export 还是 named（测试 import 需对，现有 main.tsx 路由 import 方式为准）
3. `errMsg`/`currentRole`/`isSupervisor`/`SectionHeader` 是否 WorkbenchPage 内部函数（不可跨文件 import）——OpsPanel 放同文件内可直接用
4. vitest 里 WorkbenchPage 的其他 useQuery（workbench/queue）需 msw mock，避免测试因网络挂（Task1 Step1 注）
5. `api` 是否 `client.api` 命名导出（测试 spy 用 `client.api.post`）——核对 client.ts 导出形式
