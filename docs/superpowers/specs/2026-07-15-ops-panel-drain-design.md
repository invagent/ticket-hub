# 主管运维面板（出站回写手动 drain）· 设计文档

- 日期：2026-07-15
- 状态：设计已批准，待写实施计划
- 范围：工作台加「出站回写运维」卡片，主管一键手动触发 KSM/智齿 drain 并看结果（sent/skipped/failed）。缺口盘点 A 组 A1。revert-split（A2）因缺「已拆分工单列表」入口后置。
- 相关：`frontend/src/pages/workbench/WorkbenchPage.tsx`、`backend` 端点 `POST /api/supervisor/drain-ksm-writeback` + `drain-zhichi-writeback`（已就绪）

## 1. 背景与问题

后端有 `drain-ksm-writeback` / `drain-zhichi-writeback` 端点（主管手动 flush 出站回写队列，尊重 enabled/dry_run 灰度），但前端零调用——主管想立即回写、看成败，只能等 Celery beat 每 2min 跑，且看不到结果。

## 2. 决策要点（已锁定）

| 项 | 决策 |
|---|---|
| 范围 | 只做 drain 面板（A1）；revert-split（A2）后置（缺 decision_id 来源入口）|
| 落点 | 工作台 `WorkbenchPage`，`ConfigWarningBar` 之后、看板 section 之前，supervisor-only |
| 组件 | 同文件内新增 `OpsPanel`（仿 ConfigWarningBar/HumanQueue 风格），白卡 + SectionHeader 视觉统一 |
| 交互 | KSM/智齿各一行，显示 enabled/dry_run 状态徽标 + 「立即 drain」按钮 → 内联展示结果 |
| 反馈 | 局部 flash/error state + 内联条（无全局 toast，同现有模式）|
| 权限 | 复用 `isSupervisor`（role in supervisor/admin），非主管不渲染 |
| mutation | 仿 executeSplit：`useMutation` + `api.post(path)`（无 body）+ invalidate |

## 3. UI 设计

```
┌─ 出站回写运维（SectionHeader，仅 supervisor/admin）──────────┐
│ KSM    [已启用/未启用] [dry_run]   [立即 drain]              │
│   └─ 结果：扫描 N · 发送 N · 跳过 N · 失败 N（failed>0 标红）│
│ 智齿   [已启用/未启用] [dry_run]   [立即 drain]              │
│   └─ 结果：...                                               │
└──────────────────────────────────────────────────────────┘
```

- **状态徽标**：drain 响应带 `enabled`/`dry_run`。首次不知道状态（端点是 POST 才返回）——两种处理：
  - 方案 A：点 drain 才知道 enabled/dry_run（响应里带），未点前不显示徽标
  - 方案 B：徽标只在 drain 后显示。**采用 B**——简单，点了就看到状态 + 结果。
- **结果区**：drain 后内联显示 `scanned/sent/skipped/failed/deferred`；`failed>0` 红色；`errors[]` 非空可展开看详情（≤前 5 条）。
- **dry_run 提示**：若响应 `dry_run=true`，结果区加一句「（dry_run：仅组装未真发）」，避免主管误以为已真发。
- **enabled=false 提示**：响应 `enabled=false` 时结果全 0，加一句「出站回写未启用（.env ENABLED=false）」。

## 4. 组件结构（`OpsPanel`，WorkbenchPage.tsx 内）

```tsx
function OpsPanel() {
  const qc = useQueryClient();
  const [ksmResult, setKsmResult] = useState<DrainResp | null>(null);
  const [zhichiResult, setZhichiResult] = useState<DrainResp | null>(null);
  const [error, setError] = useState<string | null>(null);

  const ksmDrain = useMutation({
    mutationFn: () => api.post("/api/supervisor/drain-ksm-writeback"),
    onSuccess: (d) => { setKsmResult(d); setError(null); qc.invalidateQueries({ queryKey: ["workbench"] }); },
    onError: (e) => setError(errMsg(e)),
  });
  const zhichiDrain = useMutation({ /* 同上，drain-zhichi-writeback */ });

  return (
    <section className="bg-white border border-hub-border rounded-[10px] p-4 mb-6">
      <SectionHeader n={...} title="出站回写运维" note="手动 flush 出站队列，尊重灰度开关" />
      <DrainRow label="KSM" mut={ksmDrain} result={ksmResult} />
      <DrainRow label="智齿" mut={zhichiDrain} result={zhichiResult} />
      {error && <ErrorBar msg={error} onClose={() => setError(null)} />}
    </section>
  );
}
```

`DrainRow`：label + 「立即 drain」按钮（teal 实心，disabled=isPending）+ 结果区（result 非空时渲染 enabled/dry_run 徽标 + scanned/sent/skipped/failed）。

类型：
```ts
type DrainResp = paths["/api/supervisor/drain-ksm-writeback"]["post"]["responses"]["200"]["content"]["application/json"];
```
（KSM 和智齿响应结构相同，共用类型）

## 5. 落点接入

`WorkbenchPage.tsx` 在 `{isSupervisor && <ConfigWarningBar />}` 之后加：
```tsx
{isSupervisor && <OpsPanel />}
```

## 6. 测试（前端 vitest）

- OpsPanel 仅 supervisor/admin 渲染（mock currentRole）
- 点「立即 drain」调 `api.post` 正确端点
- drain 成功 → 结果区显示 scanned/sent/failed
- failed>0 → 红色
- dry_run=true → 显示 dry_run 提示
- enabled=false → 显示未启用提示
- error → 错误条

（若前端 mutation 测试基建不足，至少加组件渲染 + 权限 gate 测试；drain 调用可 mock api）

## 7. 非目标

- revert-split（A2，缺 decision_id 入口，后置）
- drain 定时状态轮询（只手动触发看结果，不做实时监控）
- KSM/智齿之外的源（zammad/ai_cs 出站未实现）

## 8. 影响面

- 改动：`WorkbenchPage.tsx`（加 OpsPanel + 接入）；可能抽 `OpsPanel` 到单独文件（若 WorkbenchPage 已过大）
- 无后端改动（端点就绪）、无迁移
- openapi 类型已含 Drain*Response，无需重新生成
