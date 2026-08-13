# 补料回流按源分流设计（KSM / 智齿）

- 日期：2026-08-13
- 状态：设计已确认，待写实现计划
- 关联：[[ksm-supply-refill]]、[[operation-status-machine]]、[[op_status_simplify]]、[[reviewing_draft_display_and_ops]]、[[answer_accuracy_gate]]

## 背景与目标

现状：Operation 工单 AI 判断「需要补充资料」时，无论来源是 KSM 还是智齿，一律直接把
工单打成**补料中**（`op_status=supplementing`）并转兜底主管，需补内容只写进 `agent_decision`
审计，前端处理说明框没有稳定数据源。由此产生两个问题：

1. **KSM 补料回流是死胡同**：客户补料后 KSM 重推同 billId，系统只刷新内容，工单卡在
   补料中不动，不会自动重新触发 AI 答复，必须人工干预。
2. **「处理说明为空，无法答复」bug（TKT-005962）**：前端处理说明框临时填 supply_note，
   但点「提交答复」时 content 取值没回落到该值，后端收到空 content 报错。根因是「显示的
   值」和「提交的值」不是同一个持久字段。

目标：按工单来源系统分流，打通 KSM 补料回流闭环，用一个持久字段统一处理说明的显示/
编辑/提交，从根上消除空值 bug。

### 术语（op_status 中文对照）

| 字段值（代码内，不改） | 中文 | 含义 |
|---|---|---|
| `processing` | 处理中 | AI 自动处理中（handler=agent）或人工介入中（handler≠agent） |
| `answered` | 处理完成 | 已答复客户 |
| `supplementing` | 补料中 | 已向 KSM 客户请求补充资料，等待客户补交 |
| `reviewing` | 待审核 | AI 生成答复草稿，等人工确认后发送 |
| `closed` | 已关闭 | 硬终态，不可重开 |
| `exception` | 处理异常 | 系统故障（replay 超时/崩溃），转人工 |

## 核心决策（已与用户确认）

1. **按源分流靠 `source_code`**：关联工单的 `source_code`（`ksm`/`zhichi`）决定前端是否显示
   「补充资料」按钮。KSM 显示双按钮，智齿只显示「提交答复」。
2. **处理说明与答复正文一段共用**：不区分「答复正文」和「需补料清单」，同一段文字。因此
   复用现有草稿槽 `hub.reply_content`，不新增字段。
3. **智齿补料回流不做系统记录**：智齿全靠人工线下联系客户，客户在智齿侧重新提交不追加
   记录。智齿 ingester 保持 no-op。
4. **补料回流附件追加保留**：每次补料的新附件追加为新附件行，保留历史，可对比客户前后
   交了什么。
5. **补料重答跟随全局闸门**：补料回来后 AI 重答这一次，是否进「待审核」由全局开关
   `OPERATION_ANSWER_ACCURACY_MODE` 决定，与普通首答一致，不做特殊无条件审核。

## 状态机与流程

**核心原则**：KSM 和智齿统一——AI 判断「需要补充资料」时，工单留在**处理中**，把 AI
生成的需补内容写进处理说明草稿。唯一区别在前端是否显示「补充资料」按钮（按 source_code）。

**行为变化**：AI 判需补料**不再**直接置补料中；只有人工点「补充资料」按钮才进补料中。

### KSM 完整闭环

```
工单毕业 → AI 尝试答复（replay）
  ├─ 能直接答（branch D） → 走现有全局审核闸门（待审核 / 直发）  ← 不变
  └─ 判断需补料（branch C） → 工单=处理中，AI 把需补内容写进处理说明草稿
         │                       （reply_content + reply_is_draft=true）
         │
         ├─【人工判断可直接答】→ 改处理说明 → 点「提交答复」
         │      → POST /reply → author_reply 发客户 → 处理中 → 处理完成
         │
         └─【人工判断确实要补料】→ 改处理说明（可选）→ 点「补充资料」
                → POST /request-supply → supply outbox → KSM writeback（supplyKsmOrder）
                → 处理中 → 补料中
                → 客户补交资料，KSM 重推同一 billId
                → ksm_ingester 命中补料中分支【改】：
                     记录新描述 + 建新附件行 + 工单转回处理中（handler=agent）
                → drain 重新扫到 → AI 重新答复 → 走全局审核闸门（待审核 / 直发）
                → 人工确认（若待审核态）→ 处理完成
```

### 智齿流程

```
工单毕业 → AI 判需补料 → 工单=处理中 + 处理说明填需补内容
  → 前端只显示「提交答复」
  → 处理人线下联系客户，拿到信息后手工改处理说明 → 点「提交答复」→ 处理完成
```

智齿 ingester 重推保持 no-op（不做补料回流记录）。

## 数据落地：处理说明字段

复用现有草稿槽，**不新增字段、不加迁移**：

| 字段 | 作用 |
|---|---|
| `hub.reply_content` | 处理说明/答复正文正本（显示、编辑、提交都指向它） |
| `hub.reply_is_draft` | 是否 AI 草稿（未发）。AI 写入=true，人工点提交答复后清 false |
| `hub.reply_authored_by` | 谁写的（`agent:ai_cs:draft` / `user:张三`） |

数据流：AI 判需补料 → 写 `reply_content` + 标 `reply_is_draft=true` → 前端处理说明框读
`reply_content` → 人工编辑改的就是它 → 点「提交答复」后端读 `reply_content` 当前值发出、
清 `reply_is_draft`。显示/编辑/提交三者指向同一持久字段，空值 bug 从根上消失。

与「待审核」闸门共用同一草稿槽：待审核态本就用 `reply_content`+`reply_is_draft`。需补料草稿
和待审核草稿是同一套机制（都是「AI 产出、待人工发」），不产生两套草稿并存。

## 补料回流记录

### ① 新描述 —— 已有，不改
`content_refresh` 已把 KSM 重推的新 content 追加进 `ticket.body` 和 `hub.canonical_body`
（带北京时间戳 `[内容更新 时间]`），并写 status_history。多次补料累加，历史保留。

### ② 新附件 —— 缺口，要补
现 `content_refresh` 只覆盖 `source_payload`，不建附件行。改动：补料重推时把新 payload 的
附件 URL 建成新 `Attachment` 行（挂同一 ticket，`vision_status='queued'`），走现有附件流水线
（下载→MinIO→可选 OCR）。**追加保留**，不覆盖历史批次。

### ③ 处理节点（时间轴）—— 基础已有，前端渲染全
后端 `record_ticket_action` 已在答复时给关联工单写时间轴节点；补料回流、状态流转、答复都
落在 `status_history`（entity_type='ticket'）。前端详情页时间轴渲染完整来回：

```
[时间] 工单毕业
[时间] AI 判需补料 → 处理中
[时间] 人工点补充资料 → 补料中
[时间] 客户补料（新描述 + 2 张附件）→ 处理中
[时间] AI 重新答复 → 待审核
[时间] 处理人确认答复 → 处理完成
```

## 前端交互（详情页）

**处理说明框**：Operation 详情页读 `hub.reply_content`，可编辑。当 `reply_is_draft=true` 时，
框上方提示「以下为 AI 生成的处理建议，请审核后提交」，区分草稿与已发答复。

**按钮（按 source_code）**：
- KSM 来源：「提交答复」（POST /reply，处理说明当前内容发客户 → 处理完成）
  +「补充资料」（POST /request-supply，提交 KSM 补料接口 → 补料中）
- 智齿来源：只「提交答复」
- 多源边界（理论上 hub 挂多源）：只要有 KSM 源就显示「补充资料」。

**取值正解**：点「补充资料」时，把处理说明框当前内容作为补料说明发给 KSM（不再从别处读
可能没回落的值）。

**入口合并**：去掉现详情页独立的「请客户补料」区块（`HubIssueDetailPage.tsx:651` 附近），
统一到处理说明框下方双按钮，避免两个补料入口并存。

## 错误处理与边界

- **已关闭/已完成工单**：答复/补料前若工单已**处理完成**或**已关闭**，给明确提示，不覆盖
  （现有 409 守卫延用，补料同样加守卫）。
- **补料回流转处理中幂等**：客户短时重推多次同一补料单，转「处理中+agent」要幂等——已是
  处理中就只刷内容不重复转，避免重复触发 AI 重答。
- **AI 重答系统故障**：补料回来重答若 replay 超时/崩溃，沿用现有机制落**处理异常**转人工，
  不无限重扫。
- **附件回流失败**：单个附件下载/OCR 失败标 failed，不阻塞主流程（现有流水线容错）。
- **智齿误调 request-supply**：前端不显示按钮即可；后端若被智齿单调用（异常路径），按无源/
  无补料通道优雅处理。

## 测试

**单测**：
- branch C 改后落处理中 + 草稿（不再补料中）。
- KSM 补料回流分支：转处理中 + 建新附件行 + 追加描述。
- 幂等：补料单重推两次只处理一次，不重复触发重答。
- 已完成/已关闭工单拒绝补料/答复。

**端到端 seam**：完整闭环——判需补料→处理中→点补充资料→补料中→客户重推→处理中→
AI 重答→待审核→确认→处理完成。

**前端**：按 source_code 显示对应按钮；处理说明框读写 `reply_content`；提交答复非空。

**回归**：确认不破坏现有「待审核」闸门 drain 口径（drain 扫 处理中+agent+created，补料
回流转回的正符合口径能被扫到）。

## 改动清单（供写实现计划参考）

后端：
1. `services/agents/operation_answer.py` branch C：不再 `apply_op_status(supplementing)`，改为
   留 `processing` + `_save_draft_reply(需补内容)`。
2. `services/ingest/ksm_ingester.py` 补料中（supplementing）分支：从「只刷内容」改成「刷内容
   + 建新附件行 + 转 processing/agent」（幂等）。
3. `services/ingest/content_refresh.py`：补建 Attachment 行（追加保留）。
4. `api/hub_issues.py` 详情响应：暴露 `reply_is_draft` 字段。
5. `api/hub_issues.py` reply/request-supply 端点：补「已完成/已关闭」守卫（补料侧）。

前端：
6. `pages/hub-issues/HubIssueDetailPage.tsx`：处理说明框（草稿提示）+ 按 source_code 的双/单
   按钮 + 补料入口合并 + 时间轴渲染补料回流节点 + 附件追加展示。
7. 类型同步：`make gen-types`（若后端 schema 改动）。
