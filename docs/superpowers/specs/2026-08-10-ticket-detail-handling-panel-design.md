# 工单详情「工单处理」栏重构设计

日期：2026-08-10
范围：**纯前端**，只改 `frontend/src/pages/tickets/TicketDetailPage.tsx`，复用现有后端接口。
状态：设计已定，待写 plan。

## 背景

工单详情页「工单处理」容器（右侧处理详情区）当前是 2026-08 工单调整 V1.0 搭的 UI 骨架，处理建议下拉、处理说明、处理附件都是静态占位。本次按业务把它接到既有后端能力上，做成**分类闸门驱动**的处理流。

三个业务确认（用户 2026-08-10 拍板）：
1. **答复纯文本，去掉附件**——reply 接口 + KSM/智齿出站回写全程无附件字段，不改后端；处理附件区仅保留展示占位。
2. **退回转单 = 打回工单：前端 UI + 占位待后端**——目前无 return-to-source 端点，不真正调用。
3. **「未明确分类」用现有信号判定，纯前端**——不给后端加 `predicted_confidence` 字段。

## 后端现状（已核实，file:line 见探查）

- `POST /api/supervisor/create-hub-issue { ticket_id, type }`：把工单「毕业」成 hub_issue，`type` 可覆盖 predicted_type（Operation/Bug_fix/Demand/Internal_task，**不含 Complaint**），无置信门槛。Bug_fix/Demand 手动路径**直推 Linear**；写 ticket_hub_issue_history + status_history 审计。
- `POST /api/hub-issues/{hub_issue_id}/reply { content }`：require_supervisor，Operation-only，正式发出并置 `op_status=answered`，写 reply_history + status_history。**只接受文本，无附件字段**；出站（KSM/智齿）也只带文本。
- 运营 AI 自动答复经 `cached_reply_content`/`cached_reply_version` 回填到工单详情。`op_status=="reviewing"` 表示 AI 草稿待审核。
- `GET /api/tickets/{ticket_id}` 返回 `hub_issue_id`、`predicted_type`、`op_status`、`cached_reply_content`、`cached_reply_version`、`children_ticket_ids` 等；**不返回** `predicted_confidence`。
- **无** return-to-source / 打回端点。

## 分类判定信号（纯前端）

- **未明确分类** = `hub_issue_id == null`（工单尚未毕业成 hub_issue）。
- **已明确分类** = `hub_issue_id != null`，按 `predicted_type` 分流。

## 设计

### 右侧处理详情区：分类闸门

**A. 未明确分类（`hub_issue_id == null`）→ 只显示分类改判区**
- 类型选择器：4 个 HUB_TYPES（运营/Bug 修复/需求/内部任务），默认选中 `predicted_type`。
- 「确认分类」按钮 → `POST /api/supervisor/create-hub-issue { ticket_id, type }`。
- **隐藏**：处理建议下拉、处理说明、处理凭证/补充凭证。
- 成功后 invalidate `ticket-detail` + `ticket-history` + `tickets` + `hub-issues`；左侧时间轴自动刷新出「关联建立 HUB-xxx」节点（处理人/时间/分类结果由后端审计驱动，前端不手拼）。

**B. 已明确分类（`hub_issue_id != null`）→ 按 predicted_type 分流**
- **Bug 修复 / 需求**：后端 create-hub-issue 手动路径已直推 Linear。此区显示「已推送 Linear」状态提示，不出现对客答复编辑区。
- **运营**：显示完整处理区（处理建议下拉 + 处理说明 + 处理附件占位），见下。

### 运营类：处理意见下拉 + 确认动作

处理意见下拉（3 值新口径）：
- **正常跟进**（默认）→ 确认时将「处理说明」文本框内容作为答复发出：`POST /api/hub-issues/{hub_issue_id}/reply { content }`。文本经 AI 答复（`cached_reply_content`）回填后主管可编辑。答复只带文本；处理附件区仅展示上传控件并标「出站附件待后端」，不参与发送。
- **退回转单** → 前端确认框，标「打回逻辑待后端接口」，**不调用后端**（占位）。
- **拆分转单** → 本期不处理，选中时提示「拆分逻辑后续版本支持」，确认按钮禁用。

AI 答复回填 + 草稿提示：
- 运营类 `cached_reply_content` 已回填「处理说明」文本框（沿用现状 `noteDrafts[0] ?? cached_reply_content`）。
- 若 `op_status == "reviewing"`，文本框上方加黄色提示「AI 草稿待审核，确认后正式发出」。

操作节点：reply 接口写 status_history（后端既有），确认后 invalidate `ticket-history` → 左侧时间轴自动出新节点。退回/拆分无后端，仅前端 toast，不写节点。

### 子任务列表

- **已拆分**（`children_ticket_ids` 非空）→ 列出各子任务（现状 `SubTicketList` 已实现）。
- **未拆分**（`children_ticket_ids` 空）→ 列表回落显示**当前工单自身一行**（编号=当前 short_code、说明=title、类型=predicted_type、状态、处理人、解决方案=cached_reply_content）。相对现状改动：空时不再显示「无子任务」，改为显示当前工单本身。

### 左侧处理节点状态更新

分类确认 / 运营答复后 invalidate `ticket-history`，左侧时间轴重拉，新节点（关联建立 / 状态流转）由后端审计驱动，处理人/时间/结果全来自后端 history，前端不手工拼节点。

## 范围与验证

- 纯前端，仅改 `TicketDetailPage.tsx`，复用 `create-hub-issue` + `reply` 两接口。
- 退回转单、拆分转单、出站附件 → 前端占位「待后端」。
- 验证：`npm run type-check` + `npm run build` + `npm run test`（现有 vitest）。

## 占位清单（待后端后续接）

- 退回转单打回源系统端点。
- 拆分转单（owner-split 或 ticket-split 触发）。
- 答复出站携带附件（reply 接口 → outbox → KSM/智齿写回适配器全链）。
