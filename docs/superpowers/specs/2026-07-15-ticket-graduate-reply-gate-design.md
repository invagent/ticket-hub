# 工单手动毕业 + Operation 回复权限 gate · 设计文档

- 日期：2026-07-15
- 状态：设计已批准，待写实施计划
- 范围：缺口盘点 B 组前两项——#5 Operation 回复编辑器加前端权限 gate；#3 非投诉工单手动毕业按钮。均纯前端，后端端点就绪。#6b relink / #4 详情页协同动作因隐藏工作量单独排。
- 相关：`frontend/src/pages/hub-issues/HubIssueDetailPage.tsx`（#5）、`frontend/src/pages/tickets/TicketDetailPage.tsx`（#3）、端点 `POST /api/supervisor/create-hub-issue`（就绪）

## 1. 背景与问题

- **#5**：`OperationReplySection`（HubIssueDetailPage.tsx:192）的「撰写/修改回复」按钮对任何登录用户都渲染，靠后端 403 拦——UX 差（普通用户看到按钮点了才被拒）。同文件 owner-split 已用 `isSupervisor()` gate（:368），回复区未用。
- **#3**：非投诉普通工单（低置信度/AI 没自动毕业的）无「手动毕业为 hub_issue」入口。当前只有投诉队列（WorkbenchPage `ComplaintActions`）能 create-hub-issue，工单详情页纯只读。

## 2. 决策要点（已锁定）

| 项 | 决策 |
|---|---|
| 范围 | 本批只做 #5 + #3（纯前端、后端就绪、无隐藏工作量）|
| #5 | OperationReplySection 加 `isSupervisor()` gate，非主管隐藏编辑入口（只读回复正文）|
| #3 | 工单详情页加「毕业为 hub_issue」按钮（supervisor-only，hub_issue_id 为空时显示），可选 type 下拉（默认 predicted_type）|
| 共享 helper | `isSupervisor()`/`currentRole()` 现散落多处，本次抽到 `src/api/auth.ts` 共享（TicketDetailPage 要用第三份）|
| HUB_TYPES 常量 | WorkbenchPage 局部的 HUB_TYPES/labels，本次也抽到共享或 TicketDetail 内联 |
| relink(#6b)/#4 | 后置（relink 缺 hub 搜索端点；#4 要重构抽组件）|

## 3. #5 Operation 回复权限 gate

`HubIssueDetailPage.tsx` `OperationReplySection`（:192）：
- 体内加 `const supervisor = isSupervisor();`（照 owner-split :368）
- 「撰写/修改回复」按钮块（:222-233）：`{!editing && (...)}` → `{supervisor && !editing && (...)}`
- 编辑态 textarea 块（:235）：非主管进不了 editing（初始 false，无入口），严谨起见外层也可 `supervisor &&`
- 效果：非主管只读回复正文 + 版本，看不到编辑/保存入口

## 4. #3 工单手动毕业

`TicketDetailPage.tsx`：
- 新增 supervisor-only 操作 section（基本信息 section 后）
- **显示条件**：`isSupervisor()` 且 `detail.hub_issue_id == null`（未毕业）。已毕业则现有 :66-77 已显示 HUB 链接，不显示毕业按钮
- **UI**：type 下拉（Operation/Bug_fix/Demand/Internal_task，默认 `detail.predicted_type`）+ 「毕业为 hub_issue」按钮
- **mutation**（照搬 WorkbenchPage convertComplaint :491）：
  ```tsx
  useMutation({
    mutationFn: () => api.post("/api/supervisor/create-hub-issue", { ticket_id: id, type }),
    onSuccess: (d) => { qc.invalidateQueries({ queryKey: ["ticket-detail", id] }); /* 显示 d.hub_issue_short_code */ },
    onError: setError,
  })
  ```
- 成功后刷新 → hub_issue_id 有值 → 毕业按钮消失、HUB 链接出现
- 类型：`CreateHubIssueBody { ticket_id, type? }` / `CreateHubIssueResponse { created, hub_issue_id, hub_issue_short_code, ... }`

## 5. 共享 helper 抽取

`src/api/auth.ts` 加（若无）：
```tsx
export function currentRole(): string {
  try { return JSON.parse(localStorage.getItem("auth_user") ?? "null")?.role ?? ""; }
  catch { return ""; }
}
export function isSupervisor(): boolean {
  const r = currentRole();
  return r === "supervisor" || r === "admin";
}
```
HubIssueDetailPage / TicketDetailPage 改 import 共享版（HubIssuesListPage/WorkbenchPage 的本地版可留，避免大改；本次只让新代码用共享版 + HubIssueDetailPage 换用）。

HUB_TYPES：`src/api/hubTypes.ts`（或 auth 同级）导出 `HUB_TYPES = ["Operation","Bug_fix","Demand","Internal_task"]` + `HUB_TYPE_LABELS`。

## 6. 测试（vitest）

- #5：HubIssueDetailPage Operation hub，supervisor 看到「撰写回复」，member 看不到（mock role）
- #3：TicketDetailPage 未毕业工单 + supervisor 看到「毕业为 hub_issue」；已毕业（hub_issue_id 非空）不显示；member 不显示
- #3：点毕业调 create-hub-issue 正确 body
- 共享 isSupervisor/currentRole 单测

## 7. 非目标

- #6b relink（缺 hub 搜索端点，后置）
- #4 详情页 4 协同动作（要重构抽组件，后置）
- 后端改动（端点就绪）
- 逐单人工分类修正（毕业只是升级为 hub_issue，不改 predicted_type）

## 8. 影响面

- 改动：`HubIssueDetailPage.tsx`（#5 gate）、`TicketDetailPage.tsx`（#3 毕业 section）、新增/改 `src/api/auth.ts`（共享 helper）、可能新增 `hubTypes.ts`
- 无后端、无迁移
- 前端测试需过（含既有 20 passed 不回归）
