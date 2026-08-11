# 工单「处理人」字段 + 可见性权限 设计

日期：2026-08-11
范围：全栈(后端迁移+权限+流动写入,前端列/筛选/权限)。
状态：设计已定，待写 plan。

## 背景与问题

现在工单列表的「处理人」列读的是 `ticket.assigned_user_id`——那其实是**路由分工的责任人**(入库时按产品线/模块 `assignment_scopes` 分的),不是"当前实际在处理这单的人"。例：TKT-005929 列表显示杨慧莉(收票模块责任人),但运营分派规则把它派给了苗一琳(存在 `hub.op_handler_user_id`)。两者语义不同、字段不同、来源不同。

用户要：区分**责任人**(路由分工)与**处理人**(当前实际持有人),列表按处理人展示+筛选+做可见性权限。

## 术语

- **责任人** = `ticket.assigned_user_id`(现有字段,路由分工,语义不变)。列表保留展示,不再作为筛选项。
- **处理人** = 新增 `ticket.handler_user_id`(当前实际在处理的人,动态流动)。

## 用户确认的决策

1. 处理人初始值：入库默认=责任人；毕业时按运营分派规则分给指定运营；规则找不到人→沿用责任人。
2. 可见性：admin + supervisor(主管) 看全部；其余角色(member/assignee/knowledge_op) 只看处理人=自己的。
3. 转交权限：沿用现有,仅 supervisor/admin 可转交。
4. 答复改处理人：答复者(admin/主管/运营)→ 该 hub 下**所有**关联 ticket 的处理人都改成答复者。
5. 转交后原持有人不可见：可见性规则的自然结果(仅对普通角色生效；主管/admin 看全部不受影响)。
6. 列表默认视图：admin/主管默认全部,可按处理人筛选；非 admin/主管默认(强制)只有自己的。
7. 筛选：只保留「处理人」筛选,去掉责任人筛选；责任人列仍展示。

## 数据模型

`Ticket` 新增：
```python
handler_user_id: Mapped[int | None]  # 处理人（当前实际持有人）；FK users.id, nullable, index
```
- 迁移 `0030_ticket_handler`(down_revision=`0029_dispatch_engine`)。
- 回填：存量 `handler_user_id = assigned_user_id`(责任人补成初始处理人)。

## 处理人流动(写入路径)

1. **入库**：创建 Raw ticket 时 `handler_user_id = assigned_user_id`(路由分配后同步)。
2. **毕业(运营分派)**：`ensure_hub_issue_for_ticket` 里 dispatch 命中 → 把 `dr.user_id` 同时写进关联 ticket 的 `handler_user_id`(现在只写 `hub.op_handler_user_id`)；dispatch 无结果 → 处理人保持责任人不变。复用遍历 hub 关联 tickets 的回写。
3. **答复**：`author_reply_endpoint` 成功后,把该 hub 下所有关联 ticket 的 `handler_user_id` 改成答复者 `user.user_id`。复用 `record_ticket_action` 的 `hub_issue_id ==` 遍历(新增一个改 handler 的遍历,或扩展现有)。
4. **转交**：`ManualAssignService.assign` 改写 `handler_user_id`(不再改 assigned_user_id)。

## 可见性权限

- `list_paginated` 新增参数 `visible_to_user_id: int | None`(非空时强制 `Ticket.handler_user_id == visible_to_user_id`)。
- `GET /api/tickets` / `GET /api/tickets/{id}` / `/history`：从 `require_user` 拿登录用户；`role not in (admin, supervisor)` 时：
  - 列表：传 `visible_to_user_id = user.user_id`。
  - 详情/history：加载后校验 `ticket.handler_user_id == user.user_id`,否则 404。
- admin/主管：不加限制(看全部)。

## 转交(后端)

- `POST /api/supervisor/assign`(require_supervisor 不变)：目标字段改为 `handler_user_id`。
- 目标用户角色白名单放开：允许 member(真实处理人多为 member),只要 `is_active`。
- 审计：status_history 记 from_handler→to_handler(changed_by=user)。
- 详情页转派弹窗、列表批量指派复用此接口,语义=改处理人。

## 前端

- `TicketSummary` 新增 `handler_user_id` + `handler_user_name`(后端 batch-load 补名,仿 assigned_user_name)。
- 工单列表：
  - 「处理人」列改读 `handler_user_name`(回落 `#id`)。
  - 责任人列：**去掉**(用户确认责任人不需要展示/筛选；字段后端仍在、语义不变，仅列表不再显示)。
  - 筛选：MultiUserSelect 绑到 `handler_user_ids`（新后端参数），去掉 `assigned_user_ids` 筛选。
- 非 admin/主管：列表默认只有自己的(后端强制,前端无需特殊逻辑)。

## 验证

- 后端：迁移 0030 应用 + 回填；单测覆盖——处理人三条流动路径(入库/毕业分派/答复)、可见性过滤(admin看全/member看自己)、转交改 handler。
- `make gen-types`(TicketSummary 加字段 → openapi/types 同步)。
- 前端：type-check + test + build。

## 占位/后续

- 转派原因落库仍是既有占位(本次不做)。
- 责任人列 + 责任人筛选都去掉(用户确认只留处理人)；责任人字段/语义保留在后端。
