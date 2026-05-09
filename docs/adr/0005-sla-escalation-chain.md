# ADR-0005: SLA 升级链 — assignee → deputy → supervisor → 兜底池

- 状态：Accepted (D2-D)
- 日期：2026-05-09
- 决策依据：upgrade_plan.md §11.1 / §4.12 + D6 提前到 D1 落地的 EscalationWorker

## 背景

工单 SLA 超时后必须有明确的升级路径。原方案在 D6 落地，但 D1 开发时
为了让 SLA 健康度（≥ 90%）能在 D1 验收阶段就有意义，把
`EscalationWorker` 提前到了 D1 实现，对应代码在
`backend/app/services/sla/escalation.py`。

D2-D 把这套已经在跑的逻辑固化成 ADR，避免后续做 LLM/agent 重构时把
约束抹掉。

## 决策

### 升级序列（每条 notification 独立）

```
        [SLAWatcher 写入 notification]
                      │
                      ▼
            assignee（或 fallback 池）
                      │ 2h 内没 ack
                      ▼
            deputy_supervisor_id
                      │ 2h 内还没 ack
                      ▼
            supervisor_id
                      │ 仍然没 ack
                      ▼
            (无人可升 → 留在 pending；
             人工干预或下次 SLA 复盘)
```

### 关键参数

| 参数 | 值 | 来源 |
|---|---|---|
| 第一次 SLA 阈值 | 4h（ticket）/ 4–24h（hub_issue 按 type） | `services/sla/watcher.py` 默认；D2-C 加产品线级覆盖 |
| 升级间隔 | 2h | `escalation.py` `DEFAULT_ESCALATION_AFTER` |
| 扫描周期 | 10 min | `EscalationWorker.run()` cron |
| 兜底池 | `K0099` 或可配置的 `fallback_recipient_id` | `SLAWatcher.fallback_recipient_id` |

### 写入语义

- **不创建新 notification 给 deputy**：在原 notification 上写
  `escalated_at` + `escalated_to_user_id`，**额外**写一条新
  `notify_type='escalation'` 通知给 deputy。
  - 原行 `escalated_at` 让 `/api/supervisor/inbox` 不再展示给最初
    assignee（已经升级走了）
  - 新行让 deputy 看到 inbox 多了一条要 ack 的事
  - 两步链自然成立：deputy 也 2h 没 ack 的话，新通知再被下一轮
    `EscalationWorker` 选中升给 supervisor

### 升级目标查找规则（在 `user_supervisors` 表里查）

```python
target = deputy_supervisor_id of recipient
    ?? supervisor_id of recipient
    ?? None  # 不升级，留 pending
```

- deputy 优先（如果配置了）
- 没 deputy 走 supervisor
- 都没（user_supervisors 行根本不存在）→ 留 pending，靠人工

### Self-loop 防御

- `user_supervisors` 表 CHECK：`user_id <> supervisor_id`、`user_id <>
  deputy_supervisor_id`
- D2-E `/api/admin/users/{id}/supervisor` PATCH 端点二次校验
- `EscalationWorker` 不重复升级已有 `escalated_at` 的行（避免循环）

### 不做的事

- ✘ 不向上无限升级：到 supervisor 之上没有 "supervisor 的 supervisor"
  这一层，因为：
  - 公司层级再深也大概率到不了 7 级
  - 真到那种规模需要重新做 D6 的 escalation policy 引擎
- ✘ 不发通知到 supervisor 的 supervisor：D2 范围只两级（assignee →
  deputy → supervisor）
- ✘ 不做"假期/请假"识别：deputy 在请假期间的工单仍然走 deputy；
  D6 之前补"虚线休假"再说

### 数据流

```
notification_log（D1 落地）
├─ recipient_user_id      最初的接收人（assignee 或 fallback）
├─ acknowledged_at        被 ack 时点
├─ escalated_at           被升级走的时点（NULL = 还在最初 recipient 手里）
├─ escalated_to_user_id   升给了谁
└─ payload                JSON：原始事件信息

```

`escalated_at IS NULL AND acknowledged_at IS NULL` = 还在 inbox 显示
`escalated_at IS NOT NULL` = 已经流走，不在 assignee inbox（但 deputy 那
里有新行）

## 替代方案

### 方案 B：每个 notification 自带"升级时间表"

在 `notification_log` 加 `escalation_schedule_json` 列，预填好
"+2h → deputy，+4h → supervisor，+6h → 兜底池"。

**为什么不用**：每条都重复一份 schedule 浪费空间；schedule 改了
存量数据要回填；EscalationWorker 反而变复杂。

### 方案 C：通过 PagerDuty / Opsgenie 之类外部系统

接外部 oncall 工具能做更花哨的策略（轮换、夜间静音等）。

**为什么不用**：D1–D6 范围内还没接入外部告警平台；当前 deputy ↔
supervisor 静态映射在飞书 IM 推送已经够用。D6 上线压测后再评估。

## 后果

- D1 上线后 SLA 升级链 working as designed；D2 加产品线阈值（D2-C）
  让 4h 默认值不至于一刀切
- 后续 D2-E 加 admin 配 supervisor / deputy 的 UI 是这条链的
  "configuration plane"；ADR-0012 描述写权限边界
- D3 LLM agent 可能想"自动 ack/解决"工单，但**不能绕开** escalation
  逻辑：agent 决策走 `agent_decisions` 表，由 supervisor 或 SLA 判
  定才决定是否标 ack
- D6 灰度上线时，新旧两套 SLA 升级要双跑对账（双跑脚本待写）

## 验证

`tests/unit/services/test_sla_escalation.py` 覆盖：
- 2h 阈值生效
- deputy 优先于 supervisor
- 没 deputy 时走 supervisor
- 都没的不升级（留 pending）
- 已升级的不重复升级
