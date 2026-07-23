# Operation 工单状态机重构 · 设计文档

- 日期：2026-07-22
- 范围：**仅 Operation 类 hub_issue**，不动研发类（Bug_fix/Demand 仍走现有 status + Linear 回同步）
- 关联：[[ksm-supply-refill]]（复用补料回填机制）、[[operation-auto-reply-async]]（复用异步 drain）

## 一、目标

给 Operation 工单一套面向业务的中文状态机，覆盖：自动答复、补料回流、客户驳回、T+7 自动关闭、人工介入（改 KB/skill 后重答）、系统故障处理异常。作为 **Operation 专属状态层**叠加在现有底层机制之上，底层 `hub.status`/`ticket.status`/KSM·智齿回写全部照旧，由新状态映射驱动。

## 二、状态模型

### 新增字段（HubIssue，仅 Operation 用；非 Operation 为 NULL）

| 字段 | 类型 | 说明 |
|------|------|------|
| `op_status` | str \| NULL | Operation 专属状态，6 取值，带 CHECK 约束（仅 Operation 非空）|
| `op_handler` | str \| NULL | 处理人：`agent` 或 主管名。≠agent 即人工介入阶段 |
| `reject_count` | int | 客户驳回次数，default 0 |
| `op_status_changed_at` | datetime \| NULL | 进入当前 op_status 的时间（T+7 计时基准）|

底层 `hub.status`、`reply_content_version`、`reply_authored_by`、`ticket.status` 语义不变。

### 6 个状态

| 值 | 中文 | 含义 |
|----|------|------|
| `processing` | 处理中 | 待答复。三种入口：①入库毕业(handler=agent) ②补充重提后 agent 仍答不了(handler=主管) ③客户驳回(handler=主管) |
| `answered` | 处理完成 | agent 或主管重答成功，等 T+7 |
| `closed` | 已关闭 | T+7 未驳回自动关（**硬终态**）|
| `supplementing` | 补充资料 | agent 判需补料，补料请求已发 |
| `resupplied` | 补充重提 | 客户补料后重推同工单，待 agent 重答 |
| `exception` | 处理异常 | **仅** agent 处理时系统故障/超时/意外（handler=主管）|

### 三条核心语义

1. **处理中是复用状态**，靠 `op_handler` 区分 agent 自动 vs 人工介入。「人工介入」不是独立状态，是 `op_handler != 'agent'`。
2. **处理异常只给系统故障**（replay 超时/崩溃/鉴权等意外）。业务上答不了（answer-router 判 transfer / 没找到标准答案）→ 处理中 + handler=主管，**不是异常**。
3. **已关闭是硬终态**，驳回只认未关闭工单。已关闭工单重推 = 幂等 no-op（客户须提新工单）。

## 三、完整流转

```
①入库毕业  处理中/agent
   │ drain 异步 replay + answer-router
   ├─(D 能答)──────────→ 处理完成/agent   [reply_v+1 + 答复回写]
   ├─(C 需补料)────────→ 补充资料/agent   [发 supply outbox]
   ├─(transfer 业务答不了)→ 处理中/主管    [记审计=转人工]
   └─(系统故障/超时)────→ 处理异常/主管    [记审计=错误详情]

②补充资料 ──客户补料重推同工单(webhook,更新内容)──→ 补充重提/agent
   │ drain 重答
   ├─(能答)────→ 处理完成/agent
   └─(仍答不了)─→ 处理中/主管

③人工介入(处理中且 handler≠agent)：主管改 KB/skill → 手动点【重答】
   ├─(能答)────→ 处理完成/agent
   ├─(仍答不了)─→ 处理中/主管（留人工，可反复点）
   └─(系统故障)─→ 处理异常/主管

④处理完成 ──T+7 未驳回(每日 beat 扫 op_status_changed_at)──→ 已关闭  [关单回写]

⑤处理完成(未关闭) ──客户驳回:重推同工单(webhook,更新内容)──→ 处理中/主管, reject_count+1
   │(人工介入,同③)
```

### 触发点落点表

| # | 触发 | 前置 op_status | → op_status | handler | 底层动作 |
|---|------|---------------|------------|---------|---------|
| ① | 入库毕业 | (无) | 处理中 | agent | 初始化 |
| D | agent 答成功 | 处理中/补充重提 | 处理完成 | agent | author_reply（reply_v+1 + 级联 + reply outbox）|
| C | agent 判需补料 | 处理中 | 补充资料 | agent | request_supply（supply outbox）|
| transfer | agent 业务答不了 | 处理中/补充重提 | 处理中 | 主管 | 记审计 |
| 异常 | replay 系统故障/超时 | 任意 agent 处理 | 处理异常 | 主管 | 记审计 |
| ② | 客户补料重推 | 补充资料 | 补充重提 | agent | apply_content_refresh（更新内容）|
| ③ | 主管点【重答】成功 | 处理中(人工) | 处理完成 | agent | author_reply |
| ③ | 主管点【重答】仍答不了 | 处理中(人工) | 处理中 | 主管 | 留人工，可反复点 |
| ③ | 主管点【重答】系统故障 | 处理中(人工) | 处理异常 | 主管 | 记审计 |
| ④ | T+7 未驳回 | 处理完成 | 已关闭 | (不变) | 关单回写（_close_local）|
| ⑤ | 客户驳回:重推同工单 | 处理完成(未关闭) | 处理中 | 主管 | apply_content_refresh + reject_count+1 |

**时间驱动唯一流转**：处理完成→已关闭，从进入处理完成时 `op_status_changed_at` 起算 **T+7 自然日**（非工作日算法）。中途被驳回→回处理中→计时作废；再次处理完成重新计时。

## 四、入口与机制

### A. 重推识别（驳回 + 补料重提统一）

驳回和补料重提都是「重推同工单（同 source_ticket_id）+ 更新内容」，同构。扩展现有 `ksm_ingester` 幂等分支（补料回填分支旁），按 **本地 op_status 门控**分流：

```
existing ticket 命中（find_by_source→ 查其 hub.op_status：
  - 补充资料(supplementing)  → 补料重提：apply_content_refresh + op_status→补充重提, handler=agent
  - 处理完成(answered，未关闭)→ 驳回：  apply_content_refresh + op_status→处理中, handler=主管, reject_count+1, 记审计
  - 已关闭(closed)          → 硬终态 no-op（客户须提新工单）
  - 其他                    → 原 no-op（重复心跳）
```

**公共服务** `app/services/ingest/content_refresh.py`（重命名/泛化现有 supply_refill 思路）：
- `apply_content_refresh(db, ticket, new_payload)`：更新 source_payload + 追加 body[标记 北京时间] + 同步进 hub.canonical_body（供重答可见，同补料回填的修复）。**不改 op_status/handler**（由 ingester 分流后设置）。不 commit。
- 补料回填的 `apply_supply_refill` 现有逻辑（清 auto_reply 审计等）改为 op_status 驱动后简化——见 §六迁移。

主管名取值：驳回/转人工时 `op_handler` 置**当前配置的兜底处理人用户名**（`default_pool_user_id` 对应的 user.name；未配置则置字面量 `'主管'` 占位）。本期不专设独立的 Operation 人工处理人配置。

### B. T+7 自动关闭（新 Celery beat）

- 新任务 `close_answered_operations`，beat 每日 1-2 次（非 2min）。
- 扫 `op_status='answered' 且 op_status_changed_at ≤ now - 7天 且 deleted_at IS NULL` → 逐个经 `apply_op_status` 转 `closed` + 触发关单回写（现有 _close_local / cascade）。
- swallow-all + own session（同现有 drain task 模式）。受总开关控制。

### C. 人工重答按钮（新 API）

- `POST /api/hub-issues/{id}/re-answer`（require_supervisor 或 knowledge_op）
- 前置：op_status=处理中 且 handler≠agent（人工介入中），否则 409。
- **同步**调 replay + answer-router（主管点了要立即看结果，不走异步 drain）：
  - 答成功 → 处理完成, handler=agent
  - 业务答不了 → 留处理中, handler=主管（可反复点）
  - 系统故障（AiCsNetworkError 耗尽 / AiCsError）→ 处理异常, handler=主管
- 主管改 KB/skill 复用现有 admin_skills API + reflect 工作台（已存在，无需新建）。

### D. drain 改造（op_status 驱动）

现有 `drain_operation_auto_reply` 扫描口径从「reply_v=0 且无 auto_reply 审计」改为按 op_status：
- 扫 `op_status IN ('processing' 且 handler='agent', 'resupplied')` 的 Operation hub（limit batch）。
- **排除人工介入中**（handler≠agent）——等主管手动【重答】，不自动扫。
- D/C/transfer/异常 分别落对应 op_status（不再靠"写审计防重扫"，改靠 op_status 本身是否"待 agent 处理"）。
- ai_cs 来源仍排除（走 reflect）。

### E. 状态变更统一入口

新增 `apply_op_status(db, hub, *, to_status, handler, reason)`（仿 `apply_hub_status`）：
- 改 op_status + op_handler + op_status_changed_at，写 status_history（entity_type='hub_issue'，changed_by）。
- 映射驱动底层动作：进入 answered→author_reply；进入 closed→关单回写；进入 supplementing→request_supply 已在别处触发（此处只记状态）。
- 幂等：to_status == 当前则 no-op。

## 五、op_status ↔ 底层映射

| op_status | 底层动作 | 底层 status 结果 |
|-----------|---------|-----------------|
| 处理完成 | author_reply（reply_v+1 + reply outbox）| hub.status 不变（答复非关单）|
| 已关闭 | 关单回写（_close_local）| ticket→closed, hub.status→resolved |
| 补充资料 | request_supply（supply outbox）| ticket→awaiting_supply（真发后）|
| 补充重提 | apply_content_refresh | ticket→received |

研发类工单完全不受影响（op_status 恒 NULL）。

## 六、数据迁移

- 迁移加 4 字段（op_status/op_handler/reject_count/op_status_changed_at）+ CHECK 约束（op_status IN 6 值 OR NULL；type='Operation' OR op_status IS NULL）。
- 回填现存 Operation hub：`reply_content_version >= 1 → op_status='answered'`，`=0 → 'processing'`；`op_handler='agent'`；`reject_count=0`；`op_status_changed_at = 现有 status_changed_at 或 created_at`。
- 现有 `apply_supply_refill`（[[ksm-supply-refill]]）逻辑并入 `apply_content_refresh` + op_status 驱；「清 auto_reply 审计驱动 drain」改为「置 op_status=补充重提 让 drain 扫到」——旧的审计门控退役。

## 七、前端

- 新建 `op_status → 中文标签` 映射组件（现仅英文 STATUS_BADGE 色板）。
- Operation 列表/详情展示：op_status 中文标签 + 处理人 + 驳回次数（>0 时红标）。
- 人工介入中（op_status=处理中 且 handler≠agent）显示【重答】按钮（require_supervisor/knowledge_op）。
- 状态筛选下拉加 6 个 op_status。

## 八、边界情况

1. **毕业初始化**：Operation hub 毕业即 `op_status='processing', op_handler='agent', op_status_changed_at=now`；非 Operation 不设（NULL）。
2. **ai_cs 来源**：仍排除自动答复（走 reflect）。
3. **驳回处理中的工单**：未答完（op_status≠answered）客户无从驳回；幂等上「非 answered → 不触发驳回」。
4. **已关闭重推**：硬终态 no-op。
5. **补充重提后系统故障**（重提触发的 drain 重答遇 replay 故障）：同异常规则 → 处理异常/主管。
6. **主管【重答】反复失败**：留处理中/主管，可无限次点，人工阶段不因失败离开。

## 九、非目标（YAGNI）

- 智齿驳回/补料重提入口（本期 KSM 优先；content_refresh 公共服务已泛化预留，智齿 ingester 后续接入）。
- 研发类（Bug_fix/Demand）状态机改造（仍用现有 status + Linear）。
- op_status 与 SLA watcher 的整合（SLA 仍按现有阈值检测，不与 op_status 联动）。
- 驳回原因结构化采集（本期只记次数 + 审计文本）。
