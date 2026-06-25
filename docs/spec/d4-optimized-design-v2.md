# ticket-hub 优化设计方案 v2（2026-06-23 草案，待评审后动手）

> 结合 sample/ticket 差异分析 + 用户新需求重做。**本文档是计划，未动代码。**
> 取代/补充：`d4-stage3-design.md`、`d4-stage3-knowledge-flywheel-skill.md`。

---

## 一、边界重定性（最重要）

| 区块 | 归属 | 说明 |
|---|---|---|
| **AI agent 客服（答客户 + 引用知识）** | **范围外**（紫色） | 飞书侧已有，ticket-hub 不自建；但需调它的 `replay/ask` API 做回归测试 |
| **知识反哺修正闭环** | **范围内** | 本次核心新建 |
| 工单聚合/分类/去重/拆单/Linear/SLA/回写 | 范围内 | 主项目既有 + sample 借鉴 |

**与 sample 的本质区别仍保留**：sample 是「AI 自动答客户、自动关单」；我们是「AI 答客户在外部，ticket-hub 管**治理 + 知识反哺**，发布动作必须人工确认」。

---

## 二、核心：知识反哺修正闭环（对应需求 1.1–1.5）

### 2.1 设计灵魂——回归测试驱动的「去芜存真」

不盲目入库，而是**用可证伪的回归测试守门**：
> 改完知识 → 重跑 AI agent（吃到新知识）→ 新答案 ≈ 人工修正答案 → 才允许发布。

「接近人工答案」= 新答案与人工答案的语义相似度（embedding 余弦 + LLM 评判）≥ 阈值。
这把抽象的「正确性核对」变成一个**客观可测的闸门**，彻底解决「凭一个 LLM 拍脑袋」的问题。

### 2.2 修正确认页：每条不满意 ticket 一行，含四要素（1.1）

| 要素 | 来源 | 落地 |
|---|---|---|
| ① AI 会话过程 | escalation webhook 载荷 | 扩展 `parse_escalation_payload`：新增 `conversation`(多轮) |
| ② 答案引用的知识库内容 | escalation webhook 载荷 | **新增契约字段** `cited_knowledge`(引用的 FAQ/KB 条目 id+片段) |
| ③ ticket-hub autoreply 建议 | 新增 `autoreply` agent | 用三元组 + 知识生成「我们建议的答案」 |
| ④ 人工修正答案 | `reply_sync`（主管回复） | 已有，关联取出 |

### 2.3 每行三个控件（1.2–1.5）

**扩充列(1.3) — 反思建议**：新增 `reply-reflection` skill（LLM），输入四要素，输出**结构化修正建议**：
```json
{ "delete_faq": [{"faq_id": 12, "reason": "答案过时/误导"}],
  "fix_kb": [{"node_token": "...", "issue": "术语错误", "suggest": "..."}],
  "fix_prompt": [{"skill": "answer-router", "term": "...", "to": "..."}] }
```

**测试按钮(1.4)**：负责人按建议改完 FAQ/KB/prompt 后点测试 →
ticket-hub 调外部 AI agent 的 **replay API**（同一原始问题，AI 此时读到的是已修订的知识/skill）→ 拿回新答案 → 算与人工答案的相似度。每次测试留痕（`test_runs[]`）。

**发布按钮(1.5)**：
- 相似度 ≥ 阈值 → 发布按钮**激活** → 点击后：(a) 新答案推客户（经源回写 / AI CS reply API）；(b) 把修订后的 skill/KB/FAQ 落为正式版本（t_skill_md 升版 + Feishu KB 写 + FAQ 表）。
- 相似度 < 阈值 → 发布灰置，提示继续调 prompt/skill/上下文，再测，**直至逼近人工答案**。

### 2.4 数据模型增量

- 新表 `knowledge_corrections`：
  `ticket_id / hub_issue_id / conversation(json) / cited_knowledge(json) / autoreply / human_answer / reflection(json) / test_runs(json[: {at,by,regenerated,similarity}]) / status(pending|testing|ready|published|dismissed) / published_at`
- 复用 `agent_decisions` 记审计（reflection 提案 + publish 动作可 revert 视为撤回发布——但发客户不可撤，故 publish 前二次确认）

### 2.5 关键外部依赖（**2026-06-26 已拍板**）

1. **escalation 载荷扩展**：加 `conversation` + `cited_knowledge` + `skills_used` —— 见
   [ai-cs-api-contract.md](ai-cs-api-contract.md) 接口 1。
2. **AI agent replay API**（接口 2）：`POST {AGENT_BASE}/api/replay`，读最新知识/skill 重答。
3. **skill 归属 → ✅ 方案 B（调 API）**：AI 客服自研，暴露 `/api/skills` 读写
   （接口 3），ticket-hub 发布时调用更新；ticket-hub 侧 `skill_prompts` 表与之对齐双写。
   FAQ/KB 走共享源（飞书 KB + ticket-hub FAQ）。AI 客服 API 由用户侧实现，建议见契约文档。

---

## 三、sample 借鉴整合（需求 2–6）

### 需求 2 + 6(热加载提示词)：DB 化版本提示词 `t_skill_md` ⭐

- 主项目新增 `skill_prompts` 表（对标 `t_skill_md` + 历史表）：`name / type(llm|code) / editable / frontmatter(json) / content_md / version / 审计字段`。
- 现有 `prompts/*.md`（classify_v2 / dedup_v1 / conflict_detect / escalation / vision）**迁入表**，`_load_system_prompt` 改为「读表 + version 缓存失效」（保留文件作 fallback/seed）。
- 后端端点（require_admin）：list / get / **edit（升版+留历史）/ rollback / preview**（几乎可照搬 sample 的 `skill/service.py + router.py`，从 async 改 sync）。
- 前端「Skill 配置页」：列表 + Markdown 编辑器 + 版本历史 + 预览。
- **一石三鸟**：①「分类边界规则人工配置」诉求；②反思 skill（2.3）；③答案路由等所有 LLM skill 可热改。

### 需求 3：前端栈

- 主项目**已是 React 18 + Vite + TS + Tailwind + TanStack Query**——即你要的「react/vite 流行组合」。**结论：不引 sample 的 Vue，保持现状**，新页面（修正页、Skill 配置页）在现有 React 栈上加。后端保持 sync SQLAlchemy + Alembic + Celery 不变。

### 需求 4：hub-issue 语义去重（用 sample 的 hub-dedup 机制）⭐

- 移植 sample `runner.py::_hub_dedup`：在 `linear_push` 建 issue **之前**，对 hub_issue embedding → 召回同产品线已有 hub（余弦 ≥ 阈值 top5）→ LLM 确认 → 命中则**关联已有 hub、不新建 Linear**（保证「一 hub 一 Linear」）。
- 主项目落点：`hub_issues` 加 `embedding`(JSON 列) + 新 `hub_dedup` 服务，`creator/linear_push` 前置调用。

### 需求 5：hub-issue 挂载 90 天内语义重复工单（AI 判定 + 主管纠偏）⭐

- 扩展 dedup：新工单判为某已毕业 hub 的重复（且在 **90 天窗口**内）→ **自动挂到该 hub**（`occurrence_count++`、`last_seen_at`、`ticket_hub_issue_history`），复用已建的 `dedup_execute`。
- AI 判定写 `agent_decisions`；**主管可 relink 纠正归属**（已有 `/supervisor/relink`）。
- 90 天 + 语义：召回时加 `created_at >= now-90d` 过滤；超窗口的重复另起新 hub（避免陈年单复活）。

### 需求 6：回写源系统 + SLA 节假日（用 sample 接口）⭐

- **回写**（KSM lock/handle/supply/return）：主项目 D5 的 `sync_outbox` sender 直接移植 sample 的 `pipeline/writeback.py` + `ksm_client` 写方法（handle 的 `is_deal=结案模式`、supply 退回补资料、payload 字段映射照搬，async→sync）。智齿 `save_ticket_reply` 同理。
- **SLA 节假日/工作日**：移植 sample `sla/holiday_service.py`(sync_year) + `workday.py`(is_workday / add_workday_hours) + `t_holiday` 表，主项目 SLAWatcher 的到期计算改用工作日小时。
- **热加载提示词** = 需求 2，已覆盖。

---

## 四、我的补充分析与调整（latitude 内）

1. **PII 轻量处理**（2026-06-26 拍板：中国区开源模型、不做复杂脱敏、守底线即可）：
   - 不引 sample 全套 desensitize，不做 AES encryptor。
   - 仅保留**底线遮罩**：发客户/出库前对**手机号、身份证**做打码（前3后4 / 前6后4），
     其余（公司名/单号/邮箱）不动。一个 ~30 行 `pii_lite.py` 即可。
   - 全链路国内模型（DashScope/GLM/qwen-vl）同供应商边界，不接海外 LLM，无出境问题。
2. **发布动作不可逆 → 强制人工 + 二次确认**：发客户 + 写 KB 都对外且难撤。保留主项目「人工确认 + 审计」治理，**不引 sample 的自动发布**。这与 2.3 设计天然一致。
3. **autoreply agent 复用 escalation_classify 思路**：不新造轮子，加一个「给出建议答案」的 skill 即可。
4. **相似度阈值（2026-06-26 我确认）**：发布按钮激活条件 = **embedding 余弦 ≥ 0.90 且 LLM
   判「实质等价=是」双信号**（发客户不可逆，宁严勿松）；阈值入 `skill_prompts`/配置可调。
   仅余弦不够稳，叠加 LLM「这两个答案是否实质等价」二判。
5. **不做**：sample 的 info-dedup 答案复用关单（自动答客户，哲学冲突）、多表拆分、全异步、Vue。

---

## 五、分阶段实施计划（按依赖/阻塞排序）

### Phase 0 — ✅ 全部完成并部署生产（2026-06-26）
| # | 项 | 状态 | 实际落点 |
|---|---|---|---|
| 0.1 | PII 轻量遮罩 | ✅ | `core/pii/pii_lite.py`（手机/18位身份证打码）|
| 0.2 | skill_prompts DB 化提示词 + 编辑页 | ✅ | 表 0013 + prompt_store + admin_skills + `/admin/skills` 页；prod 已 seed 6 条 |
| 0.3 | hub-dedup 建 Linear 前去重 | ✅ | hub_issues.embedding(0014) + hub_dedup.py，push 前 supersede |
| 0.4 | 90 天语义重复挂载 | ✅ | auto_mount_recent_duplicate（开关默认关，灰度）|
| 0.5 | SLA 工作日/节假日 | ✅ | holidays 表(0015) + workday.py + watcher 精筛 + admin_holidays（开关默认关）|

**Phase 0 验收**：669 单测 90.2% 覆盖全绿；迁移 0013-0015 已上 prod；6 个新 admin 端点在线；
5 个新开关全部默认关/安全（灰度），不改既有行为。

### Phase 1 — 知识反哺闭环（需求 1，**阻塞于 §2.5 外部 API**）
| # | 项 | 依赖 |
|---|---|---|
| 1.1 | escalation 载荷扩展（conversation + cited_knowledge）| AI 客服契约 |
| 1.2 | `adapters/ai_cs` replay/ask 客户端 | AI 客服 replay API |
| 1.3 | reply-reflection skill + autoreply agent | 0.2(skill 表) |
| 1.4 | `knowledge_corrections` 表 + 修正确认页（四要素 + 扩充列 + 测试 + 发布）| 1.1/1.2/1.3 |
| 1.5 | 相似度比对 + 发布（推客户 + 落 skill/KB/FAQ）| 飞书 KB 写权限 + skill 归属拍板 |

### Phase 2 — 回写闭环（需求 6 另一半，阻塞于 KSM/飞书凭证）
| # | 项 | 依赖 |
|---|---|---|
| 2.1 | sync_outbox sender：KSM/智齿回写（移植 sample writeback）| KSM 写凭证 |
| 2.2 | 飞书 KB 写（发布落库）| 飞书空间编辑权（之前 131006 待开）|

---

## 六、阻塞清单 → 拍板结果（2026-06-26）

1. ✅ **skill 归属 = 方案 B**（调 AI 客服 `/api/skills` API）。AI 客服侧需按
   [ai-cs-api-contract.md](ai-cs-api-contract.md) 实现接口 1/2/3。
2. ⏳ **飞书知识库编辑权**：用户将开放。**ticket-hub 需要的权限**：
   - `wiki:wiki`（写，建/改节点）+ `docx:document`（写文档正文）
   - 且把 app `cli_a97200736cf8dbcb` 加为目标知识库空间的**可编辑协作者**（解之前的 131006）。
3. ✅ **KSM 回写**：查 sample 已明确——**用主项目已有的同一套凭证**（getAppToken→login→
   access_token，app_id/secret/tenant/account/user 主项目都有），端点
   `/ierp/kapi/v2/kded/kded_wos/{lock,handle,return,supply}KsmOrder`（access_token 作 query），
   外加 handler 身份 `KSM_HANDLER_NAME/NUMBER`。**无需新凭证，只需新增写方法**。
   生产 base 用 `ierp.kingdee.com`（主项目当前 UAT 是 `ierpuat`）。
4. ✅ **相似度阈值** = 余弦 ≥ 0.90 且 LLM 等价判定（§四.4）。
5. ✅ **PII** = 轻量底线遮罩（§四.1）。

---

## 七、建议启动顺序

**先做 Phase 0 全部**（不依赖任何外部 API，立即产出价值，且 0.2 的 skill 表是 Phase 1 反思 skill 的地基）→ 期间你并行解决 §六 的外部依赖 → 再做 Phase 1 闭环 → 最后 Phase 2 回写。

> 等你对本计划 + §六 阻塞项给反馈，我再动手。建议从 **0.1 PII 脱敏** 或 **0.2 skill 表** 起步。
