# 工单参数编辑迁移到 ticket 详情页 设计

- 日期：2026-08-14
- 状态：设计已确认，待写实现计划
- 关联：[[hub_attributes_edit]]（上一轮做在 hub 页，本轮迁移）、[[human_gates_over_ai_pipeline]]、[[ticket_detail_handling_panel]]、[[hub_handler_can_operate_own]]

## 背景与目标

上一轮「工单参数编辑（类型/产品线/模块）」做在了 **hub-issue 详情页**（`/hub-issues/{id}`），
但用户日常操作的是 **ticket（工单）详情页**（`/tickets/{id}`，TicketDetailPage）。需求本意
是在 ticket 详情页改工单参数。本轮把功能迁到 ticket 页，并**撤掉 hub 页的参数编辑 UI**
（后端端点保留复用）。

ticket 页的特殊性：一个 ticket 是否「毕业」成 hub_issue 决定参数存哪层——
- **未毕业**（hub_issue_id == null）：参数在 ticket 层（predicted_type/product_line_code/module）
- **已毕业**（hub_issue_id != null）：参数在 hub 层（type/product_line_code/module）

## 核心决策（已与用户确认）

1. **只在 ticket 页做**，撤掉 hub 页上轮加的 `HubAttributesEditor` UI（hub 后端 attributes 端点
   + confirm-classification 放宽保留，ticket 页已毕业单复用）。
2. **一个组件按毕业状态分流**（`TicketAttributesEditor`），UI 统一（类型/产品线/模块三下拉），
   差别只在保存打哪个端点。
3. **已毕业单**：脏检测「保存」→ 复用现成 hub `PATCH /api/hub-issues/{hub_id}/attributes`
   （只改数据不联动，改 type 同步回 ticket.predicted_type）。pending_review 时额外「确认推送」
   （按当前选中类型显隐：选运营隐藏；未保存改动先自动保存再 confirm-classification）。
4. **未毕业单**：**无单独「保存」**，改完参数点「确认分类」**一步毕业**（走 create-hub-issue，
   带上下拉里改后的 type/product_line_code/module）。
5. **create-hub-issue 扩展**：body 加可选 `product_line_code`/`module`，传了就覆盖（否则继承
   ticket 原值）；权限从 require_supervisor **放宽到处理人本人**。
6. **权限**：主管/管理员 + 该工单处理人本人（ticket 页判 `ticket.handler_user_id == 当前用户`）。
7. **已关闭工单**：只读（无下拉无按钮）。
8. **模块下拉联动**：随产品线过滤（复用 hub 页那轮加的 `GET /api/hub-issues/catalog/modules`）；
   改产品线清空已选模块。
9. **移除**现有 ticket 页 pending_review 的 `ClassificationReviewInline` 里的「改判」下拉+按钮和
   「误报关闭」按钮（改判被参数编辑取代；误报关闭暂不用）。「确认推送」保留。

## 后端

### create-hub-issue 扩展（app/api/supervisor.py + creator.py）

现状：`CreateHubIssueBody{ticket_id, type?}`，`require_supervisor`，
`ensure_hub_issue_for_ticket(ticket_id, created_by, type_override, db)` 继承 ticket 的产品线/模块。

改动：
- `CreateHubIssueBody` 加 `product_line_code: str | None = None`、`module: str | None = None`。
- 端点权限 `require_supervisor` → `require_user` + 函数体内校验「主管/管理员或 ticket.handler_user_id
  == 当前用户」（新 helper 或内联，ticket 无 hub 不能用 _authorize_hub_handler）。
- `ensure_hub_issue_for_ticket` 加可选 `product_line_code`/`module` 参：非 None 时毕业的 hub 用
  传入值（并 upsert_catalog 建目录），否则继承 ticket 原值（现状）。

### 复用（不改）

- `PATCH /api/hub-issues/{hub_id}/attributes`（上轮，已毕业单保存参数）。
- `POST /api/supervisor/confirm-classification`（上轮已放宽处理人，pending_review 确认推送）。
- `GET /api/hub-issues/catalog/modules?product_line_code=`（上轮，模块下拉）。
- `GET /api/admin/product-lines`（产品线下拉，任意登录用户可读）。

### 不新增 ticket attributes 端点

因未毕业单「一步到位」（改完直接确认分类毕业），不需要单独改 ticket 层参数的端点。

## 前端

### 撤销 hub 页 UI（HubIssueDetailPage.tsx）

hub 页回到「上一轮工单参数编辑之前」的状态（即 [[classify_tuning_and_review_gate]] 那版）：
- 删除 `HubAttributesEditor` 组件定义 + 其渲染点。
- 恢复 `ClassificationReviewPanel` 组件（pending_review 研发类的确认推送/改判/误报关闭）及其
  渲染条件 `{(type===Bug_fix||Demand) && status===pending_review && <ClassificationReviewPanel>}`。
- 恢复 `currentUserId`/`patchByPath` 若仅 HubAttributesEditor 用则可留（无害），auth.ts 的
  currentUserId 和 client.ts 的 patchByPath 保留（ticket 页会用到）。
- hub attributes 后端端点、confirm-classification 放宽、catalog/modules 端点、
  op_handler_user_id 字段全部保留（ticket 页复用）。
实现参考 git：hub 页可从上一轮 merge 前的 `HubIssueDetailPage.tsx` 取回 ClassificationReviewPanel。

### ticket 详情页新增 TicketAttributesEditor（TicketDetailPage.tsx）

替换现有「分类改判 select + 确认分类」块（未毕业）和 `ClassificationReviewInline`（pending_review）
的参数部分。组件按 `d.hub_issue_id`/`hub.status` 分流：

**权限**：`canEdit = 未关闭 && (isSupervisor() || currentUserId() === d.handler_user_id)`。
无权限或已关闭 → 只读展示类型/产品线/模块。

**下拉**（三态共用）：
- 类型：5 类型（Operation/Bug_fix/Demand/Internal_task/Complaint）中文标签。
- 产品线：`GET /api/admin/product-lines`。
- 模块：`GET /api/hub-issues/catalog/modules?product_line_code=<选中产品线>`，改产品线清空模块。

**未毕业（hub_issue_id == null）**：
- 三下拉初始化自 ticket（predicted_type/product_line_code/module）。
- 单个按钮「确认分类」→ `create-hub-issue{ticket_id, type, product_line_code, module}` 一步毕业。
- 无「保存」按钮（改动不单独落库，随确认分类毕业）。

**已毕业（hub_issue_id != null）**：
- 三下拉初始化自 hub（需 hub 详情，已有 `hub` query）。
- 「保存」按钮：脏检测（值≠原 hub 值才亮），→ `PATCH /api/hub-issues/{hub_id}/attributes`。
- pending_review 且当前选中类型 ≠ Operation → 额外「确认推送」：若脏则先 await 保存，再
  `confirm-classification`。
- 非 pending_review（已确认）：只有「保存」，无确认推送。

**移除**：`ClassificationReviewInline` 的「改判」下拉+按钮、「误报关闭」按钮（`reclassify`/`dismiss`
mutation 删除）；未毕业块的旧「分类改判 select」并入新组件。

## 错误处理与边界

- 已关闭 ticket（status 终态）：前端只读；create-hub-issue / hub attributes 后端各自守卫。
- 无权限：create-hub-issue 403（新权限校验）；hub attributes 已有 403。
- 未知产品线/模块：upsert_catalog 自动建。
- 改产品线后原模块不属新线：前端清空模块，未选就毕业/保存 → module 置空（允许）。
- 未毕业单确认分类时有未保存下拉改动：本就带当前下拉值走 create-hub-issue，无冲突。

## 测试

**后端**：
- create-hub-issue 带 product_line_code/module → 毕业的 hub 用传入值 + upsert_catalog 建目录。
- create-hub-issue 不带产品线/模块 → 继承 ticket 原值（回归现状）。
- create-hub-issue 权限：处理人本人放行、路人 403、主管/管理员放行。

**前端**：
- 未毕业单：显示三下拉 + 「确认分类」，无「保存」；确认分类带三个值。
- 已毕业单：三下拉 + 「保存」（脏检测）；pending_review 选研发显示确认推送、选运营隐藏。
- 已关闭/无权限：只读。
- 模块随产品线联动清空。
- hub 页：HubAttributesEditor 已移除，恢复原 ClassificationReviewPanel。

**回归**：ticket 页现有处理节点时间轴/处理说明/附件不受影响；hub 页回退后原 pending_review
流程正常。

## 改动清单

后端：
1. `app/api/supervisor.py`：CreateHubIssueBody 加 product_line_code/module；端点权限放宽处理人。
2. `app/services/hub_issues/creator.py`：ensure_hub_issue_for_ticket 加 product_line_code/module 参。
3. gen-types。

前端：
4. `src/pages/hub-issues/HubIssueDetailPage.tsx`：移除 HubAttributesEditor，恢复
   ClassificationReviewPanel 渲染。
5. `src/pages/tickets/TicketDetailPage.tsx`：新增 TicketAttributesEditor（按毕业状态分流），
   替换旧分类改判块 + 精简 ClassificationReviewInline。

不改：hub attributes 端点、confirm-classification、catalog/modules（复用）。
