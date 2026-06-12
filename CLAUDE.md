# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

ticket-hub 是跨源工单枢纽，聚合 KSM / 智齿 / zammad / Linear 的工单，通过 Agent 自动分类、路由、去重，主管事后修正。当前处于 D3 阶段（Agent 全家桶），大幅领先计划进度。

## 仓库结构

Monorepo，三个独立子栈：

- `backend/` — FastAPI + SQLAlchemy + Alembic + Celery（Python 3.11+）
- `frontend/` — Vite + React 18 + TypeScript + Tailwind + TanStack Query
- `cli/` — Typer CLI（OpenAPI-driven）
- `scripts/` — 对账、评测、迁移、压测脚本（Python）
- `docs/adr/` — 架构决策记录（已采纳：0001/0002/0005/0012）
- `docs/spec/` — data_model / api / routing 三份规格草案

## 常用命令

### Backend（在 `backend/` 目录下）

```bash
make install          # 创建 .venv 并安装所有依赖（含 dev）
make lint             # ruff check + ruff format --check + mypy
make unit             # 单测，覆盖率门槛 ≥70%
make pii-cov          # PII 模块单测，覆盖率门槛 ≥95%
make integration      # 集成测试（需要 Docker）
make eval-routing     # D1 路由回放评测（需要 routing_v1.jsonl）
make cov              # 生成 HTML 覆盖率报告（htmlcov/index.html）
make clean            # 删除 .venv、缓存、覆盖率文件

# 运行单个测试文件
.venv/bin/pytest tests/unit/core/pii/test_sanitizer.py -v

# 启动开发服务器
.venv/bin/uvicorn app.main:app --reload --port 8080
```

### Frontend（在 `frontend/` 目录下）

```bash
npm install
npm run dev           # 开发服务器 http://localhost:5173
npm run build         # tsc + vite build
npm run type-check    # tsc --noEmit
npm run test          # vitest run
npm run test:watch    # vitest watch 模式
npm run lint          # eslint
npm run gen:api       # 从 openapi.json 生成 types.ts
npm run gen:api:live  # 从运行中的后端（:8080）生成 types.ts
```

### 根目录（全栈）

```bash
make test             # backend lint+unit+pii-cov + frontend type-check+test
make gen-types        # 重新生成 frontend/src/api/openapi.json + types.ts
make check-types      # CI 门槛：检查 openapi.json 和 types.ts 是否与后端同步
make eval-routing     # D1 路由回放
```

### 本地依赖

```bash
docker compose up -d pg redis minio   # PG16+pgvector / Redis7 / MinIO
cd backend && .venv/bin/alembic upgrade head   # 应用数据库迁移
```

## 架构要点

### 数据流

```
外部 webhook (KSM/智齿/zammad)
  → POST /webhook/{source}
  → Ingester（services/ingest/）解析 raw payload
  → 写入 tickets 表（type='Raw'）
  → BackgroundTask 触发 classify Agent
  → GLM LLM 分类 → 写回 tickets.predicted_* + agent_decisions 表
  → 路由 Router（services/routing/）→ 分配 assigned_user_id
```

### 核心模型关系

- `tickets`（Raw/Parent/Child 三类型单表）→ 关联 `hub_issues`（4 出口类型：Operation/Bug_fix/Demand/Internal_task）
- `customers` ← `customer_identities`（多源身份图谱，erp_uid/mobile/email 解析）
- `assignment_scopes_module`（产品线+模块 → 用户）+ `assignment_scopes_feature`（跨产品线兜底）
- `agent_decisions`（所有 Agent 决策审计表，supervisor 可 revert）
- PK 全部用 INT autoincrement（非 UUID，见 ADR-0002）
- JSON 字段用 `JSON` 类型（PG JSONB / SQLite 兼容）

### Backend 分层

```
app/api/          路由层（FastAPI routers）
app/services/     业务逻辑
  agents/         LLM Agent（classify、后续 conflict_detect、dedup）
  identity/       客户身份解析
  ingest/         各源 webhook 解析器
  routing/        工单路由
  sla/            SLA 监控 + 升级链
  supervisor/     主管修正
  metrics/        仪表盘指标（Celery 物化）
app/repositories/ 数据访问层
app/core/
  pii/            PII 脱敏/还原（strict mypy，≥95% 覆盖率硬门槛）
  llm_router/     LLM Provider 抽象（当前仅 GLM，D3-B）
  trace/          trace_id 中间件
  logging/        structlog + trace_id
app/models.py     所有 ORM 模型（单文件，按阶段分区注释）
app/db.py         engine + session（StaticPool for SQLite in tests）
```

### Frontend 类型同步

前端 API 类型从后端 OpenAPI schema 自动生成：`frontend/src/api/types.ts`。修改后端 API 后必须运行 `make gen-types` 并提交，否则 CI `make check-types` 会失败。

### 测试分层

- `tests/unit/` — 默认运行，SQLite in-memory（StaticPool），不需要 Docker
- `tests/integration/` — 需要 Docker（testcontainers），标记 `@pytest.mark.integration`
- `tests/e2e/` — 需要真实 UAT 凭证，标记 `@pytest.mark.e2e`
- `tests/eval/` — 需要 LLM Provider key，标记 `@pytest.mark.eval`

默认 `pytest` 只跑 unit（`pyproject.toml` 中 `-m "not integration and not e2e and not eval"`）。

### LLM Router

`app/core/llm_router/router.py` 抽象多 Provider，当前只实现 GLM（`providers/glm.py`）。新增 Provider 约 80 行，实现 `BaseLLMProvider` 接口。接入 OpenAI/Anthropic 等外部 LLM 前必须先补 PII 脱敏（`app/core/pii/` 的 AES-GCM encryptor 目前是 Protocol 占位）。

## 前端 Auth Guard

`frontend/src/main.tsx` 中 `RequireAuth` 组件保护所有非登录路由，未登录或 token 过期自动跳转 `/login`（解析 JWT `exp` 字段判断）。auth token 存储在 `localStorage.auth_token`，飞书 SSO 回调后由 `consumeSsoFragment()` 写入。

`frontend/src/api/client.ts` 中所有 API 请求收到 401 响应时，自动清除 localStorage 并跳转 `/login`。

JWT TTL 为 7 天（`backend/app/config.py` 中 `jwt_ttl_seconds = 60 * 60 * 24 * 7`）。

## 服务器部署

详见 `CLAUDE.local.md`（不提交 git）。生产服务器 `123.57.100.193`，nginx 配置在 `/etc/nginx/sites-enabled/reverse-proxy`。

- shaobin 原版：端口 9093，路径 `/ticket-hub/`，DB `ticket_hub`
- panda_li v2：端口 9094，路径 `/ticket-hub-v2/`，DB `ticket_hub_v2`，Python 3.12

前端 build 需指定环境变量：
```bash
VITE_PUBLIC_BASE=/ticket-hub-v2/ VITE_API_BASE=/ticket-hub-v2 npm run build
```

## 当前技术债（2026-06-12 更新）

完整清单见 **`docs/progress/2026-06-12-plan.md` §四**（含冻结项说明）。要点：

- PII encryptor 未实现（D4 第 3 段 Vision 接外部多模态 LLM 前必须补，硬门槛）
- dedup 评测未跑（dataset_v1 已有 `expected_dedup` 标注，待补 eval）
- ADR 0013(llm_router) / 0014(agent_decisions) / 0015(JSON 向量代替 pgvector) 待补记
- 16 条 `needs_review` 评测标签：❄️ 冻结 — 分类边界规则将来走人工配置 skill，不再改标签
- ~~`HANDOFF.md` 过时~~ ✅ 已重写为指针（2026-06-12）

## 工作计划

**以 `docs/progress/2026-06-12-plan.md` 为准**（2026-06-12 重排）。摘要：第 1 段 Linear 状态回同步 + 主管运营 UI（pending 队列/dedup 卡片/重推按钮）→ 第 2 段 cascade 双向同步（reply_sync + status_cascade + hub-issues 分视图）→ 第 3 段 How-To RAG + Vision 多模态（前置 PII encryptor）→ D5/D6 原内容时间前移。

## 飞书工号同步说明（2026-05-12）

- 飞书 `/authen/v1/user_info`（SSO 登录接口）**不返回 `employee_no`**，这是飞书接口本身限制
- 工号只能通过「从飞书同步」（`/contact/v3/users/find_by_department`）批量补全
- 需要在飞书开放平台开通 `contact:user.employee_number:read` 权限
- `feishu_sso.py` 的 `upsert_user` 更新分支已修复，统一同步 name/email/mobile/employee_no 四个字段

## 用户角色说明（2026-05-12）

系统有四个角色，前端统一显示中文名：

| 英文值 | 中文名 | 职责 |
|--------|--------|------|
| `member` | 普通成员 | 可查看工单和仪表板，无管理权限 |
| `assignee` | 处理人 | 可查看工单，被分配处理工单 |
| `supervisor` | 主管 | 可使用主管工作台、修正 Agent 决策、重新关联工单 |
| `admin` | 管理员 | 拥有全部权限，含用户管理、分工配置、目录管理 |

权限校验在 `backend/app/api/deps/auth.py`：`require_admin()`、`require_supervisor()`、`require_user()`。

## 飞书同步对话框（2026-05-12）

- 对话框打开后自动分批并发（每批 5 个）预加载所有部门成员，左侧树顶部显示进度条
- 树节点支持 checkbox 勾选，递归选中子部门所有可同步成员，支持三态（未选/半选/全选）
- 未加载完的节点 checkbox 禁用，加载失败的节点持续禁用不阻塞其他节点

## 工单入库自动 upsert 产品线/模块（2026-05-12）

- 工单入库时，若 `product_line_code` 或 `module` 不在 `product_lines`/`modules` 表，自动创建（`catalog_upsert.py`）
- 使用 `INSERT ... ON CONFLICT DO NOTHING`，并发安全，不需要手动维护种子数据
- 新创建的产品线/模块无处理人，路由落 `default_pool`
- 三个 Ingester（KSM/Zhichi/Zammad）均已接入，在 dedup 检查之后、Ticket 构造之前调用

## 主管工作台配置警告（2026-05-12）

- `GET /api/supervisor/config-warnings` 返回系统配置问题列表（require_supervisor）
- 检查项1：有 module 但 `assignment_scopes_module` 无处理人 → 提示去「管理后台 → 分工配置」
- 检查项2：未配置 `DEFAULT_POOL_USER_ID` → 提示联系运维设置 `.env`
- 前端主管工作台顶部显示黄色警告 Banner（可折叠）

## 重新触发分配（2026-05-13）

- `POST /api/supervisor/reroute`（require_supervisor）：对 1-50 条工单重新执行路由
- 复用现有 `Router` 逻辑，写 `status_history` 审计（changed_by="system:reroute"）
- 路由仍无匹配时返回 `no_match` 提示，不报错
- 前端工单列表页新增：「仅未分配」筛选、checkbox 多选（仅主管/管理员）、底部浮动操作栏、结果弹窗

## `sources` 表种子数据（2026-05-13 已自动化）

`sources` 种子数据已内置到 `0001_d0_initial` 迁移中（`ON CONFLICT DO NOTHING`），`alembic upgrade head` 后自动写入，无需手动操作。

## 兜底处理人配置（2026-05-13，入口更新 2026-05-14）

- 兜底处理人现在可在主管工作台直接配置，无需修改 `.env` 或重启服务
- 配置存储在 `system_settings` 表（`key='default_pool_user_id'`），立即生效
- 读取优先级：数据库 > `.env` `DEFAULT_POOL_USER_ID` > NULL
- API：`GET/PUT /api/admin/settings/default-pool-user`（require_supervisor）
- 主管工作台 `no_default_pool` 警告 Banner 内联用户下拉选择器，保存后 Banner 消失
- **分工配置页（`/admin/scopes`）新增「全局兜底」标签页**（2026-05-14）：固定入口查看/修改/清除兜底处理人，标签顺序：Module 分工 → Feature 兜底 → 全局兜底 → 变更审计
- 前端组件：`frontend/src/pages/admin/scopes/DefaultPoolTab.tsx`
- 数据库迁移：`0002_system_settings.py` + `0008_merge_system_settings.py`（合并迁移）
- `GET /api/admin/users` 权限为 `require_supervisor`（非 require_admin），主管可获取用户列表用于下拉选择
- 前端 `SupervisorPage.tsx` 中用户列表解析直接用数组（`users.data as UserOut[]`），不是 `{ users: [] }` 对象

## 用户管理状态筛选与启用（2026-05-13）

- 用户列表页新增状态筛选下拉（在岗 / 已停用 / 全部状态），默认显示"在岗"
- `GET /api/admin/users` 新增 `include_inactive: bool = False` 参数，切到"已停用"或"全部"时前端传 `include_inactive=true`
- 已停用用户行显示绿色"启用"按钮，调用 `POST /api/admin/users/{user_id}/revive` 恢复
- `UserRepository` 新增 `revive()` 方法（清除 `deleted_at`，设 `is_active=True`）
- 注意：前端路径参数替换必须用 `postByPath`，不能用 `api.post`（后者不替换 `{user_id}`）

## 阶段进度

D0✅ D1✅ D2✅ D3✅（A/B/C/D/E 全部完成，2026-06-12）D4🟡（hub_issue 创建 + Linear push 已实现；Linear 状态回同步待开工）D5~收尾⬜。当前分支：`main`。

## D3-D conflict_detect Agent（2026-06-11）

- `services/agents/conflict_detect.py`：判断 Raw 工单是否混合多个独立问题需要拆分
- webhook ingest 后经 `run_post_ingest_agents`（webhooks.py）依次跑 classify + conflict_detect，单 BG task
- **仅写 `agent_decisions` 审计行**（`decision_type='split_ticket'/'no_split'`，sub_issues 在 proposal 里），不改工单
- 开关 `CONFLICT_DETECT_ENABLED`（默认 true）；prompt 版本 `CONFLICT_DETECT_PROMPT_VERSION`（默认 v1，`prompts/conflict_detect_v1.md`）
- 判定原则：拿不准默认 no_split（误拆代价 > 漏拆）；split 时 sub_issues ≥2 否则解析报错
- 单测注意：`tests/conftest.py` 显式清空 GLM/DASHSCOPE key，防止本地 `.env` 真实 key 让 BG task 发起真实 LLM 调用

## D3-D split 执行器（2026-06-11）

- `services/agents/split.py`：把 `split_ticket` 提案物化为 Child 工单，**全程无 LLM**（语义拆分 LLM 在 conflict_detect 已完成，此处纯机械物化 + 规则重路由）
- Child 契约（`ck_tickets_type_fields`）：`source_code/source_ticket_id=NULL`、`internal_split_id='{parent.short_code}-C{n}'`（确定性+unique）、`parent_ticket_id` 必填；title/body 来自 LLM 的 sub_issue（**不切原文**，原文留在 Parent）；customer/product_line/module/reporter 继承
- Parent 翻转：type Raw→Parent、status→'split'、`children_ticket_ids` 落 JSON；幂等卫语句 `parent.type=='Raw'`
- 每个 child 重新走 Router（纯规则）各自分配；之后重跑 classify，**绝不**再 conflict_detect（防递归拆分）
- 触发：conf ≥ `SPLIT_AUTO_CONFIDENCE`(0.85) 且 `SPLIT_AUTO_ENABLED`（**默认 false，先灰度手动**）→ ingest 链自动；否则留给主管 `POST /api/supervisor/execute-split`
- 回滚 `POST /api/supervisor/revert-split`：软删 children + Parent 还原 Raw + decision 翻 reverted；**任一 child status ≠ received 则拒绝**（有进展不可自动回滚）
- 物化审计写回 `decision.proposal.materialized`（at/by/child_ids/parent_prev_status）
- 注意：Router 的 `multi_match`（一个问题多团队认领）是归属歧义，**不是**拆分场景，split.py 只消费 `split_ticket`

## 主管工作台拆单提案 UI（2026-06-12）

- `GET /api/supervisor/split-proposals`：待处理提案队列（未物化、未 reverted、parent 仍 Raw；materialized 过滤在 Python 做，JSON 谓词不值得跨库写）
- `POST /api/supervisor/dismiss-split`：主管忽略提案 → decision 翻 `reverted`（留审计）；已物化的拒绝（409，提示走 revert-split）
- 前端 `SupervisorPage.tsx` 新增 `SplitProposalCard`：紫色卡片显示 short_code/置信度/理由/子单列表 + 「执行拆分」「忽略」按钮

## D3-E dedup Agent（2026-06-12）

- `services/agents/dedup.py`：跨源重复工单判定。链路：embedding 入库 → 余弦召回 → 候选≥阈值才送 LLM 判定 → 写 `agent_decisions`（`dedup_link`/`dedup_new`），**仅审计不动工单**（同 split 灰度剧本：先审计、再手动、后自动）
- **刻意不用 pgvector**：向量存 `ticket_embeddings` JSON 列（迁移 0009），Python 余弦暴力扫最近 `DEDUP_CANDIDATE_POOL`(200) 条。当前量级（百级/天）足够；扫描进 profile 热点再迁 pgvector
- embedding：`app/core/llm_router/embeddings.py`，DashScope `text-embedding-v4` / GLM `embedding-3`（同 OpenAI `/embeddings` 方言，一个 httpx 客户端 + failover，顺序同 `LLM_PROVIDER_ORDER`）
- 召回参数：`DEDUP_RECALL_THRESHOLD`(0.80) / `DEDUP_RECALL_TOP_K`(5)；无候选 → 直接写 `dedup_new`（`method='recall_only'`，零 LLM 成本）
- LLM 判定约束：`duplicate_of_ticket_id` 必须在候选集内，否则解析报错丢弃；拿不准判 new（误合并代价 > 漏判）；prompt `prompts/dedup_v1.md`
- 只对 Raw 工单跑（Child 是拆分内部产物，不做 dedup 目标也不做 dedup 主体）
- ingest 链顺序（webhooks.py `run_post_ingest_agents`）：classify → (auto hub_issue) → dedup → conflict_detect → (auto split → 每个 child 再 classify)

## D4 hub_issue 创建 + Linear push（2026-06-12）

- `services/hub_issues/creator.py`：`ensure_hub_issue_for_ticket` 把已分类工单「毕业」成 hub_issue（短码 `HUB-{n:06d}`，status='created'，继承 title/body/产品线/module/处理人），写 `ticket_hub_issue_history`（user: 前缀 → human_confirmed=true）+ status_history；幂等（已链接直接返回 created=false）；split Parent 拒绝（children 各自毕业）
- 自动路径：classify conf ≥ `HUB_ISSUE_AUTO_CONFIDENCE`(0.80) 且 `HUB_ISSUE_AUTO_ENABLED`（**默认 false**）→ ingest 链自动建；手动 `POST /api/supervisor/create-hub-issue`（无置信门槛，可 `type` 覆盖 predicted_type）
- `services/hub_issues/linear_push.py`：Bug_fix/Demand 推 Linear（`LINEAR_PUSH_ENABLED` 默认 false + key/team 三门槛），回写 `linear_uuid/linear_identifier/linear_status_synced_at`；幂等（linear_uuid 非空跳过）；失败吞错留 NULL 可重推；priority 映射 critical→1…lowest→4；description 附 source tickets 引用
- 待开工：Linear 状态回同步（webhook /linear 或轮询）、Operation 回复流

## AI 分类结果展示（2026-05-13）

- `TicketSummary` 新增 `predicted_type`、`predicted_confidence`、`classified_at`、`assigned_user_name` 四个字段
- `list_tickets` 接口批量查询 `assigned_user_name`（一次额外 IN 查询，不影响性能）
- 工单列表页新增「AI 分类」列，显示彩色标签（Bug 修复=红、需求=蓝、运营=黄、内部任务=灰），未分类显示「未分类」
- 工单列表页「分配」列改为显示用户名，找不到时降级显示 `#ID`
- 工单详情页基本信息区新增「AI 分类」（标签+置信度百分比）和「分类时间」字段
- `PredictedTypeBadge` 组件定义在 `TicketDetailPage.tsx`，列表页 import 复用

## KSM 客户信息字段映射（2026-05-14）

`ksm_payload.py` 中客户信息取自 KSM `subscribeCallback` 响应的顶层字段（非 `customerInfo`）：

| 系统字段 | KSM 字段 | 说明 |
|---------|---------|------|
| `accountName`（姓名） | `feedbackUser` | 反馈人姓名 |
| `email`（邮箱） | `feedbackEmail` | 反馈人邮箱 |
| `mobile`（联系手机） | `feedbackPhone` | 反馈人手机 |
| `tel`（联系电话） | `feedbackTel` | 反馈人电话 |
| `account` / `erpUid` | `customerInfo.customerNumber` | 客户编号（仍取自 customerInfo）|

## Linear Adapter（2026-05-15）

- `adapters/linear/` 已实现，提供 `LinearClient.create_issue()` 方法
- 使用 Linear GraphQL API（`POST https://api.linear.app/graphql`）
- `CreateIssueRequest`：title / team_id / description / label_ids / assignee_id / priority
- `CreatedIssue`：id（UUID）/ identifier（如 ENG-42）/ url / title
- 配置项：`LINEAR_API_KEY` + `LINEAR_TEAM_ID`（写入 `backend/.env`，**待 hub_issue 自动创建完成后再配置部署**）
- 触发时机：hub_issue 创建且 type ∈ Bug_fix / Demand 时异步推 Linear，回写 `linear_uuid` / `linear_identifier`（见 D4 hub_issue 段）
- **鉴权坑（2026-06-12 生产首推暴露）**：Linear 个人 API key（`lin_api_` 前缀）的 `Authorization` 头要放**原始 key，不能带 `Bearer` 前缀**（带了报 HTTP 400）；OAuth token 才用 Bearer。`_headers()` 按前缀判断

## Linear 按处理人 team 路由 + 用户同步（2026-06-12）

- **目标**：Bug_fix/Demand issue 落到「被分配处理人所属的 Linear team」，而非固定一个 team
- `User.linear_team_id`（迁移 0010）：被分配时 issue 进哪个 team，由邮箱同步填充
- `LinearClient.list_users()`：分页拉活跃成员 + team 归属（**页大小 50**，250 会触发 Linear「Query too complex」400）
- `services/linear/user_sync.py` `sync_linear_users()`：按 `@email` 不区分大小写匹配 ticket-hub 用户 → 填 `linear_user_id` + `linear_team_id`
  - team 取值：单 team 直接用；多 team 优先默认 `LINEAR_TEAM_ID`，否则留空（→ 推送回落默认）；成员离开 Linear 清陈旧映射
  - **组账号**（数电开票组…）无邮箱 → 不匹配 → 两字段留空 → 推送回落默认 team 且无 assignee（刻意的优雅降级）
- `POST /api/admin/users/sync-from-linear`（require_admin）：触发同步，返回匹配报告
- `linear_push.py` 按 `assignee.linear_team_id` 路由，回落 `settings.linear_team_id`
- **生产现状（2026-06-12 已配置部署）**：`LINEAR_PUSH_ENABLED=true`，默认 team=CNPRD（中国区产品部，id `8abb86a2…`）；首轮同步 21 个个人映射（INTPRD 9 / CNPRD 5 / ARALGO 5 / KNOPS 2），9 个组账号跳过；实测分配给覃强(ARALGO)的工单落到 ARALGO-36 ✅
- API key：Linear 个人 key「ticket-hub push (shaobin prod)」，权限 Read + Create issues
- 单测：`test_linear_client.py`(13) / `test_linear_user_sync.py`(8) / `test_linear_push.py` 路由用例 / `test_admin_users.py` sync 端点

## Linear 状态回同步（2026-06-12，D4 第①段）

- `services/hub_issues/linear_status_sync.py`：Celery beat 5min 轮询已推送 hub_issue（最近 200 条），`LinearClient.get_issue_states()` 批量查（**50/批**防复杂度超限）
- 双层回写：`linear_status` 始终镜像 Linear 列名（展示层）；hub 状态只做**保守级联** `started→in_progress`、`completed→released`(+actual_released_at)
- `canceled` 只镜像不动状态（研发取消需主管判断）；**reopen 跟随**（released→in_progress，Linear 是研发态源头）；Linear 侧删除的 issue 只计数不动数据
- 状态变更写 status_history（`agent:linear_status_sync`）；无变化不写（幂等）
- beat 任务 `poll_linear_statuses_every_5min`（key 未配自动跳过）；生产已部署，实测 CNPRD-809 Backlog 正确镜像 ✅
- 升级路径：量大或要求实时再加 `/webhook/linear`，回写层不用改

## Linear 推送 pending 待人工（2026-06-12）

- **个人处理人（有邮箱）在 Linear 查无此人** → 不推送，hub_issue `status='pending'` + status_history 记原因（含邮箱）；**组账号（无邮箱）不受影响**，仍优雅降级推默认 team
- **Linear API 推送失败**（网络/鉴权/业务错）→ 同样置 pending + 错误原文
- 重试仍失败不重复写 history（pending 幂等）；`linear_uuid` 始终留 NULL 可重推
- **修复路径**：人加入 Linear 工作区 → `POST /api/admin/users/sync-from-linear` 补映射 → 重推成功自动 `pending→created`（留审计「pending 解除」）
- 生产实测：分配给 minjun_gong@kingdee.com（查无此人）→ 正确置 pending 不产生垃圾 issue ✅
