# ADR 0016：Agent 流水线重构 — 分诊→原子化→按类型分流

- 状态：**已采纳（Accepted）**（决策全部锁定，2026-07-05）
- 日期：2026-07-05
- 相关：[[0014-agent-decisions-minimal-audit]]、[[0015-json-vectors-over-pgvector]]、d4-stage3-design、console-redesign

## 1. 背景与问题

当前所有类型的工单走**同一根线性管子**（`run_post_ingest_agents`）：

```
vision → classify → 毕业hub_issue → dedup → conflict_detect → split
```

三个结构性毛病：

1. **dedup 排在 split 前面**。混合工单（「登录失败(bug)+补开发票(operation)」）还没被拆成原子问题，就拿去跟单一问题的 hub_issue 查重——查重语义错乱。这也是 **dedup/split 提案互斥只做了一半**的根因：执行合并后再执行拆分不被挡，会产生「已判重复的工单又被拆」的矛盾态。
2. **类型定义两处维护**。`classify` 和 `escalation_classify` 各存一份四类型文案，改一处另一处不同步，两条链分类口径会悄悄分叉。
3. **skill 版本双轨制**。`classify_v1/classify_v2` 用两个 skill 名 + config 选版，与 `skill_prompts` 表自带的版本历史+回滚**重复实现了版本概念**；列表越攒越长且分不清哪个生效。

同时，业务侧有四个未落地的诉求：Operation 的反思闭环只有 `skill` 病因有落点；Demand 跨多责任人无法在「一个需求」下协作跟踪；投诉类被自动化消化而非第一时间给人；运营/管理员权限未分层。

## 2. 决策

采用 **类型优先 + 先原子化 + 按类型分流** 的流水线，并附带三项横切决策（版本三槽、权限双层、投诉人工分诊）。

### 2.1 核心原则

- **split（原子化）挪到 dedup 之前**：混合工单先拆成「一个问题一张原子子单」，每个原子单再各自 `classify → 毕业hub_issue → hub_dedup`。原子单天生不含多问题 → **永不再拆**（自然满足「子单只拆一次」），且 dedup 只见原子单 → **互斥矛盾从数据流上消失**，不再需要重量级守卫。
- **混合单闸门（灰度语义，已定）**：自动拆分关闭时，triage 判定「混合」的工单**停摆进「待拆分」人工队列**，主管确认拆分后子单才继续下游；不采用「整单先往下走+提案旁挂」（那会重新引入事后回滚 hub 挂靠的矛盾态）。混合是少数（triage 高置信才标），人工队列压力可控。非混合单不受影响、即时流转。
- **triage 合并**：`classify + conflict_detect` 合成一次 LLM（「定类型 + 是否混合 + 子问题列表」），省一次调用，避免两处类型定义分叉。
- **escalation 链保留专用 `escalation_classify`**：AI 客服 escalation 有黄金三元组上下文与「压低 Operation 概率」的特殊逻辑，**不走普通 triage**；仅 KSM/智齿/zammad 普通源走 triage。两者共享 `type_taxonomy` 片段保持口径一致（含新增的 Complaint）。
- **hub_dedup 是唯一主查重**：所有类型毕业 hub_issue 时对既有 hub_issue 查重；命中则挂靠复用。ticket 级 `dedup.py` 退役删除（见 §6）。

### 2.2 五类型分流终局

| 类型 | 路径 |
|---|---|
| **Operation** | 毕业 hub_issue → hub_dedup → 命中?复用答复级联 : （**仅来自 AI 客服 escalation 的新失败**）进 reflect 反思队列；来自 KSM/智齿的新 Operation → 直接答复 |
| **Bug_fix** | 毕业 hub_issue → hub_dedup → 命中迭代中?跟踪排期/发版 : 推 Linear |
| **Demand** | 毕业 hub_issue → hub_dedup → 命中?跟踪 : 定责任人 → 单责任人?推 1 个 Linear issue : **owner-split**（1 hub_issue 挂 N 个 Linear 子 issue） |
| **Complaint（投诉，新增第 5 型）** | 停在 ticket 层，红色高亮人工队列，**不自动毕业**；人工二选一：手工关闭 / 转 hub_issue 并指定 type（Op/Bug/Demand，复用现成 `create-hub-issue` 的 type 覆盖） |
| **Internal_task** | 内部处理 |

### 2.3 skill 三槽版本（替代 _v1/_v2 双轨）

内部 skill_prompts 表改为**每个 skill 名固定三槽**，与外部 AI 客服 `skill-management.json` 的 draft→published→superseded **同构**：

- `previous`（上一版，回滚用）
- `current`（在用版，live）
- `draft`（准备迭代版，验证中，不生效）

agent 统一 `load_prompt("classify")`，版本完全走三槽；废弃 `classify_v1/v2` 命名。**内外一套版本语义**。

### 2.4 权限双层

| 角色 | 能改 |
|---|---|
| 运营/操作人员（知识运营） | 仅 AI 客服对客 skill（反思工作台）+ 飞书 KB/FAQ |
| 管理员（流程编排） | classify / conflict_detect / dedup / hub_dedup / vision / escalation_classify / reflect + 人员分工 + 流程配置 |

`/admin/skills`（内部编排 skill）保持 `require_admin`；反思工作台 + KB 编辑对知识运营角色放行，且够不到内部 skill。

## 3. 流程图（入库主链）

```mermaid
flowchart TD
  WH["外部 webhook<br/>KSM / 智齿 / zammad / AI客服"] --> ING["Ingester 入库 Raw ticket"]
  ING --> V{"有截图?"}
  V -- 是 --> VIS["vision_extract<br/>OCR 补进 body"]
  V -- 否 --> TRI
  VIS --> TRI["triage：classify + conflict_detect<br/>定 type + 是否混合 + 子问题列表"]
  TRI --> MIX{"混合多问题?"}
  MIX -- 是 --> GATE["闸门：自动拆关闭时停摆<br/>进「待拆分」人工队列"]
  GATE --> SPLIT["split 物化原子子工单<br/>灰度：先人工后自动"]
  SPLIT --> ATOM["每个原子子单<br/>type 已定, 不再 conflict_detect"]
  MIX -- 否 --> ATOM
  ATOM --> TYPE{"type ?"}

  TYPE -- Operation --> OPG["毕业 hub_issue → hub_dedup"]
  OPG --> OPDUP{"命中现有?"}
  OPDUP -- 是 --> OPRE["挂靠 + 复用答复级联"]
  OPDUP -- 否 --> OPESC{"来自 AI 客服 escalation?"}
  OPESC -- 是 --> OPQ["进 reflect 反思队列（人工）"]
  OPESC -- 否 --> OPANS["直接答复客户"]

  TYPE -- Bug_fix --> BG["毕业 hub_issue → hub_dedup"]
  BG --> BDUP{"命中迭代中?"}
  BDUP -- 是 --> BTRK["跟踪排期/发版, 回写客户"]
  BDUP -- 否 --> BLIN["推 Linear"]

  TYPE -- Demand --> DG["毕业 hub_issue → hub_dedup"]
  DG --> DDUP{"命中?"}
  DDUP -- 是 --> DTRK["跟踪"]
  DDUP -- 否 --> DOWN{"单 / 多责任人?"}
  DOWN -- 单 --> DLIN["推 Linear（1 issue）"]
  DOWN -- 多 --> DSPLIT["owner-split（v1 主管手动分解）：<br/>1 hub_issue 挂 N 个 Linear 子 issue"]
  DSPLIT --> DREL["每个子 issue Done → 自动进度通知 x/n<br/>x&lt;n: progress_note 回复不关单(is_deal=false)<br/>x=n: release_note 答复关单"]

  TYPE -- Complaint投诉 --> CQ["停 ticket 层, 红色高亮人工队列"]
  CQ --> CACT{"人工"}
  CACT -- 关闭 --> CCL["手工关闭"]
  CACT -- 转hub_issue --> CCONV["指定 type（Op/Bug/Demand）→ 毕业"]

  TYPE -- Internal_task --> IN["内部处理"]
```

## 4. 时序图（入库 → agent → 分支）

```mermaid
sequenceDiagram
  autonumber
  participant EXT as 外部源
  participant WH as webhook
  participant BG as BG task chain
  participant LLM as 内部 agent (LLM)
  participant DB as DB
  participant LIN as Linear

  EXT->>WH: POST 工单
  WH->>DB: 写 Raw ticket
  WH->>BG: run_post_ingest_agents
  opt 有截图
    BG->>LLM: vision_extract OCR
    LLM->>DB: 回写 body（[附件识别]）
  end
  BG->>LLM: triage（classify + conflict_detect）
  LLM->>DB: predicted_type + 是否混合 + 子问题
  alt 混合
    BG->>DB: split 物化原子子工单
  end
  loop 每个原子工单（按 type 分流）
    BG->>DB: 毕业 hub_issue
    BG->>LLM: hub_dedup 查重（vs 既有 hub_issue）
    alt Operation 且 escalation 新失败
      BG->>DB: 入 reflect 队列（人工诊断）
    else Bug_fix / Demand 全新
      BG->>LIN: 推 Linear（Demand 多责任人 → 多子 issue）
    else 命中现有
      BG->>DB: 挂靠 + 复用（复用答复 / 跟踪迭代）
    end
  end
```

## 5. 反思闭环（Operation 病因诊断，人工触发，独立于入库链）

多病因**集合**（非单选）+ 修复清单，每病因一项，各自修各自验证，全绿才闭环：

```mermaid
flowchart TD
  Q["reflect 队列：Operation 新失败"] --> RF["escalation_reflect 三步排查<br/>→ 病因集合（可多选）+ 建议修订"]
  RF --> CL["修复清单：每病因一个待办项"]
  CL --> C1["病因 skill：改 AI 客服 skill draft<br/>→ replay 对比 → API 发布"]
  CL --> C2["病因 knowledge/FAQ：改飞书 KB/FAQ<br/>→ replay 对比 → API 发布"]
  CL --> C3["病因 retrieval：补知识条目<br/>→ replay 验证命中"]
  C1 --> DONE{"全部打勾 + 整体 replay 答对?"}
  C2 --> DONE
  C3 --> DONE
  DONE -- 是 --> CLOSE["escalation 工单闭环关闭"]
  DONE -- 否 --> CL
```

## 6. 后果与取舍

**正面**
- 互斥矛盾从架构消失（split 上游 + 原子单不可再拆），删掉半成品守卫。
- 类型定义单点、版本单轨，运营心智统一。
- 五类型各有明确终局，Operation 反思三病因都有落点。

**代价 / 待决**
- **发版通知 = 每个子 issue Done 即自动发进度通知**（已定）：文案带运行计数「您的需求含 n 个子任务，本次上线第 x 个，剩余 n-x 待处理」，x=n 时即全部完成。**永不等齐 → 无「差一个小功能卡住」；进度框架 → 不碎片化**。自动发只入 `sync_outbox`，真正对客发送受 KSM `dry_run` 灰度阀保护。
  - ⚠️ **关单语义（review 修正）**：现有 `release_note` 消费动作是 `handleKsmOrder(is_deal=True)` **答复并关单**——若进度通知直接复用，第 1 条通知就会关掉客户源工单。故新增 outbox kind **`progress_note`**：x<n 走 `is_deal=False` 只回复不关单（KSMClient 已支持）；**仅 x=n 最后一条走 `release_note` 关单**。
  - **owner-split v1 触发 = 主管手动**（已定）：研发协同页「按责任人拆分」，主管填 N 个子任务标题+责任人 → 系统建 N 个 Linear 子 issue。LLM 预拆建议留 v2。
- **triage 合并**（已定）耦合 classify 与 conflict_detect 到一个 prompt；换来省一次 LLM + 类型定义单点。若日后要独立调其一，再拆回两 prompt。
- **ticket 级 dedup 退役删除**（已定）：hub_dedup 成为唯一主查重。**注意级联**——`hub_dedup.py` 目前 `from ...dedup import cosine_similarity`，故删 `dedup.py` 前须先把 `cosine_similarity` 挪到共享位（如 `core/llm_router/embeddings.py` 或新 `core/vecmath.py`）。删除清单：`dedup.py` + `dedup_execute.py` + supervisor 三端点（dedup-proposals/execute-dedup/dismiss-dedup）+ 工作台「重复工单提案」卡 + `ticket_embeddings` 表 + `DEDUP_*` 配置。**保留**：`EmbeddingClient`、`hub_issues.embedding`（hub_dedup 自用自己的列，不碰 ticket_embeddings）。
- **owner-split 复杂度**：新增 `hub_issue_linear_issues` 子 issue 跟踪表 + 研发协同页里程碑 UI + 状态同步扩成一组。
- **灰度顺序**：split 自动化、hub_issue 自动毕业、Linear 推送、发版自动通知 均沿用既有「先审计/人工 → 后自动」剧本，逐项开关。

## 7. 迁移影响

| 改动 | 文件/表 | 迁移 |
|---|---|---|
| 链路重排（split 前置、triage 合并、按类型分流、混合单闸门队列） | `app/api/webhooks.py run_post_ingest_agents`、新 `services/agents/triage.py`、工作台「待拆分」队列 | 无（逻辑重排） |
| 加第 5 型 Complaint | triage prompt + **`escalation_classify.py` 的 `_VALID_TYPES`（代码，parser 会拒新类型）** + 前端 `PredictedTypeBadge` 等 5 处徽标消费点（建议 rose 深色高亮） | 无（predicted_type 无 CHECK） |
| 评测机制升级 | `routing_v1.jsonl` 为 4 类型标注；triage 输出 schema 变（type+混合+子问题），`make eval-routing` 需同步升级 | 无 |
| 投诉分诊：关闭 / 转 typed hub_issue | 复用 `create-hub-issue`（已支持 type 覆盖）+ 工作台投诉队列 | 无 |
| skill 三槽版本 + **draft 验证器** | `skill_prompts` 表 + `prompt_store.load_prompt`、6 个 agent 去 `_vN`；draft 提升 current 前**用评测集回放对比新旧命中率**（复用 eval 机制），满足「检测后再更新」 | **有**：三槽结构 + 归一 classify_v2→current |
| 类型定义共享 | 抽 `type_taxonomy` 片段，triage/escalation_classify 拼接 | 无 |
| classify/conflict_detect 退役 | P2 后被 triage 取代：skill 列表存档（previous 槽保留），文件移 `prompts/archive/` | 无 |
| owner-split 进度通知关单语义 | 新 outbox kind **`progress_note`**（is_deal=False 不关单）；`ck_sync_outbox_kind` 扩枚举；仅 x=n 走 release_note 关单 | **有**（CHECK 扩枚举） |
| 多病因修复清单 | `reflect` 结果改集合 + escalation 工单挂 checklist | 视存储方式，或 JSON 无迁移 |
| owner-split | 新 `hub_issue_linear_issues(hub_issue_id, linear_uuid, identifier, status, released_at)` + Linear 子 issue API + **每子 issue Done 自动进度通知** | **有** |
| **ticket 级 dedup 退役** | 挪 `cosine_similarity` → 共享；删 `dedup.py`/`dedup_execute.py` + 3 端点 + 工作台队列 `dup` 类型 chip + `DEDUP_*` 配置 + 掉 `ticket_embeddings` 表。**`agent_decisions` 的 CHECK 保留 `dedup_link/dedup_new` 枚举**（历史审计行仍在，勿删） | **有**（drop 表） |
| 权限双层 | 知识运营角色能力 vs `require_admin` | 无（能力对齐） |

## 8. 落地分期（建议）

1. **P0 补洞**：reflect prompt 入库、Skill 配置页「内部 vs 对客」认知澄清、reflect 队列加 Operation-only 过滤。
2. **P1 版本三槽 + draft 验证器**：内外统一 draft/current/previous，废 _v1/_v2；draft 提升前评测集回放对比（「检测后再更新」）。
3. **P2 链路重排**：triage 合并 + split 前置 + **混合单停摆闸门与「待拆分」队列** + 五类型分流 + Complaint 第 5 型（含 escalation_classify 代码与前端徽标）+ 类型定义共享 + 评测机制升级（本 ADR 地基，充分单测）。
4. **P3 反思闭环补全**：多病因修复清单 + KB/FAQ 调试（依赖飞书 KB/FAQ API）。
5. **P4 owner-split**：子 issue 跟踪表 + 主管手动分解 UI + **progress_note（不关单）/release_note（末条关单）双 kind 自动进度通知**。
6. **P5 权限双层**落地。
