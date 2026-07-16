# Operation 自动答复 · 设计文档

- 日期：2026-07-15
- 状态：设计已批准，待写实施计划
- 范围：Operation 工单毕业后自动调 AI 客服 agent 生成答复并回写客户（路线图批次 4️⃣ / ADR-0016 §3 「Operation 未命中 → 直接答复客户」分支）
- 相关：`docs/adr/0016-agent-pipeline-restructure.md` §3 流程图、`adapters/ai_cs/client.py`（replay 能力）、`services/cascade/reply_sync.py`（author_reply 复用）、参考项目 `ticket-hub/app/pipeline/runner.py` S6-S7-D

## 1. 背景与问题

ADR-0016 §3 流程图对 Operation 类工单画了「毕业 hub_issue → hub_dedup → 未命中且非 escalation → **直接答复客户**」分支。当前代码 `_route_by_type` 对 Operation 只毕业不答复——毕业后停在 hub_issue，等主管手动在详情页写回复。图承诺的自动答复完全没实现。

参考项目 ticket-hub 用公司内部 agent（`INTERNAL_AGENT_URL`）+ answer-router 重新分 A/B/C/D 决定答复/转研发/补料。新项目 triage 已先分类（Operation 已确定是运营类可答复问题），**不需要重新分类**，只需二元判断「agent 这次答复能不能直接发给客户」。

## 2. 决策要点（已锁定）

| 项 | 决策 |
|---|---|
| agent 来源 | 复用现有 `adapters/ai_cs` 的 `replay(question=, use_latest_knowledge=True)` |
| 答复判断 | harness 硬判（不加 LLM 判断层，灰度观察后再决定） |
| 答复后 | 自动答复 + 自动关单（全自动，按图；出站回写仍受 writeback 灰度双保险） |
| 来源范围 | KSM + 智齿 + zammad 的 Operation 都自动答复；**escalation(ai_cs) 除外**（走 reflect 反思队列） |
| hub_dedup 判断 | 本批次简化——用 `created==True` 作为「新毕业需答复」判据，不单独判 dedup 命中（P2-1 hub_dedup 覆盖 Operation 完成后再加"命中复用不重答"） |
| 灰度 | 新增 `operation_auto_reply_enabled`（默认 false）+ `operation_auto_reply_min_length`（默认 10） |
| 审计 | agent_decisions decision_type=`auto_reply`（CHECK 已有，V1 预留）；authored_by=`agent:ai_cs` |

## 3. 核心流程

### 新建 `services/agents/operation_answer.py`

```
auto_answer_operation(hub_issue_id: int) -> bool  # True=已答复, False=留主管
  1. 门控：operation_auto_reply_enabled 关 → return False
  2. build_client(settings)（ai_cs）——未启用/未配置 → return False
  3. 取 hub_issue + 首个关联工单，拼问题：
       q = "{product_line}-{module}：{canonical_body 或 title}"
  4. client.replay(question=q, use_latest_knowledge=True) → answer
  5. harness 硬判 _is_answer_sendable(answer):
       - replay 抛异常（AiCsError）→ return False（留主管 + log）
       - answer 空 或 len < operation_auto_reply_min_length → False
       - answer 含转人工信号词（转人工/无法回答/无法处理/请联系/人工客服）→ False
       - 否则 → True
  6. 可发 → author_reply(db, hub_issue_id, content=answer, authored_by="agent:ai_cs")
       （复用 cascade → outbox → KSM/智齿 回写关单）
  7. 写 agent_decisions(decision_type="auto_reply", subject=hub_issue,
       proposal={question, answer, trace_id, sent: true})
  8. return True
```

### 接入点 `webhooks.py` `_route_by_type`

```python
if ticket_type == "Operation":
    result = create_hub_issue_for_ticket_auto(ticket_id)
    if (
        result is not None
        and result.created
        and settings.operation_auto_reply_enabled
        and _ticket_source(ticket_id) != "ai_cs"  # escalation 走 reflect
    ):
        auto_answer_operation(result.hub_issue_id)
    return
```

注意：
- 现有 `_route_by_type` 对所有非 Complaint 类型统一 `create_hub_issue_for_ticket_auto`。改造后 Operation 单独分支（毕业 + 自动答复），Bug_fix/Demand/Internal_task 保持原逻辑（毕业 + Bug/Demand 内部推 Linear）。
- `_ticket_source(ticket_id)` 是本次新增的小辅助（开 make_session 查 `ticket.source_code`），或把来源判断放进 `auto_answer_operation` 内部（拿 hub_issue 的关联 ticket 判 source_code=='ai_cs'）——实现时择一，倾向后者（少一次 session）。

## 4. harness 硬判规则（判断答复能否发）

| 情况 | 处理 |
|---|---|
| replay 抛 AiCsError（网络/超时/agent 错） | 留主管 + log warning |
| answer 为空 | 留主管 |
| answer 长度 < `operation_auto_reply_min_length`(10) | 留主管 |
| answer 含转人工信号词 | 留主管 |
| 否则 | 视为有效答复，发 |

转人工信号词集合（`_TRANSFER_HINTS`）：`{"转人工", "无法回答", "无法处理", "请联系", "人工客服", "抱歉"}`（可调）

**原则**：任何不确定都留主管（hub_issue 停在 created，进主管队列），绝不静默发不确定答复。

## 5. 配置（config.py 新增）

```python
# ---- Operation 自动答复（调 ai_cs replay 生成答复回写客户）----
# 默认关：开了才对新毕业的 Operation hub_issue 自动答复。
# 出站回写仍受 ksm/zhichi_writeback_enabled + dry_run 二层灰度保护（双保险）。
operation_auto_reply_enabled: bool = False
operation_auto_reply_min_length: int = 10  # 答复短于此视为无效，留主
```

## 6. 灰度与安全

- **总开关默认关**：`operation_auto_reply_enabled=false` → `_route_by_type` 完全不触发自动答复，现有流程不变
- **双层灰度**：即使自动答复生成了 outbox 行，真正发到 KSM/智齿仍受各自 `*_writeback_enabled` + `*_writeback_dry_run` 保护——所以「生成答复」和「真发客户」是两道独立开关
- **escalation 隔离**：ai_cs 来源工单不自动答复（它有黄金三元组，走 reflect 人工诊断）
- **依赖 ai_cs 已配**：build_client 未启用/凭证缺 → 静默跳过留主管

## 7. 审计

- `agent_decisions`：decision_type=`auto_reply`，subject_type=`hub_issue`，subject_id=hub_issue_id，proposal={question, answer, trace_id, sent}
- `hub_issue_reply_history`（author_reply 自动写）：authored_by=`agent:ai_cs`（区别主管手动的 `user:xxx`）
- log：`operation_auto_reply_sent` / `operation_auto_reply_skipped`（带原因）

## 8. 测试（`tests/unit/services/test_operation_answer.py`）

mock ai_cs client：
- 答复有效 → author_reply 被调 + outbox 入队 + agent_decisions auto_reply 写入
- answer 空 → 留主管（author_reply 不调）
- answer 太短 → 留主管
- answer 含转人工信号词 → 留主管
- replay 抛 AiCsError → 留主管 + 不崩
- operation_auto_reply_enabled=false → 不触发
- ai_cs 未配置 → 静默跳过
- 集成：webhooks `_route_by_type` Operation + enabled → 触发；escalation(ai_cs) 来源 → 不触发

## 9. 非目标（本次不做）

- LLM 判断答复质量层（harness 硬判先行，灰度观察后再评估）
- hub_dedup 命中复用答复（依赖 P2-1，本批次用 created==True 简化）
- answer-router A/B/C/D 重新分类（triage 已分类，不需要）
- 智齿简化流水线（决策 3：智齿也自动答复，不遵循参考项目）
- 图片/多模态问题（replay 目前纯文本）

## 10. 影响面

- 改动：`webhooks.py` `_route_by_type`（Operation 分支）、新增 `services/agents/operation_answer.py`、`config.py`（2 项）
- 无数据库迁移（auto_reply decision_type 已在 CHECK，reply_history 表已存在）
- 无前端改动（自动答复是后台，主管在 hub_issue 详情页能看到 agent 答的回复版本）
- 复用：author_reply（cascade+outbox）、ai_cs replay、create_hub_issue_for_ticket_auto
