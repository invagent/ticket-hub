# D4 第③段详细设计：AI 客服闭环 + Vision 多模态（2026-06-12 草案）

> 状态：**开放问题已定稿（2026-06-12）**，见 §八。实施待 AI 客服 API 路径。
> 前提变化：已存在一套 AI agent 客服系统（API 路径待补），能解答 Operation
> 类操作问题。原计划的「自建 How-To RAG（knowledge_chunks + how_to agent）」
> **取消自建**，改为与该系统集成。

## 一、边界修正：谁负责什么

| 能力 | 归属 | 说明 |
|------|------|------|
| Operation 操作类问题解答 | **已有 AI 客服系统** | 不重复建设 |
| 多模态图像识别（截图/报错图）| **ticket-hub Vision 管道** | AI 客服不支持，hub 补位 |
| 解答失败 → Bug/需求二次分类 | **ticket-hub escalation 链** | AI 客服不支持，hub 补位（本设计核心）|
| Bug_fix/Demand 建单 → Linear → 状态回流 | ticket-hub（已建成）| 第①②段成果直接复用 |
| Operation 回复版本化 + 回写源系统 | ticket-hub（已建成 reply_sync）| 可选反哺 AI 客服知识库 |

一句话：**AI 客服管「答」，ticket-hub 管「答不上来之后的事」**。

## 二、总体架构

```
客户提问（文本+截图）
   │
   ▼
已有 AI agent 客服 ──解答满意──▶ 会话关闭（hub 可选记录，不建单）
   │
   │ 解答失败 / 客户不满意（信号来源见 §三.1）
   ▼
POST /webhook/cs-escalation（hub 新增）
   │  载荷：原问题 + AI 回答历史 + 不满反馈 + 附件 + 客户标识
   ▼
建 Raw ticket（source='ai_cs'）
   │
   ├─▶ Vision 管道（§四）：附件图 → 多模态 LLM → 结构化文本 → 补进 body
   │
   ▼
escalation_classify Agent（§三.3）
   │  专用 prompt：携带「AI 客服已解答失败」上下文 →
   │  判定 Bug_fix / Demand / Operation(复杂操作需人工) / Internal_task
   ▼
既有链路：毕业 hub_issue → Bug_fix/Demand 推 Linear → 状态回流 → outbox 回写
```

## 三、核心交互：不满意 → 二次分类流转

### 3.1 不满意信号的三种可能来源（待 API 确认后定）

| 方案 | 机制 | 评价 |
|------|------|------|
| **A. AI 客服主动回调**（推荐）| 会话判定失败（客户点"不满意"/转人工/连续追问 N 轮）时 AI 客服侧 POST hub 的 escalation webhook | 实时、信号语义最准；需要对方系统支持 outbound |
| B. hub 轮询会话 API | Celery 定时拉「失败会话」列表 | 对方只读 API 即可；有延迟、需要游标管理 |
| C. 源工单兜底 | 客户回到 KSM/智齿重新提单，dedup 召回发现与 AI 客服会话同主题 | 已天然存在（dedup 已建成），但丢失「AI 已答过」上下文 |

设计取 **A 为主、C 为兜底**；若对方无 outbound 能力则降级 B。
**→ 开放问题 ①**：AI 客服系统有没有会话结束/转人工的回调或事件推送？

### 3.2 二次分类的输入是「黄金三元组」

普通 classify 只有工单标题/正文；escalation_classify 多了两层关键信号：

```
① 客户原始问题（+Vision 提取的截图内容）
② AI 客服的回答（完整 Q/A 轮次）
③ 客户的不满反馈（"还是不行"/"我要的不是这个"/转人工原因）
```

这个三元组大幅提高 Bug/需求判定准确率：
- AI 答了标准操作步骤、客户说"按步骤做了还是报错" → **Bug_fix** 强信号
- AI 答了"当前不支持"、客户说"那你们要支持" → **Demand** 强信号
- AI 答非所问、客户重述问题 → 仍可能是 Operation，转人工
- 判定原则沿用：拿不准 → Bug_fix（漏 bug 代价 > 误判）；
  但 **escalation 场景默认显著压低 Operation 概率**（AI 已经试过操作解答且失败）

### 3.3 escalation_classify Agent

- `services/agents/escalation_classify.py` + `prompts/escalation_classify_v1.md`
- 复用 LLMRouter / agent_decisions 审计 / 灰度剧本（先审计→手动→自动），
  与 classify/conflict_detect/dedup 完全同构，约 1 天工作量
- 输出写 `tickets.predicted_*`（复用现有字段），`agent='escalation_classify_v1'`
  在 agent_decisions 里与普通 classify 区分
- 自动毕业门槛独立配置 `ESCALATION_AUTO_CONFIDENCE`（建议 0.85，比普通 0.80 高——
  这条链动作更重：直接建 hub_issue 推 Linear）

### 3.4 新 webhook 与数据落点

- `POST /webhook/cs-escalation`（access_token 鉴权，同现有三源）
- 新 source 种子：`ai_cs`（迁移种入，ON CONFLICT DO NOTHING）
- ticket 字段映射：`source_ticket_id` = AI 客服会话 ID；`body` = 客户原问题；
  `source_payload` = 完整 Q/A 轮次 JSON（审计 + 二次分类输入）
- 客户身份：载荷里的 erp_uid/mobile/email 走现有 identity 解析
- 会话 Q/A 不挤进 body（保持 body 是"客户的问题"语义），
  escalation_classify 从 source_payload 读轮次

## 四、Vision 多模态管道

### 4.1 模型选型决定 PII 边界（重要论证）

原技术债写「Vision 接外部多模态 LLM 前必须补 PII encryptor」。但：

- **DashScope qwen-vl-plus / GLM-4V 都是国内多模态模型**，与现有
  classify/dedup 用的 deepseek-v4-flash/GLM 是**同一供应商边界**
- 现有文本链路并未做 PII 脱敏（接受的风险边界），Vision 用同边界供应商
  在数据合规上没有新增暴露面
- **结论：选国内多模态 → PII encryptor 不再是第③段前置**，降级为
  「接 OpenAI/Anthropic（海外）时再补」的原始语义
- failover 顺序沿用 `LLM_PROVIDER_ORDER`，embeddings 客户端同款套路

**→ 开放问题 ②**：接受用 qwen-vl-plus/GLM-4V（国内），PII encryptor 出第③段？

### 4.2 attachments 表（迁移 0012）

```
attachments:
  id PK
  ticket_id FK
  source_url / storage_key（MinIO）
  mime / size_bytes
  kind: image | pdf | video | other
  vision_status: pending | extracted | skipped | failed
  extracted_text TEXT        ← Vision 输出（OCR 报错文本 + 界面描述）
  vision_model / cost_usd
  created_at / updated_at
```

### 4.3 Vision Agent 链路

```
ingester 收附件（KSM subscribeCallback 附件字段 / cs-escalation 载荷）
  → 下载存 MinIO → attachments 行（vision_status='pending'）
  → vision_extract Agent（BG task）：
      image → qwen-vl-plus → 结构化提取：
        { "ocr_text": 报错原文, "ui_context": 所在界面/操作路径,
          "summary": 一句话描述 }
  → extracted_text 落库 + ticket.body 追加「[附件识别] …」段
  → 之后的 classify / dedup / escalation_classify 天然受益
    （embedding 文本里包含报错原文 → dedup 召回质量直接提升）
```

- 开关 `VISION_ENABLED`（默认 false 灰度）；单图成本 ~¥0.01 内
- 失败吞错：vision_status='failed'，不阻塞 ingest 链（与全家桶一致）
- 顺序：vision **在 classify 之前**（让分类吃到图里的报错文本）

### 4.4 KSM 附件获取

`tickets.attachments_synced` 字段已预留。KSM subscribeCallback 的附件
下载接口与鉴权待确认（**→ 开放问题 ③**：KSM 附件 URL 是否需要单独的
下载凭证 / 有效期？）。智齿/zammad 附件后续按需接。

## 五、与已有 AI 客服的适配层（占位，等 API 路径）

`adapters/ai_cs/`（待 API 文档后落地），预期需要的能力：

| 能力 | 用途 | 必要性 |
|------|------|--------|
| 会话详情查询（by session_id）| escalation 载荷瘦身时反查 Q/A | 视回调载荷而定 |
| 失败/转人工事件回调注册 | §三.1 方案 A | 核心 |
| 知识库导入 API | hub 的 Operation 回复（reply_sync 版本化内容）反哺 AI 客服 | 可选增强（**→ 开放问题 ④**）|

知识反哺如果可行，形成完整飞轮：
`客户问题 → AI 答不上 → hub 人工解答（reply_sync）→ 反哺知识库 → 下次 AI 能答`。

## 六、数据模型与配置增量

- 迁移 0012：attachments 表 + sources 种子 `ai_cs`
- 配置：`VISION_ENABLED`(false) / `VISION_MODEL`(qwen-vl-plus) /
  `ESCALATION_AUTO_CONFIDENCE`(0.85) / `AI_CS_*`（适配层凭证，待定）
- 不新建：knowledge_chunks（取消自建 RAG）、escalations 表（复用 tickets + source='ai_cs'）

## 七、实施切分与验收

| 步骤 | 内容 | 预估 | 依赖 |
|------|------|------|------|
| ③-1 | attachments 表 + Vision 管道 + KSM 附件接入 | 2~3d | 开放问题 ②③ |
| ③-2 | /webhook/cs-escalation + escalation_classify Agent | 2d | API 路径 + 开放问题 ① |
| ③-3 | 适配层 adapters/ai_cs +（可选）知识反哺 | 1~2d | API 路径 + 开放问题 ④ |

验收（对照原计划口径调整）：
- Vision：抽 20 张真实工单截图人工核对提取质量 ≥85% 可用
- escalation 二次分类：先积累审计行，离线评测 ≥85% 再开自动毕业
- ~~How-To 采纳率 ≥50%~~ → 改为 AI 客服侧指标（不归 hub）

## 八、开放问题 → 定稿（2026-06-12 拍板）

1. **不满意信号来源** ✅ → **方案 A 实时 webhook**。AI 客服有「会话失败/转人工」
   事件回调，escalation 走 `POST /webhook/cs-escalation` 实时触发。不需要轮询兜底。
2. **PII 边界** ✅ → **全部走管理大模型**（qwen-vl / GLM-4V / DeepSeek-v4 或同类，
   均国内）。**PII encryptor 正式移出第③段**，降回「接海外 LLM 才补」的原始语义。
   Vision 与文本链同一供应商边界，无新增暴露面。
3. **KSM 附件** ✅ → **截图为主**。Vision 管道聚焦 image（kind='image'），
   PDF/video 留作后续；attachments.kind 仍保留全集以防扩展。
4. **知识反哺** ✅ → **有（飞书侧知识库），且要做成独立 skill**，作为**重要待办**
   单独设计 → 见 [d4-stage3-knowledge-flywheel-skill.md](d4-stage3-knowledge-flywheel-skill.md)。
   **核心要求：FAQ 不盲目入库**——新答复必须经存量知识库核对校验，去芜存真：
   既补全正确版图，也对错误答复来源予以剔除/修正。
5. **Operation 转人工** ✅ → **走 hub 人工回复（reply_sync）**。escalation 判回
   Operation（复杂操作）时由 hub 主管在工作台撰写回复，复用第②段 reply_sync 链路
   （版本化 + 级联 + outbox 回写源 + 可反哺知识库）。工单归属 hub。

### 定稿带来的简化

- 删掉「轮询兜底」「PII encryptor 前置」两块工作 → ③-1/③-2 更轻
- escalation 判回 Operation 不需要新代码：直接进 hub_issue(Operation) → 主管 reply_sync
- 知识反哺独立成 skill（飞轮逻辑复杂，不塞进工单链），见专项设计
```
