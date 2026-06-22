# ADR-0014: agent_decisions 极简审计表（取代 spec 的 agent_runs + agent_decision_targets）

- 状态：Accepted (D3)
- 日期：2026-05-10
- 决策依据：D3-A `migrations/0007_d3_a_agent_decisions.py`；`app/models.py` AgentDecision

## 背景

spec 为 Agent 审计设计了三张表：

- `agent_runs`：每次 LLM 调用的流水（provider/model/tokens/cost/latency）
- `agent_decisions`：每个 Agent 决策
- `agent_decision_targets`：多目标决策（如 dedup 关联多条）的关联行

D3 实际需要的是：**主管能看到每个 Agent 决策、能 revert**。三表方案对当前规模偏重。

## 决策

**只建一张 `agent_decisions`。LLM 调用流水交给结构化日志（ADR-0013），多目标关联塞进
`proposal` JSON。**

- 一行一决策：`decision_type`(classify_type/split_ticket/no_split/dedup_link/dedup_new/...) +
  `subject_type`(ticket/hub_issue) + `subject_id` + `proposal`(JSON) + `status`(executed/reverted)
- revert：翻 `status='reverted'` + `reverted_at/by/reason`，CHECK 约束保证一致性
- 多目标（dedup 的候选集、split 的 sub_issues、物化结果）全部进 `proposal` JSON
- 灰度三段剧本统一靠它：先写审计行 → 主管手动执行（execute-*）→ 稳了开自动

## 理由

1. **够用**：主管工作台需要的「看决策 + revert」单表全覆盖
2. **JSON 装多目标**：dedup 候选/ split sub_issues 是变长且只读，JSON 比关联表轻
3. **审计/物化同表**：执行结果写回 `proposal['materialized']`，决策与执行一处可查
4. **与 ADR-0013 呼应**：LLM 流水日志已有，不重复建 agent_runs
5. **全家桶一致**：classify/split/dedup/escalation 共用同一审计与 revert 机制，新 Agent 零样板

## 后果

- 所有 Agent 写 `agent_decisions`，主管 revert 走统一路径
- `proposal` 无 schema 约束（灵活但需各 Agent 自律字段命名）——已在 CLAUDE.md 各段记录约定
- 跨决策的复杂分析（如「某工单所有 Agent 决策时间线」）靠 `(subject_type, subject_id)` 索引扫
- 若将来要 LLM 成本报表/多目标强查询，再补 agent_runs / decision_targets（非破坏性加表）
