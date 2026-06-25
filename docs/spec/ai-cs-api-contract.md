# AI agent 客服 ↔ ticket-hub 接口契约建议（方案 B，2026-06-26）

> 背景：AI 客服自研，与 ticket-hub 分系统。方案 B = ticket-hub **调用** AI 客服的 API
> 来做知识反哺闭环（回放重跑 + 修订 skill）。本文档是给 AI 客服侧的接口建议，
> 供你们实现；ticket-hub 侧按此对接 `adapters/ai_cs/`。
> 鉴权统一：`AGENT_APPID` + `AGENT_APP_KEY`（沿用 sample 的 env 命名），建议 HMAC 签名或 Bearer。

---

## 接口 1：escalation 回调（AI 客服 → ticket-hub，已有，需**扩展载荷**）

客户对 AI 答复不满意/转人工时，AI 客服 POST：
`POST {TICKET_HUB}/webhook/cs-escalation?access_token=<webhook_token>`

```jsonc
{
  "session_id": "会话ID(幂等键)",
  "original_question": "客户原始问题",
  "ai_answer": "AI 最终答复",
  "dissatisfaction": "不满反馈/转人工原因",
  // ↓↓↓ 新增（知识反哺闭环必需）
  "conversation": [                       // 完整多轮会话
    {"role": "user", "text": "...", "ts": "..."},
    {"role": "assistant", "text": "...", "ts": "..."}
  ],
  "cited_knowledge": [                     // AI 答复引用了哪些知识（去芜存真的关键）
    {"type": "faq", "id": "F123", "title": "...", "snippet": "...", "score": 0.91},
    {"type": "wiki", "node_token": "...", "title": "...", "snippet": "..."}
  ],
  "skills_used": ["answer-router", "..."], // 本次答复用到的 AI skill（用于反思定位）
  "customer": {"erp_uid": "...", "mobile": "...", "email": "...", "name": "..."},
  "attachments": [{"url": "...", "filename": "..."}]
}
```
> 没有 `conversation`/`cited_knowledge` 也能降级跑（只是反思精度下降）。

---

## 接口 2：replay 回放重跑（ticket-hub → AI 客服，**新增，测试按钮核心**）

负责人修订知识/skill 后，ticket-hub 让 AI「就同一问题、用**当前最新**知识/skill，重答一次」：
`POST {AGENT_BASE_URL}/api/replay`

请求：
```jsonc
{
  "session_id": "原会话ID",          // 二选一：用原会话上下文重跑
  "question": "客户原始问题",         // 或直接给问题
  "context": {                       // 可选：覆盖/补充上下文（如指定产品线/模块）
    "product_line": "...", "module": "..."
  },
  "use_latest_knowledge": true       // 必须读最新 FAQ/KB/skill（修订后的）
}
```
响应：
```jsonc
{
  "answer": "AI 重新生成的答复",
  "cited_knowledge": [ /* 同接口1 结构，便于对比引用是否变化 */ ],
  "skills_used": ["..."],
  "trace_id": "..."
}
```
> 关键语义：replay 必须命中**修订后的**知识与 skill（否则回归测试无意义）。

---

## 接口 3：skill / prompt 读写（ticket-hub → AI 客服，**新增，发布核心**，方案 B）

发布时 ticket-hub 要把修订后的 AI skill 落为正式版本。建议 AI 客服暴露：

```
GET    {AGENT_BASE_URL}/api/skills              列出 skill（name/version/可编辑）
GET    {AGENT_BASE_URL}/api/skills/{name}       取 skill 正文 + 版本
PUT    {AGENT_BASE_URL}/api/skills/{name}       更新 skill（升版+留历史）
  body: {"content": "新提示词正文", "operator": "user:xxx", "reason": "..."}
POST   {AGENT_BASE_URL}/api/skills/{name}/rollback  {"version": N}
```
> 与 ticket-hub 侧 `skill_prompts` 表（见优化方案 §三需求2）字段对齐，便于双向同步/审计。
> 若 AI skill 数量固定，PUT 单个即可；不需要复杂的 skill 创建。

---

## 接口 4：FAQ / 知识库（走飞书，不经 AI 客服）

- FAQ：ticket-hub 自管 `faq` 表 + 飞书知识库写（接口 1 的 `cited_knowledge` 里 wiki 类型对应飞书节点）。
- 发布时：删/改 FAQ 在 ticket-hub 库；改 wiki 走飞书 docx 写 API（需空间编辑权，见下）。
- AI 客服 replay 时通过共享的飞书 KB + ticket-hub FAQ 读到最新——**前提：AI 客服与 ticket-hub 读同一份 FAQ/KB 源**（建议明确这点）。

---

## ticket-hub 侧对接落点（adapters/ai_cs/）

```
adapters/ai_cs/client.py:
  AiCsClient.replay(session_id|question, context, use_latest=True) -> ReplayResult
  AiCsClient.list_skills() / get_skill(name) / update_skill(name, content, operator) / rollback(name, ver)
配置：AGENT_BASE_URL / AGENT_APPID / AGENT_APP_KEY（沿用 sample env 命名）
```

---

## 给 AI 客服侧的最小实现清单

1. escalation 回调载荷加 `conversation` + `cited_knowledge` + `skills_used`（接口1）。
2. 实现 `/api/replay`（接口2）——**读最新知识/skill 重答**。
3. 实现 `/api/skills` 读写（接口3）。
4. 与 ticket-hub 约定**共享同一份 FAQ/KB 源**（飞书 KB + ticket-hub FAQ），否则回归测试不闭环。
5. 鉴权：`AGENT_APPID/APP_KEY`。
