# 工单参数编辑（类型/产品线/模块）设计

- 日期：2026-08-14
- 状态：设计已确认，待写实现计划
- 关联：[[classify_tuning_and_review_gate]]、[[human_gates_over_ai_pipeline]]、[[hub_handler_can_operate_own]]、[[operation_dispatch_engine]]

## 背景与目标

现状：详情页的「待确认分类」面板（`ClassificationReviewPanel`，仅研发类 +
`pending_review` 时出现、`require_supervisor`）把「改类型（reclassify）」+「确认推送
（confirm-classification）」+「误报关闭（dismiss-classification）」揉在一起。其中
reclassify 改类型时会**自动分流**（改运营→回答复链、改研发→进推 Linear 闸门）。

目标：把「改工单参数」做成一个**通用的、任意状态可用、只改数据不联动**的编辑表单，
方便处理人日常修正工单信息；「待确认分类」的闸门确认逻辑保留但精简。

## 核心决策（已与用户确认）

1. **权限放宽**：主管/管理员 + 该工单处理人（op_handler/handler 本人）可改。复用
   `_authorize_hub_handler`（[[hub_handler_can_operate_own]] 的 helper）。
2. **可改字段**：工单类型 `type`、产品线 `product_line_code`、模块 `module`。
   product（产品）不动。
3. **出现范围**：任意状态的详情页都能改（不限 pending_review），**已关闭工单除外**。
4. **只改数据不联动**：改参数只落库（+同步 ticket.predicted_type + 自动建目录 +
   写审计），**不推 Linear、不重分派、不重答、不改 hub.status/op_status**。
5. **默认可编辑 + 脏检测保存**：有权限且非关闭时，下拉直接可改（无需先点「编辑」）；
   仅当值与原值不同才亮「保存」按钮；未保存离开不落库。无权限/已关闭 → 纯只读。
6. **pending_review 闸门确认**：保留「确认推送」，按表单**当前选中类型**显/隐——选
   Operation 隐藏，选 Bug_fix/Demand/Internal_task 显示。「改判」下拉+按钮和「误报关闭」
   按钮**移除/隐藏**（改判被通用编辑取代；误报关闭暂不用）。
7. **确认推送时自动保存**：点「确认推送」时若表单有未保存改动，先自动保存当前参数再推
   （一步到位，confirm-classification 读到的是最新落库值）。
8. **模块下拉联动**：模块下拉随产品线过滤（只列该产品线下模块）；改产品线时清空已选模块。

## 架构：拆分「参数编辑 A」与「闸门确认 B」

**A. 通用工单参数编辑**（新组件，任意非关闭状态显示）
改 type/product_line_code/module，「保存」调新端点只落库。权限：主管/管理员/处理人本人。

**B. 闸门确认**（pending_review 专属，研发类）
只在 `status='pending_review'` 且研发类时额外出现「确认推送」，按 A 表单当前选中类型
显/隐。点确认推送 → 自动保存 A 的未保存改动 → 调 `confirm-classification`（原逻辑）。

A、B 放同一父组件，共享「当前选中类型」state，B 才能实时响应 A 的下拉。

## 后端

### 新端点 `PATCH /api/hub-issues/{hub_issue_id}/attributes`

只改数据，不联动。与会自动分流的 `reclassify` 分开（reclassify 保留给需要联动的路径，
本设计前端不再用它）。

**入参**（全部可选，只传要改的）：
```json
{ "type": "Operation|Bug_fix|Demand|Internal_task|Complaint",
  "product_line_code": "string",
  "module": "string" }
```

**逻辑**：
1. 权限：`_authorize_hub_handler(db, hub_issue_id, user)`（主管/管理员/op_handler 本人）。
2. **已关闭守卫**：hub.status=='closed' 或（Operation 且 op_status=='closed'）→ 409。
3. 改 `hub.type` / `hub.product_line_code` / `hub.module`（只改传入的字段）。
4. 若改了 `type` → 同步所有关联未删 ticket 的 `predicted_type`，每条写一条
   `classify_type` AgentDecision（`skill="manual", human_confirmed=True, changed_by=user`），
   与 reclassify 一致（防工单列表「AI 分类」列串）。
5. 若改了产品线/模块 → `upsert_catalog(db, product_line_code=..., module=...)` 自动建目录。
6. 写 status_history（entity_type='hub_issue'，from==to==hub.status，reason 记改了什么）
   + `record_ticket_action`（投影到关联 ticket 时间轴）。
7. **不碰** hub.status / op_status / Linear / 分派 / 答复链。
8. commit。返回 `{hub_issue_id, type, product_line_code, module, updated_ticket_count}`。

### 处理人可读的下拉数据源

现状权限缺口：
- `GET /api/admin/product-lines`：端点无 admin 守卫 → 任意登录用户可读（够用）。
- `GET /api/admin/modules?product_line_code=`：`require_admin` → 处理人（assignee/member）
  **读不了**。

补：新增处理人可读的模块列表端点（`require_user`），例如
`GET /api/hub-issues/catalog/modules?product_line_code=`，返回 `[{code/name}]`（复用 Module
模型，active_only）。产品线沿用现有 `/api/admin/product-lines`（已可读）。
（备选：把 admin_catalog 的 list_modules 权限从 require_admin 降到 require_user——但改动
既有 admin 端点语义，倾向新增专用只读端点更干净。）

## 前端 UI

### A. 工单参数编辑区（详情页「任务信息」附近）

有权限且非关闭：下拉直接可改（默认可编辑，无「编辑」切换）。

```
┌─ 工单参数 ─────────────────────────────┐
│ 类型   [Bug_fix        ▾]               │
│ 产品线 [发票云          ▾]               │
│ 模块   [开票管理        ▾] (随产品线联动) │
│                          [保存]          │  ← 仅有未保存改动时亮起可点
└────────────────────────────────────────┘
```
- 类型下拉：Operation/Bug_fix/Demand/Internal_task/Complaint（中文标签，值用英文）。
- 产品线下拉：`GET /api/admin/product-lines`。
- 模块下拉：随产品线联动（新只读端点，带 product_line_code 过滤）；改产品线清空已选模块。
- 脏检测：`dirty = 任一字段 ≠ 原值`；`保存`按钮 `disabled={!dirty || saving}`。
- 「保存」→ `PATCH .../attributes`，成功后 invalidate 详情 query。
- 无权限或已关闭 → 纯只读文本展示（类型/产品线/模块），无下拉无按钮。

### B. pending_review 闸门确认区（研发类 + pending_review，在 A 下方）

```
┌─ 待确认分类 ───────────────────────────┐
│ AI 判为 Bug_fix，确认后推送研发          │
│                        [确认推送]        │  ← A 表单当前选中运营时隐藏
└────────────────────────────────────────┘
```
- 「确认推送」显/隐：读 A 的当前选中类型——Operation 隐藏，其余显示。
- 点「确认推送」：若 A 有未保存改动 → 先 `await` 保存（PATCH attributes）→ 再调
  `confirm-classification`。confirm 逻辑不变（研发走推 Linear 闸门/运营回答复链）。
- 移除原「改判」下拉+按钮、「误报关闭」按钮。
- 无权限（非主管——注：confirm-classification 仍 require_supervisor）→ 保留原「待主管确认」
  只读提示。

**权限注记**：参数编辑 A 放宽到处理人；但闸门确认 B（confirm-classification）保持
`require_supervisor` 不变（推 Linear 是主管职能）。处理人能改 pending_review 单的参数，
但确认推送仍需主管。

## 错误处理与边界

- 已关闭工单：后端 409 + 前端只读双保险。
- 无权限调 attributes：403（_authorize_hub_handler）。
- 未知产品线/模块：upsert_catalog 自动建，不报错。
- 改产品线后原模块不属于新线：前端清空模块；未选就保存 → module 置空（允许）。
- Complaint：只改数据不触发毕业，安全（Complaint「绝不自动毕业」约束不受影响）。

## 测试

**后端**：
- attributes 改 type → 落库 + 同步 predicted_type + 写 classify_type 审计。
- attributes 改产品线/模块 → 落库 + upsert_catalog 建目录。
- 已关闭工单 → 409。
- 无权限（非处理人非主管）→ 403；处理人本人 → 放行。
- **不联动**断言：改 type 后 hub.status/op_status 不变、无 Linear push、无 outbox。
- 新模块只读端点：require_user 可读、带 product_line_code 过滤。

**前端**：
- 有权限显示可编辑下拉；无权限/已关闭只读。
- 脏检测：无改动保存置灰，改动后亮起。
- 模块随产品线联动清空。
- pending_review：选运营隐藏「确认推送」、选研发显示；无「改判」「误报关闭」按钮。
- 确认推送时有未保存改动 → 先保存再确认。

**回归**：移除改判/误报关闭后，pending_review 确认推送主流程不破。

## 改动清单

后端：
1. `api/hub_issues.py`：新增 `PATCH /{id}/attributes`（只改数据端点）。
2. `api/hub_issues.py`：新增 `GET /catalog/modules?product_line_code=`（require_user 只读）。
3. gen-types。

前端：
4. `pages/hub-issues/HubIssueDetailPage.tsx`：新增「工单参数」编辑区（A）；改造
   `ClassificationReviewPanel`（B）——移除改判/误报关闭，确认推送按选中类型显隐 + 自动
   先保存；A/B 共享选中类型 state。

不改：`reclassify` / `confirm-classification` / `dismiss-classification` 后端端点（B 仍调
confirm-classification；reclassify/dismiss 端点保留但前端不再用）。
