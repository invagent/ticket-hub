# Operation answer-router（自动答复 + 自动补料）· 设计文档

- 日期：2026-07-15
- 状态：设计已批准，待写实施计划
- 范围：在批次 4️⃣（Operation 自动答复）基础上，加 answer-router LLM 判 C/D/转人工——能精准答则答，信息不足则自动补料退回客户，答不了则转人工。参照 ticket-hub `runner.py` S6-S7 + `answer-router` skill。
- 相关：批次 4️⃣ spec `2026-07-15-operation-auto-reply-design.md`、`services/agents/operation_answer.py`、`services/cascade/supply_sync.py`（request_supply）、参考项目 `ticket-hub/skills_md/answer-router/`

## 1. 背景与问题

批次 4️⃣ 已实现 Operation 自动答复：毕业后调 ai_cs.replay → harness 二元硬判（能发/留主管）。但缺 ticket-hub 的核心能力——**信息不足时自动退回客户补料**。当前 agent 答复里若说「请提供截图」，系统不认识这是补料请求，要么当普通答复发出，要么留主管手动。

目标（用户明确）：hub-issue 核心价值 = agent 精准答复客户工单。信息不足应自动打回客户要更准资料，帮 agent 答得更准，而非瞎答或占用人工。

## 2. 决策要点（已锁定）

| 项 | 决策 |
|---|---|
| 分类方式 | 保留 hub-issue triage 先分类（Operation），不改成 ticket-hub 的 agent 先答再分。answer-router 只在 Operation 内判 C/D/转人工（不判 bug/需求，triage 已分） |
| C/D 判断 | 方案二：hub 侧 LLM answer-router（独立 prompt，纳入 skill_prompts 三槽，仿 triage/hub_dedup） |
| 补料话术 | answer-router LLM 顺便生成——判 C 时输出 `supply_note`（补料请求文案） |
| LLM 失败 | 兜底转人工（留主管，绝不瞎答） |
| 知识反哺进化 | 本次不动，保持只 escalation 触发（后续沟通） |
| 灰度 | 复用 `operation_auto_reply_enabled`（默认 false） |
| 改造原则 | 基于批次 4️⃣ + 现有 request_supply/author_reply/LLMRouter，不推翻 |

## 3. 核心流程

```
triage → Operation → 毕业 hub_issue（created）
  → auto_answer_operation(hub_issue_id):
     1. 门控 operation_auto_reply_enabled + 非 ai_cs 来源（escalation 走 reflect）
     2. ai_cs.replay(question="{产品线}-{模块}：{body}") → agent answer
     3. answer-router LLM 判（输入：客户问题 + agent答复）:
          → {branch: "D"|"C"|"transfer", supply_note?: str}
     4. 按 branch:
        D → author_reply(hub, content=answer, authored_by="agent:ai_cs")
             → cascade → outbox kind=reply → 回写关单（复用批次4️⃣）
        C → request_supply(hub, note=supply_note, requested_by="agent:ai_cs")
             → outbox kind=supply → 退回客户要资料（复用现有 supply_sync）
        transfer → 留 hub_issue created，进主管队列
     5. 写 agent_decisions(auto_reply, proposal={branch, question, answer, supply_note})
```

## 4. answer-router LLM（新增）

### prompt：`prompts/answer_router.md`（纳入 skill_prompts 三槽）

判定输入：客户原始问题 + agent 答复。输出 JSON：
```json
{"branch": "D", "supply_note": ""}
{"branch": "C", "supply_note": "为定位问题，请提供开票时的完整报错截图 + 操作时间"}
{"branch": "transfer", "supply_note": ""}
```

判定规则（prompt 内）：
- **D（正常答复）**：agent 给出了客户可直接用的有效解答（操作步骤 / 明确结论）
- **C（信息不足补料）**：agent 答复表明需要客户提供更多信息才能精准回答（缺截图/日志/复现步骤/具体报错/环境）→ 生成 supply_note（面向客户的补料请求，礼貌具体）
- **transfer（转人工）**：agent 明确答不了 / 要求转人工 / 答复空泛无实质
- 拿不准 → transfer（保守，不瞎答不瞎补）

### 调用（仿 triage）
- `LLMRouter.from_settings()` + `load_prompt("answer_router")`（三槽）
- `response_format={"type":"json_object"}`，temperature=0
- 解析失败 / LLMRouterError → 兜底 transfer

## 5. 改动点（基于现有）

1. **新建 `prompts/answer_router.md`** + 从文件导入 skill_prompts（管理后台可调三槽）
2. **扩 `services/agents/operation_answer.py`**：
   - 新增 `_route_answer(question, answer) -> AnswerRoute`（调 answer-router LLM，返回 branch + supply_note）
   - `auto_answer_operation` 主流程：replay → _route_answer → 按 branch 执行（D=author_reply / C=request_supply / transfer=留主管）
   - 保留批次 4️⃣ 的门控 + escalation 排除
3. **C 分支复用 `request_supply`**（supply_sync.py，现成）
4. **审计**：agent_decisions auto_reply，proposal 记 branch/supply_note
5. 无迁移、无新配置（复用 operation_auto_reply_enabled）

## 6. 关键复用（不新建）

| 能力 | 复用 |
|---|---|
| agent 答复 | `ai_cs.replay`（现有） |
| C/D 判断 | `LLMRouter` + 新 answer_router prompt（仿 triage） |
| D 答复出站 | `author_reply` → outbox reply（批次4️⃣验证过） |
| C 补料出站 | `request_supply` → outbox supply（KSM supplyKsmOrder / 智齿 ticket_status=2，批次1️⃣验证过映射） |
| 审计 | agent_decisions auto_reply |

## 7. 灰度与安全

- `operation_auto_reply_enabled` 默认 false → 不触发
- 出站真发（reply/supply）仍受 ksm/zhichi_writeback_enabled + dry_run 二层灰度
- LLM 判断失败 → transfer 留主管
- escalation(ai_cs) 来源不走此路（reflect 反思队列）
- ai_cs 未配置 → 跳过留主管

## 8. 测试

mock ai_cs replay + mock LLMRouter（answer-router 返回）：
- branch=D → author_reply 调用 + outbox reply
- branch=C → request_supply 调用（note=supply_note）+ outbox supply
- branch=transfer → 都不调，留主管
- answer-router LLM 异常 → 兜底 transfer
- ai_cs replay 异常 → 留主管
- operation_auto_reply_enabled=false → 不触发
- escalation(ai_cs) 来源 → 不触发
- answer-router 返回非法 branch → 兜底 transfer

## 9. 非目标

- 知识反哺进化（保持只 escalation，后续沟通）
- A/B（bug/需求）分类——triage 已分，answer-router 只判 Operation 内 C/D/transfer
- LLM 生成答复内容（答复来自 ai_cs，answer-router 只判走向 + 生成补料文案）
- 前端改动（自动答复/补料是后台，主管在 hub 详情页看到结果）

## 10. 影响面

- 改动：`services/agents/operation_answer.py`（扩）、新增 `prompts/answer_router.md`
- 无迁移、无新配置
- 复用：ai_cs.replay / LLMRouter / author_reply / request_supply / agent_decisions
- 风险：动 Operation 自动答复路径（灰度默认关，充分单测）
