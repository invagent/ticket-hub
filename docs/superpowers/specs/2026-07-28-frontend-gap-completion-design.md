# 前后端缺口补全设计（2026-07-28）

## 背景

`project_gaps_audit`（2026-07-14 盘点）列出的 P2 前端缺口中，有 4 项前后端未打通。用户要求依次补全。经确认，**「升级新工单」本期不做**（语义未定，留 v2）。本期做剩余 3 项：

1. 单条/批量手动指派 ticket
2. relink 重关联 + hub 搜索
3. hub_issue 详情页补 3 个协同动作

三项相互独立，都是「镜像现有代码模式」的增量，无架构选型。

## 现状勘察结论

- **① 指派**：后端**无**直接指派端点，只有 `POST /api/supervisor/reroute`（AI 路由，批量 1-50）。`Ticket.assigned_user_id`（nullable FK users.id）所有写入均来自 Router 决策。前端 TicketDetailPage「负责人」只读。
- **② relink**：`POST /api/supervisor/relink` 已就绪（body `{ticket_id, new_hub_issue_id, reason}`；幂等；关旧 history 开新 human_confirmed=true）。`GET /api/hub-issues` 无 search 参数。前端**无任何 relink UI**。
- **③ 详情页动作**：4 个动作全在 HubIssuesListPage 自包含，typed `HubIssueSummary`。后端端点全部已存在（`services/hub_issues/devcollab.py`）。`HubIssueDetail` 是 `HubIssueSummary` 的**严格超集**。

---

## ① 单条/批量手动指派

### 需求
主管手动把工单指派给指定处理人，绕过 AI 路由。详情页单条 + 列表页批量都要。指派对象限「处理类角色」（assignee / supervisor / admin）。

### 后端

新增 `POST /api/supervisor/assign`（`require_supervisor`）：

- Body：`{ ticket_ids: list[int] (min 1, max 50), assigned_user_id: int }`
- 校验：目标 user 存在、`is_active=True`、角色 ∈ `{assignee, supervisor, admin}`——否则 422
- 逐条 `update(Ticket).where(id).values(assigned_user_id=...)`，**绕过 Router**
- 每条写 `StatusHistoryRepository.record`：`entity_type="ticket"`、`from_status==to_status`（status 不变）、`changed_by=f"user:{operator_id}"`、reason 注明手动指派、metadata 记 `{operator_user_id, assigned_user_id, prev_assigned_user_id}`
- 端点内 `db.commit()`（服务只 flush），镜像 reroute 的分层
- 新建 `app/services/supervisor/manual_assign.py`（与 `reroute.py` 平级），返回每条成败 `{ticket_id, ok, prev_assigned_user_id}`
- 找不到 ticket → 跳过并在结果标记，不整批失败

响应：`{ assigned: [...], not_found: [ticket_id...] }`（镜像 reroute 响应风格）。

### 前端

- **详情页** `TicketDetailPage`：「负责人」只读字段旁加「指派」按钮（supervisor-only）→ 展开 `<UserSelect>`（复用 `src/components/selectors.tsx`）→ 选人后调 assign 端点（`ticket_ids: [当前]`）→ invalidate `["ticket-detail", id]`
- **列表页** `TicketsListPage`：现有多选 + 底部浮动栏（reroute 同区）加「指派给…」按钮 → 弹出 `<UserSelect>` → 批量调同一端点 → 结果弹窗（成/败计数）
- `<UserSelect>` 需支持按角色过滤候选。`useUserOptions` 现拉全部 active user；在组件层加 `roles?: string[]` 过滤 prop（不改后端 users 端点），只在指派场景传 `["assignee","supervisor","admin"]`

---

## ② relink 搜索重关联

### 需求
主管把某条工单从当前 hub 改挂到另一个 hub。入口在工单详情页弹窗，通过搜索框选目标 hub。

### 后端

`GET /api/hub-issues` 加可选 query `search: str | None`（`app/api/hub_issues.py:129`）：

- 透传到 `HubIssueRepository.list_paginated`（`app/repositories/ticket.py:189`）
- 在 `base` 和 `count_base` 都加：`or_(HubIssue.short_code.ilike(f"%{search}%"), HubIssue.title.ilike(f"%{search}%"))`
- `search` 为空/None 时不加条件，行为完全不变
- 镜像 `CustomerRepository.search` 的 ilike 模式（`sqlalchemy.or_`/`func` 已在 `ticket.py:9` 导入）

relink 端点本身不改（已就绪）。

### 前端

`TicketDetailPage`：当工单**已有** `hub_issue_id` 时，在 hub 链接旁加「重新关联」按钮（supervisor-only）→ 弹窗：

- 搜索框（debounce ~300ms）调 `GET /api/hub-issues?search=xxx&page_size=20`
- 下拉列出命中 hub：short_code + 类型徽标 + title
- **前端过滤掉当前已关联的 hub**（避免选到自己）
- 选中 + 填 reason（必填提示，可空但建议填）→ 调 `POST /api/supervisor/relink` `{ticket_id, new_hub_issue_id, reason}` → invalidate ticket-detail
- 错误映射：404（ticket/hub 不存在）、403、no_op（已关联同一 hub）→ toast/内联提示

---

## ③ 详情页补 3 协同动作

### 需求
hub_issue 详情页补 催办 / 发版通知 / 记录回访 三个动作（登记自修复是全局新建入口，留列表页不搬）。按列表页相同条件显示。

### 后端
无改动。三端点已存在，`require_supervisor`：
- `POST /{id}/urge`（24h throttle）
- `POST /{id}/notify-release`（`{fix_version, note}`）
- `POST /{id}/feedback`（`{status: resolved|stillbad, note}`）

### 前端

抽共享组件，避免详情页重复实现：

- 新建 `src/components/hubActions.tsx`，从 HubIssuesListPage 搬入：
  - `NotifyReleaseModal`、`FeedbackModal` 两个弹窗组件（含各自 mutation）
  - 催办 urge 的 mutation + 按钮
  - 通用 `Modal`/`ModalHeader`/`ModalFooter` 原语
  - 辅助函数 `isDone` / `urgedRecently` / `dwellDays`
- 组件 typed 到 `HubIssueSummary`。因 `HubIssueDetail` 是严格超集，详情页直接传入，**零类型改动**
- 列表页改为 import 共享组件（纯搬迁，行为不变）
- 详情页 `HubIssueDetailPage` 渲染这 3 个动作，**沿用列表页相同条件显示**：
  - 催办：dev 类型（Bug_fix/Demand）+ 未完成 + 有 `linear_identifier`
  - 发版通知：dev 类型 + 已完成 + `!release_notified_at` + `!self_found`
  - 记录回访：`feedback_status === "pending"`（另 `resolved` 时显示「查看闭环」）
- 新组件依赖共享 `@/api/auth`（`isSupervisor`）。**不**顺手重构列表页/详情页原有本地重复的 `isSupervisor`/`errMsg`（控制改动面），只在新组件用共享版

---

## 测试策略

- **① 指派后端**：unit 覆盖——正常指派单条/批量、目标用户不存在(422)、角色不符(422)、非活跃(422)、部分 ticket 不存在（跳过不整批失败）、status_history 审计写入正确、绕过 Router
- **② search 后端**：unit 覆盖——search 命中 short_code、命中 title、空 search 行为不变、大小写不敏感、total 计数正确
- **③**：无后端改动。前端组件搬迁后跑 `npm run test` + `type-check` + `build`，确认列表页行为不回归
- 全量：后端 `make lint` + `make unit`（≥70% 覆盖）；前端 type-check + test + build
- API 契约：改了后端 → `make gen-types` 同步 openapi.json + types.ts，`make check-types` 门槛

## 部署注意

- **无数据库迁移**（① 复用 assigned_user_id，② 只加 query 参数，③ 无后端改动）
- 改了后端端点（①②）→ 必须 `make gen-types` 提交，否则 CI `make check-types` 失败
- SIT 部署：rebuild + force-recreate 后端三容器 + 前端 build+publish

## 范围与非目标

- ❌ 升级新工单（stillbad 下游动作）——本期不做，v2
- ❌ 列表页/情页原有本地 `isSupervisor`/`errMsg` 重复的统一重构——控制改动面，只新组件用共享版
- ❌ owner-split 的 ad-hoc 用户下拉迁移到 `<UserSelect>`——非本任务目标（可顺带记为后续优化）
