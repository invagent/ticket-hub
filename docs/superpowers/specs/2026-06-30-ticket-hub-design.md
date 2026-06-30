# ticket-hub 需求设计文档

> 版本：2026-06-30 | 基于源码全面分析生成

---

## 一、项目概述

ticket-hub 是**多源工单聚合中台系统**，核心价值：

1. **统一接入**：聚合 KSM / 智齿 / Zammad / AI客服 工单
2. **AI 智能分类**：LLM 自动判断工单类型（4 种出口）
3. **自动路由**：按产品线/模块/功能分配给对应处理人
4. **去重合并**：客户身份合并 + 工单重复检测
5. **双向同步**：hub 状态/回复反向回写源系统（KSM 优先）
6. **Linear 对接**：Bug/需求自动推送研发平台
7. **SLA 监控**：超期告警 + 主管升级链

---

## 二、核心业务概念

| 概念 | 解释 |
|---|---|
| **Ticket（工单）** | 来自外部源系统的原始工单，三态：Raw / Parent / Child |
| **Hub Issue** | 若干 Tickets 聚合后的内部工单，4 个出口类型 |
| **Operation** | 客户咨询/投诉 → 人工撰写回复 → 回写源系统 |
| **Bug_fix** | 产品缺陷 → 推送 Linear → 跟踪版本发布 |
| **Demand** | 功能需求 → 推送 Linear → 排期迭代 |
| **Internal_task** | 内部任务 → 同步飞书任务 |
| **Split（拆单）** | 单工单包含多个问题 → LLM 建议拆分 → 人工确认 |
| **Dedup（去重）** | 多源重复工单 → 向量召回+LLM判定 → 合并到同一 Hub |
| **Relink（重关联）** | 主管手动将工单重新关联到正确的 Hub Issue |
| **Module Scope** | 产品线 × 模块 → 分配给指定处理人（最高优先级） |
| **Feature Scope** | 跨产品线的功能名称 → 分配给指定处理人（次优先） |
| **Default Pool** | 全局兜底处理人（最低优先级） |
| **SyncOutbox** | Hub 状态/回复变更的出站队列，异步消费回写源系统 |

---

## 三、数据模型

### 3.1 核心表关系

```
sources ─────────────────────────────┐
product_lines ──────────────────────┐│
                                    ││
customers ← customer_identities     ││
                ↕ (resolve)         ││
tickets ←──────────────────────────┘│
  ├── type: Raw / Parent / Child     │
  ├── source_code → sources ─────────┘
  ├── product_line_code → product_lines
  ├── assigned_user_id → users
  ├── hub_issue_id ──────────────────┐
  └── parent_ticket_id (Child only)  │
                                     │
hub_issues ←───────────────────────┘
  ├── type: Operation / Bug_fix / Demand / Internal_task
  ├── linear_uuid (Bug_fix / Demand)
  ├── feishu_task_id (Internal_task)
  └── superseded_by_hub_issue_id (重复时)

ticket_hub_issue_history  (关联变更审计)
hub_issue_reply_history   (回复版本历史)
status_history            (状态流转审计)
agent_decisions           (LLM 决策审计)
sync_outbox               (出站回写队列)
ticket_embeddings         (工单向量存储)
attachments               (附件 + Vision OCR)
notification_log          (SLA 超期 + 升级通知)
```

### 3.2 工单状态流转

```
Ticket 状态：
received → linked → waiting_reply → replied → done
                 ↘ split (拆单后 Parent)

HubIssue 状态：
created → pending (Linear推送阻塞)
       → in_progress (Linear开始处理)
       → waiting_reply (等客户补料)
       → released (Linear完成/发布)
       → done
```

### 3.3 关键字段说明

**Ticket 关键字段**：

| 字段 | 说明 |
|---|---|
| `source_code` | 来源（ksm / zhichi / zammad / ai_cs）|
| `source_ticket_id` | 源系统工单 ID（幂等键）|
| `type` | Raw（初始）/ Parent（拆后主）/ Child（子单）|
| `predicted_type` | LLM 预测类型 |
| `predicted_confidence` | 预测置信度（0~1）|
| `assigned_user_id` | 路由分配的处理人 |
| `hub_issue_id` | 关联的 Hub Issue |
| `cached_reply_content` | 来自 hub 的回复缓存 |
| `internal_split_id` | Child 专属：`{parent_short_code}-C{n}` |

**HubIssue 关键字段**：

| 字段 | 说明 |
|---|---|
| `short_code` | 内部编号 `HUB-000001` |
| `type` | 4 种出口类型 |
| `status` | created / pending / in_progress / released 等 |
| `linear_uuid` | Linear issue ID（推送成功后填入）|
| `linear_status` | 镜像 Linear 状态 |
| `reply_content` | Operation 类型的当前回复 |
| `reply_content_version` | 回复版本号 |
| `occurrence_count` | 关联工单数（dedup 合并后递增）|

---

## 四、系统架构

### 4.1 总体架构

```
外部来源                   接入层               Agent 处理链              外部平台
─────────              ──────────         ────────────────────        ──────────
KSM webhook ─────────→                   Vision Extract
智齿 webhook ─────────→  FastAPI          ↓                           Linear
Zammad webhook ───────→  (webhooks.py)   Classify                    ↑
AI客服 escalation ────→                  ↓                           linear_push
                         ↓              Auto Hub Issue              
                       Ingester          ↓                           KSM
                         ↓              Dedup                       ↑
                       Router            ↓                           KSM Writeback
                         ↓              Conflict Detect              (Celery beat)
                       Ticket            ↓
                       (DB)             Auto Split
                                         ↓
                                        Child Classify
```

### 4.2 技术栈

| 层次 | 技术 |
|---|---|
| API 框架 | FastAPI 0.115.14 + Pydantic v2 |
| 数据库 | PostgreSQL 16（psycopg3）|
| ORM | SQLAlchemy 2.x |
| 迁移 | Alembic |
| 异步任务 | Celery 5.x + Redis |
| LLM | DashScope（deepseek-v4-flash 分类 / qwen-vl-max Vision）|
| Embedding | DashScope text-embedding-v4 / GLM embedding-3 |
| 向量检索 | Python 应用层余弦（暂不用 pgvector）|
| 前端 | React 18 + TypeScript + Vite + TanStack Query + Tailwind |
| 部署 | Docker Compose + nginx（宿主机）|

---

## 五、业务流程

### 5.1 工单接入流程（以 KSM 为例）

```
1. KSM 轻量 ping → POST /webhook/ksm
   payload: {billId, noticeNum}
   → 立即 200 ack，不阻塞 KSM

2. BackgroundTask:
   a. fetch_order_detail(billId) → 完整工单数据
   b. 幂等检查：(source='ksm', source_ticket_id=billId) 已存在 → 跳过
   c. IdentityResolver：按优先级解析客户身份
      erp_uid > mobile > email > (source, source_custom_id) > 新建
   d. CatalogUpsert：确保 product_line / module 存在
   e. 创建 Ticket (type=Raw, status=received)
   f. Router.route() → 分配 assigned_user_id
   g. 写 status_history

3. 异步 Agent 链（BackgroundTask，不阻塞 webhook）：
   vision_extract → classify → (auto hub_issue) → dedup → conflict_detect → (auto split)
```

### 5.2 AI 分类 → Hub Issue 毕业流程

```
classify Agent：
  input:  ticket (title, body, product_line, module)
  output: {type, confidence, reason}
         → ticket.predicted_type / predicted_confidence

毕业条件（二选一）：
  自动：HUB_ISSUE_AUTO_ENABLED=true AND confidence ≥ HUB_ISSUE_AUTO_CONFIDENCE(0.80)
  手动：supervisor POST /api/supervisor/create-hub-issue

毕业操作：
  1. 创建 HubIssue (short_code=HUB-NNNN, status=created)
  2. 关联 ticket.hub_issue_id
  3. Bug_fix/Demand → linear_push（条件见 5.4）
```

### 5.3 工单去重流程

```
dedup Agent（每条 Raw 工单触发）：
  Step 1 - Embed：text-embedding-v4 → ticket_embeddings
  Step 2 - Recall：余弦相似度 ≥ 0.80，取 Top-5 候选
  Step 3 - LLM 判定：candidate 集内是否有重复
    有重复 → agent_decisions(dedup_link)
    无重复 → agent_decisions(dedup_new)，$0 LLM 成本

90天自动挂载（DEDUP_AUTO_MOUNT_ENABLED）：
  90天内相似工单 → 自动挂到已毕业的 hub_issue
  主管可 relink 纠偏

手动审核路径：
  supervisor GET /api/supervisor/dedup-proposals
  → [采纳合并] POST /api/supervisor/execute-dedup
     目标工单 hub_issue.occurrence_count++
  → [忽略] POST /api/supervisor/dismiss-dedup
```

### 5.4 Linear 推送流程

```
触发：Hub Issue type=Bug_fix 或 Demand 且 linear_push_enabled=true

前置检查：
  1. 处理人有邮箱 AND 邮箱能匹配 Linear 用户 → 推到对应 team
  2. 处理人无邮箱（组账号）→ 推到默认 team（无 assignee）
  3. 处理人有邮箱但 Linear 查无此人 → status=pending（阻塞）

Hub Dedup（推 Linear 前）：
  同产品线已推过类似问题 → supersede 到已有 issue，不新建

推送成功：
  hub.linear_uuid / linear_identifier ← Linear
  hub.status ← created

Linear 状态回同步（Celery beat 5min）：
  Linear "In Progress" → hub in_progress → tickets 级联
  Linear "Completed"   → hub released + actual_released_at
```

### 5.5 Operation 回复 → KSM 回写流程

```
supervisor POST /api/hub-issues/{id}/reply { content }

后端操作：
  1. hub_issue.reply_content = content（版本化）
  2. HubIssueReplyHistory 存快照
  3. 所有 linked_tickets.cached_reply_content 更新
  4. 源工单（有 source_code）→ SyncOutbox (kind='reply', status='pending')

KSM Writeback（Celery beat 2min，ENABLED+!DRY_RUN）：
  drain sync_outbox WHERE target_source_code='ksm' AND status='pending'
  → lock → refresh node → handleKsmOrder(is_deal=true)
  → outbox.status = 'sent'

灰度路径：
  KSM_WRITEBACK_ENABLED=false → 完全跳过
  KSM_WRITEBACK_DRY_RUN=true  → 组装请求但标 skipped，不真发
```

### 5.6 SLA 超期告警流程

```
Celery beat 扫描（间隔：约 5-10min）：
  对每条 active ticket / hub_issue：
    计算 age = now - received_at（工作日感知可选）
    超过阈值（product_line 级或全局默认）→
      NotificationLog (notify_type=sla_overdue)

升级链（Celery beat 10min）：
  已发通知 且 未 ack 且 超 ESCALATION_AFTER(2h) →
    查 recipient 的 supervisor_id / deputy_id
    → 创建新 NotificationLog 给 supervisor
    → 标记原通知 escalated_at

主管确认：POST /api/supervisor/notifications/{id}/ack
```

### 5.7 拆单流程

```
conflict_detect Agent（每条 Raw 工单触发）：
  input:  ticket (title, body)
  output: {decision: split/no_split, sub_issues: [{title, summary}]}
  原则：拿不准判 no_split（误拆代价 > 漏拆）

自动拆单（SPLIT_AUTO_ENABLED，默认关）：
  confidence ≥ SPLIT_AUTO_CONFIDENCE(0.85) → 直接物化

手动拆单路径：
  supervisor GET /api/supervisor/split-proposals
  → [执行拆分] POST /api/supervisor/execute-split
     Parent ticket: Raw → Parent, status=split
     子工单: Child (internal_split_id={parent}-C{n})
     每个 Child 重新路由 + 重新 classify（不再 conflict_detect）
  → [忽略] POST /api/supervisor/dismiss-split

撤销：POST /api/supervisor/revert-split
  条件：所有 Child status=received（未进行处理）
```

---

## 六、路由分配规则

```
优先级（由高到低）：

1. Module Scope（最优先）
   匹配：product_line_code × module → user_id
   多组匹配（multi_match）→ 触发 conflict_detect

2. Feature Scope（次优先）
   匹配：feature → user_id
   适用：跨产品线的同类问题（如"认证问题"）

3. Default Pool（兜底）
   system_settings.default_pool_user_id
   或 .env DEFAULT_POOL_USER_ID
   未配置 → config_warning: no_default_pool

Partner 机制：
  同一 UserPartner 对视为单个路由单元
  两人共同承担，路由不重复分配
```

---

## 七、权限模型

| 角色 | 英文值 | 权限范围 |
|---|---|---|
| 管理员 | admin | 全部功能 + 用户/分工/目录管理 |
| 主管 | supervisor | 主管工作台（dedup/split/relink/回复/补料）|
| 处理人 | assignee | 工单列表查看 + 详情只读 |
| 普通成员 | member | Dashboard + 客户搜索只读 |

**后端鉴权**：
- `require_admin()`：用户管理、分工配置、目录管理
- `require_supervisor()`：主管工作台所有操作、回复编辑
- `require_user()`：工单/客户查看（所有已认证用户）

---

## 八、前端页面功能

### 8.1 Dashboard（仪表盘）
- 4 个 KPI 指标卡片（每 30 秒自动刷新）：
  - 自动分配命中率 ≥95%
  - 主管调整率 <10%
  - 客户识别准度 ≥90%
  - SLA 确认率 ≥90%
- 24h Webhook 入流量（按来源分组）

### 8.2 主管工作台
5 类待处理卡片：
1. **配置警告**（黄色）：无兜底处理人 → 内联选择保存
2. **拆单提案**（紫色）：AI 建议拆分 → 执行 / 忽略
3. **Linear 推送待处理**（琥珀色）：pending hub → 重推 Linear
4. **重复工单提案**（青色）：AI 建议合并 → 采纳 / 忽略
5. **通知箱**：SLA 超期通知 → 确认 / Relink

### 8.3 跨源工单列表
- 过滤：来源 / 状态 / 仅未分配 / 分页
- 多选（主管专属）→ 重新触发分配
- AI 分类彩色徽章

### 8.4 Hub 工单列表
- 4 个 Tab（4 种出口类型）
- 类型专属列（Linear 状态 / 回复版本 / 飞书任务状态）

### 8.5 Hub 工单详情
- Operation：回复编辑 + 补料请求
- Bug_fix / Demand：Linear 状态 + 版本追踪
- 关联工单列表

### 8.6 用户管理
- 角色/状态筛选 + 搜索
- 从飞书同步（部门树 + 多选批量同步）
- 从 Linear 同步（邮箱匹配 + 填充 linear_team_id）

### 8.7 分工管理
- Module 分工 / Feature 兜底 / 全局兜底 / 变更审计 四个 Tab

### 8.8 Skill 配置
- DB 化提示词编辑（版本化 + 回滚）

---

## 九、外部集成

### 9.1 KSM（金蝶移动服务云）
- **入站**：轻量 ping + 后台 fetch 详情（避免超时）
- **出站**：lock → refresh → handle/supply（reply/supply/status 三种操作）
- **身份**：KSM_HANDLER_NAME + KSM_HANDLER_NUMBER
- **灰度**：KSM_WRITEBACK_ENABLED + KSM_WRITEBACK_DRY_RUN

### 9.2 Linear（研发管理）
- **推送**：Bug_fix / Demand → Linear issue（按处理人 team 路由）
- **同步**：Celery beat 5min 轮询状态回写
- **用户同步**：邮箱匹配 → linear_user_id + linear_team_id

### 9.3 飞书（Feishu）
- **SSO 登录**：OAuth2 code → JWT（7天 TTL）
- **用户同步**：部门树 + 批量同步员工信息
- **内部任务**：Internal_task 类型 hub → 飞书任务（待完整实现）

### 9.4 AI 客服（ai_cs）
- **升级入口**：POST /webhook/cs-escalation
- **黄金三元组**：原问题 + AI 答复 + 不满反馈 → 二次分类（置信度更高）

---

## 十、Celery 定时任务

| 任务 | 周期 | 功能 |
|---|---|---|
| `poll_linear_statuses` | 5min | Linear 状态回同步 |
| `drain_ksm_writeback` | 2min | KSM 出站回写 |
| `materialise_metrics` | 5min | 仪表盘指标物化 |
| `sla_watcher` | ~5-10min | SLA 超期扫描 |
| `escalation_worker` | 10min | 未确认通知升级 |

---

## 十一、灰度开关

| 开关 | 默认值 | 说明 |
|---|---|---|
| `LINEAR_PUSH_ENABLED` | true（生产） | Linear 推送总开关 |
| `HUB_ISSUE_AUTO_ENABLED` | false | 自动毕业 Hub Issue |
| `HUB_ISSUE_AUTO_CONFIDENCE` | 0.80 | 自动毕业置信门槛 |
| `DEDUP_ENABLED` | true | 去重 Agent（仅审计）|
| `DEDUP_AUTO_MOUNT_ENABLED` | false | 90天自动挂载 |
| `CONFLICT_DETECT_ENABLED` | true | 拆单检测（仅审计）|
| `SPLIT_AUTO_ENABLED` | false | 自动拆单 |
| `SPLIT_AUTO_CONFIDENCE` | 0.85 | 自动拆单置信门槛 |
| `VISION_ENABLED` | false | Vision OCR |
| `ESCALATION_AUTO_ENABLED` | false | AI客服升级自动毕业 |
| `KSM_WRITEBACK_ENABLED` | false | KSM 回写总开关 |
| `KSM_WRITEBACK_DRY_RUN` | true | 演习模式（不真发）|
| `SLA_WORKDAY_AWARE` | false | 工作日感知 SLA |

---

## 十二、技术债与待办

| 项 | 状态 | 说明 |
|---|---|---|
| PII 加密（AES-GCM）| 降级 | 全走国内 LLM，暂不需要 |
| pgvector 迁移 | 冻结 | 余弦暴力扫描够用，性能热点再迁 |
| 智齿回写 | 待做 | outbox 已入队但 sender 未实现 |
| 飞书任务同步 | 待做 | Internal_task 飞书侧未完整实现 |
| AI 客服适配层 | 待做 | webhook 载荷格式待确认 |
| dedup 评测集 | 待做 | 需积累真实重复数据后建立 |
| Linear webhook | 待评估 | 当前轮询，实时需求再升级 |

---

*文档由源码分析自动生成，2026-06-30*
