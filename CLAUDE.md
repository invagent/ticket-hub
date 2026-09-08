# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

ticket-hub 是跨源工单枢纽，聚合 KSM / 智齿 / zammad / AI客服 / 飞书AI 的工单，用 LLM 做分类、派单、模块归类、去重、自动答复，转研发出口推飞书 webhook / Linear。

**当前形态（2026-08 起）= AI 全自动链路 + 三道可开关人工闸门**，而非早期的「全自动 + 事后修正」。三道闸门各自独立开关，SIT 默认全开（「现阶段人在中枢」）：

| 闸门 | 开关 | 作用 |
|---|---|---|
| ① 分类确认 | `gate_classify_enabled`（未显式设置回落 `require_review_before_linear`） | 全类型毕业后停 `pending_review`，待人确认分类才继续分流 |
| ② 答复确认 | `operation_answer_accuracy_mode`（`off/observe/enforce/review`） | Operation 自动答复按准确率打分决定直发 / 转 `reviewing` 人工审 |
| ③ 推研发确认 | `gate_linear_push_enabled`（默认 True） | 研发类确认分类后停 `pending_linear_review`，待人确认（可改选 assignee）才推 |

## 仓库结构

Monorepo，三个独立子栈：

- `backend/` — FastAPI + SQLAlchemy + Alembic + Celery（Python 3.11+）
- `frontend/` — Vite + React 18 + TypeScript + Tailwind + TanStack Query
- `cli/` — Typer CLI（OpenAPI-driven）
- `scripts/` — 对账、评测、迁移、压测、种子脚本（Python；**不进 docker 镜像**，镜像只 bake `backend/`，SIT 要跑得 `docker cp` 进容器）
- `docs/adr/` — 架构决策记录（已采纳：0001/0002/0005/0012/0013/0014/0015/0016）
- `docs/spec/` — data_model / api / routing 规格
- `docs/superpowers/plans/` — **每个功能的实施计划（2026-07~08 新增功能的权威设计意图都在这里，比代码注释更完整）**
- `docs/manual/operation-manual.md` — 产品操作手册（面向使用者）
- `docs/memory/` — 另一位协作者的项目记忆（部署/踩坑/智齿接入）

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
外部 webhook (KSM/智齿/zammad/ai_cs/feishu_ai)
  → POST /webhook/{source}
  → Ingester（services/ingest/）解析 raw payload → 写入 tickets（type='Raw'）
  → 【入库阶段·同步】dispatch_handler 派单选处理人（写 handler_user_id）
                     + KSM 来源可选 takeover 抢占受理（lock→重拉→handle）
  → BackgroundTask run_post_ingest_agents:
      vision_extract(截图OCR)
      → triage（分类 + 是否混合，合一单次 LLM）
      → module_resolve（AI 判产品线/模块，覆盖生效值为目录内规范值）
      → 混合单：split_auto 开且过门槛 → 拆子单各自分流；关 → 停摆进「待拆分」队列
      → 非混合：按类型分流 → 毕业 hub_issue
  → 【闸门①】gate_classify_enabled 开 → 全类型停 pending_review，不自动分流
             关 → Operation 进自动答复链 / 研发类走闸门③ / Complaint 永远停 ticket 层
  → 【闸门②】Operation：Celery drain 扫 status='created' + op_status=processing + op_handler='agent'
             → ai_cs replay 生成答复 → answer_router 判 D/C/transfer + 准确率打分
             → 可发则 author_reply 级联回写客户；否则转 reviewing 人工审
  → 【闸门③】研发类：确认分类后停 pending_linear_review → 人确认（默认 assignee=模块研发责任人，
             可改选）→ 推飞书 webhook（linear_webhook_enabled 默认 True）或直连 Linear
```

关键点：
- **派单前移到入库阶段**（早于 triage/module_resolve），所以派单规则的「适配产品线/模块」两个维度已停用，只按来源+SLA 匹配。
- **责任人 vs 处理人是两个字段**：`assigned_user_id`（入库责任人，Router 算的）≠ `handler_user_id`（派单引擎写的实际处理人）。派单**只写 handler，绝不覆盖 assigned**。
- Operation 自动答复**不在 ingest 热路径**上（replay 慢约 138s/单会阻塞 worker），改由 Celery beat 每 2min drain，兼作补偿重试。
- `ai_cs` 来源走 `run_escalation_agents`（黄金三元组二次分类）；`feishu_ai` 来源请求形状与之完全相同但走标准 `run_post_ingest_agents`，三元组仅存档不参与分类。

### 核心模型关系

- `tickets`（Raw/Parent/Child 三类型单表）→ 关联 `hub_issues`（4 出口类型：Operation/Bug_fix/Demand/Internal_task）
- `customers` ← `customer_identities`（多源身份图谱，erp_uid/mobile/email 解析）
- `assignment_scopes_module`（产品线+模块 → 用户）+ `assignment_scopes_feature`（跨产品线兜底）
- `agent_decisions`（所有 Agent 决策审计表，supervisor 可 revert）
- `sync_outbox`（出站写队列，kind：`reply`/`status`/`supply`/`release_note`/`progress_note`/`return`）
- `dispatch_rules` + `dispatch_assignees` + `dispatch_config` + `dispatch_log`（运营派单引擎，迁移 0029/0032/0041）
- `attachments`（vision_status：`pending`=escalation 走 ingest 链 / `queued`=KSM 走异步流水线）
- `hub_issue_linear_issues`（owner-split 子 issue 跟踪）、`modules.dev_owners`（研发责任人轮询）
- PK 全部用 INT autoincrement（非 UUID，见 ADR-0002）
- JSON 字段用 `JSON` 类型（PG JSONB / SQLite 兼容）
- **`hub_issues.status` 和 `op_status` 都是无 CHECK 的 String 列** → 加新状态不需要迁移

### Backend 分层

```
app/api/          路由层（FastAPI routers）
  history_labels.py / ksm_nodes.py  ← 不是 router，是展示适配纯函数模块
app/services/     业务逻辑
  agents/         LLM Agent：triage / classify(子单兜底) / module_classify /
                  module_resolve / operation_answer / answer_accuracy /
                  escalation_classify / vision_extract / split / dedup_execute
  dispatch/       运营派单引擎（入库阶段选处理人）
  attachments/    附件异步流水线（download → MinIO → OCR）+ 缩略图
  ai_cs/          AI 客服共享纯生成层（replay + answer-router，不写库）
  zhichi/         智齿出站回写
  ksm/            KSM 回写 / 接管 takeover / 操作员身份 identity
  cascade/        reply/status/supply 级联 + outbox_retry + return_sync
  hub_issues/     毕业 creator / linear_push / webhook_push / op_status 状态机 /
                  module_owner / owner_split / hub_dedup / devcollab
  identity/ ingest/ routing/ sla/ supervisor/ metrics/ knowledge_feedback/ skills/
app/repositories/ 数据访问层
app/core/
  storage/        MinIO 对象存储（附件正本 + 缩略图缓存）
  pii/            PII 脱敏/还原（strict mypy，≥95% 覆盖率硬门槛）
  llm_router/     LLM Provider 抽象（dashscope + GLM，含 embeddings/vision）
  trace/ logging/
app/celery_app.py Celery + beat 调度（8 个定时任务）
app/models.py     所有 ORM 模型（单文件，按阶段分区注释）
app/db.py         engine + session（StaticPool for SQLite in tests）
```

### 四条横切约定（改代码前必读）

1. **Celery beat 任务统一范式**：自持 session、异常全吞（beat 永不死）、**开关关时任务内部自跳过**（beat 照常跳，不用改调度）。
2. **事务边界**：service 层普遍**不 commit**（`module_resolve`/`apply_op_status`/`content_refresh`/`takeover`/`manual_assign`），由调用方管；例外是 `return_sync.request_return` 和 `operation_answer._record_decision`（内部 commit）。
3. **灰度双开关**：`*_enabled`（默认 False）+ `*_dry_run`（默认 True）；失败 `attempts++`，超 `max_attempts` 标 failed 转人工；只有 pending 行被 drain，成功翻 sent 保证幂等。
4. **AI agent 一律「永不抛」**，失败降级到安全侧：转主管 / accuracy=0 / 返回 None / 落兜底目录。

### Frontend 类型同步

前端 API 类型从后端 OpenAPI schema 自动生成：`frontend/src/api/types.ts`。修改后端 API 后必须运行 `make gen-types` 并提交，否则 CI `make check-types` 会失败。

### 测试分层

- `tests/unit/` — 默认运行，SQLite in-memory（StaticPool），不需要 Docker
- `tests/integration/` — 需要 Docker（testcontainers），标记 `@pytest.mark.integration`
- `tests/e2e/` — 需要真实 UAT 凭证，标记 `@pytest.mark.e2e`
- `tests/eval/` — 需要 LLM Provider key，标记 `@pytest.mark.eval`

默认 `pytest` 只跑 unit（`pyproject.toml` 中 `-m "not integration and not e2e and not eval"`）。

### LLM Router

`app/core/llm_router/router.py` 抽象多 Provider，已实现 DashScope（`deepseek-v4-flash`，默认在前）+ GLM，failover 顺序看 `llm_provider_order`。另有 `embeddings.py`（hub_dedup 召回 + `cosine_similarity`）和 `vision.py`（qwen-vl 截图 OCR）。新增 Provider 约 80 行，实现 `BaseLLMProvider` 接口。接入 OpenAI/Anthropic 等**海外** LLM 前必须先补 PII 脱敏（`app/core/pii/` 的 AES-GCM encryptor 目前是 Protocol 占位）；当前全走国内管理大模型同边界，故非阻塞项。

## 前端 Auth Guard

`frontend/src/main.tsx` 中 `RequireAuth` 组件保护所有非登录路由，未登录或 token 过期自动跳转 `/login`（解析 JWT `exp` 字段判断）。auth token 存储在 `localStorage.auth_token`，飞书 SSO 回调后由 `consumeSsoFragment()` 写入（读 `#token=...` 落盘后清 hash）。

`frontend/src/api/client.ts` 中所有 API 请求收到 401 响应时，自动清除 localStorage 并跳转 `/login`。

JWT TTL 为 7 天（`backend/app/config.py` 中 `jwt_ttl_seconds = 60 * 60 * 24 * 7`）。

**`RequireAdmin`**（2026-08 新增）：`/admin/catalog`、`/admin/skills`、`/admin/holidays`、`/admin/dispatch` 四个页面包一层，非 admin（含 supervisor）直达 URL 会被重定向回 `/admin/users`——对齐后端 `require_admin` 的 403，避免主管点进去只看到报错。

## 前端多标签 keep-alive 架构（2026-08，`frontend/src/tabs/`）

本轮前端最大的结构性改动。**路由表已从 `main.tsx` 迁到 `tabs/appRoutes.tsx`**；`main.tsx` 只剩 `/login` 和 `/*`（catch-all → `RequireAuth` + `TabsProvider` + `Layout`），实际分发在 Layout 内每个 tab 自己的 `<Routes>`。

- `TabsContext.tsx` — tab 清单 + 激活态；**`tab.key = pathname`（去 search）**保证同一信息只开一个 tab；tabs + activeKey 持久化 localStorage，刷新可恢复
- `TabBar.tsx` — 顶部标签栏：点击激活 / × 关闭 / 中键关闭 / 溢出横向滚动
- `tabTitle.ts` + `useTabTitle.ts` — path→标题映射；详情页先占位，页面加载后 `updateTitle` 填真实短码（TKT-xxx / HUB-xxx / 客户名）
- **keep-alive 实现在 `Layout.tsx` 渲染层**：所有已打开 tab **同时挂载**，非活跃的用 `hidden` 藏起来；每个 tab 用自己冻结的 location 渲染，所以各 tab 的 `useParams`/`useSearchParams` 互不干扰

改前端路由/页面时注意：新增路由要同时加进 `appRoutes.tsx` 和 `tabTitle.ts`，否则 tab 标题会是兜底值。

## 服务器部署

详见 `CLAUDE.local.md`（不提交 git）。生产服务器 IP、nginx 配置、SSH 等均见 `CLAUDE.local.md`。

- shaobin 原版：端口 9093，路径 `/ticket-hub/`，DB `ticket_hub`
- panda_li v2：端口 9094，路径 `/ticket-hub-v2/`，DB `ticket_hub_v2`，Python 3.12

前端 build 需指定环境变量：
```bash
VITE_PUBLIC_BASE=/ticket-hub-v2/ VITE_API_BASE=/ticket-hub-v2 npm run build
```

## 当前技术债（2026-06-12 更新）

完整清单见 **`docs/progress/2026-06-12-plan.md` §四**（含冻结项说明）。要点：

- PII encryptor 未实现 —— **降级**：D4 第③段全走国内管理大模型同边界，仅接海外 LLM 才补（不再是第③段硬门槛）
- dedup 评测 —— **改判**：`expected_dedup` 字段 60 条全 null（实际未标注），生产无真实重复对；待积累真实数据再建配对评测集，不编造
- ~~ADR 0013/0014/0015 待补记~~ ✅ 已补（2026-06-12，见 `docs/adr/`）
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

系统有五个角色（2026-07-07 ADR-0016 P5 加第 5 个），前端统一显示中文名：

| 英文值 | 中文名 | 职责 |
|--------|--------|------|
| `member` | 普通成员 | 可查看工单和仪表板，无管理权限 |
| `assignee` | 处理人 | 可查看工单，被分配处理工单 |
| `knowledge_op` | 知识运营 | 反思诊断工作台 + 对客 AI 客服 skill / 知识库维护；够不到主管修正权与内部编排 skill |
| `supervisor` | 主管 | 可使用主管工作台、修正 Agent 决策、重新关联工单（天然涵盖知识运营能力） |
| `admin` | 管理员 | 拥有全部权限，含用户管理、分工配置、目录管理、内部编排 skill |

权限校验在 `backend/app/api/deps/auth.py`：`require_admin()`、`require_supervisor()`、`require_knowledge_op()`（knowledge_op|supervisor|admin）、`require_user()`。迁移 0020 扩 `ck_users_role`。

**注意 knowledge_op 的边界**：反思工作台端点组放行；`/api/admin/skills` 的**读**接口也放行（反思诊断训练页要用），但**写**接口（draft/promote/rollback/import）仍 `require_admin`；主管队列（split/dedup/complaint 等）一律 403。

**前端左侧导航（`Layout.tsx`，2026-08 改版，支持二级展开）**：

| 导航项 | path | 可见角色 |
|---|---|---|
| 工作台 | `/` | 全部 |
| 全部工单列表 | `/tickets` | 全部 |
| 工单任务表 | `/hub-issues` | 全部 |
| 反思诊断 | `/reflect` | knowledge_op+ |
| 反思诊断训练 | `/reflect-training` | knowledge_op+ |
| 统计看板（可展开）├ 综合看板 └ 每日看板 | `/analytics`、`/analytics/daily` | supervisor+ |
| 系统基础配置 | `/admin/users` | supervisor+ |

管理页顶部 tab 扩到 5 个：人员与分工（supervisor 可见）+ admin-only 的产品模块管理 / Skill 配置 / 节假日 / 派单规则配置。

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

D0✅ D1✅ D2✅ D3✅ D4✅（Linear 回同步 / cascade / KSM 回写 / Vision / escalation / Phase0 全家桶）。当前分支：`main`，迁移 head = **0044**。

**2026-07-13 ~ 08-14 大批量演进（约 464 提交，另一位协作者主导）**：智齿双向打通 / 运营派单引擎 / Operation 自动答复 + op_status 状态机 / 模块归类 / 答复准确率闸门 / 三道人工闸门 / 附件流水线 + MinIO / KSM 接管与退回 / 统计看板 + 每日看板 / 前端多标签架构。**这批功能的权威设计意图见 `docs/superpowers/plans/`（按日期命名，一功能一份）**，本文件只记要点。

> **ADR-0016 流水线重构（`docs/adr/0016-agent-pipeline-restructure.md`）P0-P2e 完成并部署 SIT；P4 owner-split + P5 权限双层代码完成（2026-07-07）**：triage 合一 / Complaint 第 5 型 / split 前置 / dedup+conflict_detect 退役 / skill 三槽 / 投诉人工队列 / 评测升级 / owner-split 子任务进度通知 / knowledge_op 角色。剩余 P3（反思闭环补全，**等用户给飞书知识空间 space_id + 把应用加进空间**）。

> **优化 v2 计划见 `docs/spec/d4-optimized-design-v2.md`**：Phase 0（PII 轻量/skill_prompts/hub-dedup/90天挂载[ADR-0016 已随 ticket-dedup 退役]/SLA工作日）全部完成部署；Phase 2（KSM 回写）代码完成待部署；**Phase 1（知识反哺闭环）阻塞于自研 AI 客服 replay+skills API**（方案 B，见 `ai-cs-api-contract.md`）。

## ADR-0016 流水线重构（2026-07-06/07 P0-P2e 全部落地）

- **triage agent**（`services/agents/triage.py` + `prompts/triage.md`）：classify + conflict_detect **合一**，单 LLM 调用输出 `{type, confidence, reason, is_mixed, sub_problems[]}`；sub_problem 带 `type`（子单继承，不再重分类）。写 `classify_type` 审计 +（混合时）`split_ticket` 审计（proposal.skill='triage'）
- **第 5 类型 Complaint（投诉）**：`prompts/type_taxonomy.md` 是 5 类型唯一权威定义，triage/classify prompt 用 `{{TYPE_TAXONOMY}}` 占位符注入（`assemble_prompt`）。投诉**停 ticket 层绝不自动毕业**（creator 守卫拒绝无 type 覆盖的投诉毕业）；人工出路：`POST /api/supervisor/close-complaint` 关闭，或 create-hub-issue 带 type 转型毕业
- **混合单闸门**：is_mixed 且 `SPLIT_AUTO_ENABLED` 关（默认）→ 停摆进主管拆单提案队列，**不毕业不分流**；开且 conf ≥ `SPLIT_AUTO_CONFIDENCE`(0.85) → 自动拆
- **ticket 级 dedup 退役**（P2e 删除）：agents/dedup.py + prompts/dedup.md + 6 个 dedup_* 配置已删；`cosine_similarity` 迁 `core/llm_router/embeddings.py`；**hub_dedup 是唯一主查重**。`dedup_execute.py` 暂留消化历史 dedup_link 提案，清零后连同 supervisor dedup-proposals 三端点整删。`ticket_embeddings` 表（迁移 0009）留存历史数据未删
- **conflict_detect 退役**（P2c 删除）：职责并入 triage；`CONFLICT_DETECT_ENABLED` 配置已删
- **classify 保留**：作为子单兜底分类器（旧 conflict_detect 提案的 sub_issues 无 sub_type 时用）+ 评测对照
- **skill 三槽版本**（P1，迁移 0018）：skill_prompts 加 draft/current/previous 槽；`admin_skills.py` PUT/DELETE draft、POST draft/validate（差异回放验证器 `draft_validator.py`，真实工单 current vs draft 对比）、POST draft/promote；skill 名去 `_v1` 后缀
- **评测**（P2e）：`scripts/eval/run_eval.py --agent triage|classify`（默认 triage），报告 mixed_diagnostics；2026-07-06 实测 triage 0.909 confirmed-only 过 0.9 门槛（classify 对照 0.955，差距在低置信边界样本，可经 skill draft 回放迭代 prompt）
- 单测注意：`tests/conftest.py` 显式清空 GLM/DASHSCOPE key，防止本地 `.env` 真实 key 让 BG task 发起真实 LLM 调用

## D3-D split 执行器（2026-06-11，ADR-0016 P2c 更新）

- `services/agents/split.py`：把 `split_ticket` 提案物化为 Child 工单，**全程无 LLM**（语义拆分 LLM 在 triage 已完成，此处纯机械物化 + 规则重路由）
- Child 契约（`ck_tickets_type_fields`）：`source_code/source_ticket_id=NULL`、`internal_split_id='{parent.short_code}-C{n}'`（确定性+unique）、`parent_ticket_id` 必填；title/body 来自 LLM 的 sub_issue（**不切原文**，原文留在 Parent）；customer/product_line/module/reporter 继承
- **子单类型继承**（P2c）：triage 提案的 sub_issue 带 `sub_type` → child 直接落 predicted_type（无 LLM）；旧提案无 sub_type → 兜底跑 classify。Child 不允许 Complaint
- Parent 翻转：type Raw→Parent、status→'split'、`children_ticket_ids` 落 JSON；幂等卫语句 `parent.type=='Raw'`
- 每个 child 重新走 Router（纯规则）各自分配 + 按类型分流；**绝不**再 triage（防递归拆分）
- 触发：conf ≥ `SPLIT_AUTO_CONFIDENCE`(0.85) 且 `SPLIT_AUTO_ENABLED`（**默认 false，先灰度手动**）→ ingest 链自动；否则留给主管 `POST /api/supervisor/execute-split`
- 回滚 `POST /api/supervisor/revert-split`：软删 children + Parent 还原 Raw + decision 翻 reverted；**任一 child status ≠ received 则拒绝**（有进展不可自动回滚）
- 物化审计写回 `decision.proposal.materialized`（at/by/child_ids/parent_prev_status）
- 注意：Router 的 `multi_match`（一个问题多团队认领）是归属歧义，**不是**拆分场景，split.py 只消费 `split_ticket`

## 主管工作台拆单提案 UI（2026-06-12）

- `GET /api/supervisor/split-proposals`：待处理提案队列（未物化、未 reverted、parent 仍 Raw；materialized 过滤在 Python 做，JSON 谓词不值得跨库写）
- `POST /api/supervisor/dismiss-split`：主管忽略提案 → decision 翻 `reverted`（留审计）；已物化的拒绝（409，提示走 revert-split）
- 前端 `SupervisorPage.tsx` 新增 `SplitProposalCard`：紫色卡片显示 short_code/置信度/理由/子单列表 + 「执行拆分」「忽略」按钮

## owner-split 按责任人拆分（2026-07-07，ADR-0016 P4）

- **场景**：一个 Demand/Bug_fix hub_issue 的工作分属多个责任人 → 主管在详情页手动拆成 N 个 Linear 子 issue（`parentId` 挂 hub 主 issue，Linear 原生父子）；LLM 预拆建议留 v2
- `services/hub_issues/owner_split.py`：`execute_owner_split`（守卫：研发类 only、hub 须已推 Linear、≥2 子任务、v1 不支持追加/重拆、个人责任人 Linear 查无此人直接拒绝）+ `notify_sub_issue_done`（进度通知）
- 跟踪表 `hub_issue_linear_issues`（迁移 0019）：linear_uuid/identifier/title/assignee_user_id/status(镜像 Linear 列名)/state_type/released_at/notified_at(防重)
- **进度通知（永不等齐 + 进度框架）**：`linear_status_sync` 每 5min 轮询未完成子 issue，转 completed → released_at + 自动入 outbox——**x<n 走新 kind `progress_note`**（KSM `handleKsmOrder(is_deal=False)` 只回复不关单），**仅 x=n 最后一条走 `release_note` 关单** + 置 `hub.release_notified_at`（与 devcollab.notify_release 互斥防二次关单，谁先谁算）
- 中途建失败：已建子 issue 行保留（Linear 侧已存在），报错带已建数；「已拆过」守卫挡住裸重试，人工去 Linear 补齐
- 子 issue reopen 不回滚通知（通知已对客发出，撤回是人工事务）；自查/无源 hub 不发通知
- 出站受 `ksm_writeback_enabled/dry_run` 灰度阀（同 Phase 2 剧本）；`ck_sync_outbox_kind` 扩 'progress_note'
- 前端 `HubIssueDetailPage`：Bug_fix/Demand 详情页「子任务里程碑」区（x/n 进度 + 每行状态色）+「按责任人拆分」表单（动态行：标题+责任人下拉，2-20 行）
- API：`POST /api/hub-issues/{id}/owner-split`（require_supervisor）；detail 响应带 `sub_issues[]`

## ~~D3-E dedup Agent~~（2026-06-12，**ADR-0016 P2e 已退役删除**）

- ticket 级 dedup（agents/dedup.py：embedding 入库 → 余弦召回 → LLM 判定）已删；**hub_dedup**（`services/hub_issues/hub_dedup.py`，建 Linear 前 hub 级语义查重）是唯一主查重
- embedding 基础设施保留：`app/core/llm_router/embeddings.py`（DashScope `text-embedding-v4` / GLM `embedding-3`，OpenAI `/embeddings` 方言 + failover）+ `cosine_similarity`，hub_dedup 消费
- `dedup_execute.py` + supervisor dedup-proposals 三端点暂留（消化历史 dedup_link 提案，无 LLM），存量清零后整删
- ingest 链顺序见「架构要点 → 数据流」（triage 主导，ADR-0016）

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
- **生产现状（2026-06-12 已配置部署）**：`LINEAR_PUSH_ENABLED=true`，默认 team=CNPRD（中国区产品部，id 见 `CLAUDE.local.md`）；首轮同步 21 个个人映射（INTPRD 9 / CNPRD 5 / ARALGO 5 / KNOPS 2），9 个组账号跳过；实测分配给某 ARALGO 成员的工单落到对应 team ✅
- API key：Linear 个人 key「ticket-hub push (shaobin prod)」，权限 Read + Create issues
- 单测：`test_linear_client.py`(13) / `test_linear_user_sync.py`(8) / `test_linear_push.py` 路由用例 / `test_admin_users.py` sync 端点

## Linear 状态回同步（2026-06-12，D4 第①段）

- `services/hub_issues/linear_status_sync.py`：Celery beat 5min 轮询已推送 hub_issue（最近 200 条），`LinearClient.get_issue_states()` 批量查（**50/批**防复杂度超限）
- 双层回写：`linear_status` 始终镜像 Linear 列名（展示层）；hub 状态只做**保守级联** `started→in_progress`、`completed→released`(+actual_released_at)
- `canceled` 只镜像不动状态（研发取消需主管判断）；**reopen 跟随**（released→in_progress，Linear 是研发态源头）；Linear 侧删除的 issue 只计数不动数据
- 状态变更写 status_history（`agent:linear_status_sync`）；无变化不写（幂等）
- beat 任务 `poll_linear_statuses_every_5min`（key 未配自动跳过）；生产已部署，实测 CNPRD-809 Backlog 正确镜像 ✅
- 升级路径：量大或要求实时再加 `/webhook/linear`，回写层不用改

## AI 客服 escalation 链（2026-06-12，D4 第③段 ③-2）

- **核心交互**：客户对已有飞书 AI 客服的回答不满意 → AI 客服实时回调 `POST /webhook/cs-escalation` → 建 `ai_cs` 工单 + 截图 attachments → `run_escalation_agents` 链
- `escalation_ingester.py`：`parse_escalation_payload` **隔离** AI 客服载荷格式（API 定稿后只改这一处，同 ksm_payload 套路）；黄金三元组（原问题/AI答复/不满反馈）存 `source_payload['ai_cs']`；幂等(session_id)
- `escalation_classify.py` + `prompts/escalation_classify_v1.md`：**黄金三元组**二次分类。强信号——AI给步骤+「做了没用」→Bug_fix；AI「不支持」+「要支持」→Demand；AI答错+客户重述→Operation。**显著压低 Operation 概率**（AI 已操作解答失败过）
- 链顺序：vision → escalation_classify → (auto hub_issue at `ESCALATION_AUTO_CONFIDENCE` 0.85，比普通 0.80 高，因直接推 Linear) → dedup → conflict_detect
- 判回 Operation 走 hub 主管 reply_sync（复用第②段，零新代码）；agent_decisions 里 `agent='escalation_classify_v1'` / `source='ai_cs_escalation'` 与普通 classify 区分
- 生产实测：「AI给认证步骤+客户说做了还是转圈超时」→ Bug_fix 0.94，理由精准 ✅
- **待补**：AI 客服真实 API 路径（webhook 载荷确认 + adapters/ai_cs 反查/反哺，③-3）

## Vision 多模态（2026-06-12，D4 第③段 ③-1）

- **架构前提**：已有飞书侧 AI 客服解答 Operation，取消自建 How-To RAG；hub 补「多模态 + 答不上之后的事」。详细设计 `docs/spec/d4-stage3-design.md`
- `app/core/llm_router/vision.py` VisionClient：DashScope **qwen-vl-max**（截图 OCR 要准）多模态，`image_url` 直传（DashScope 自己抓图）或 base64；结构化输出 `{ocr_text, ui_context, summary}`，容忍 ```json 围栏
- `services/agents/vision_extract.py`：ingest 链在 **classify 之前**对 image 附件 OCR → 拼进 `ticket.body`（`[附件识别]` 段）。下游 classify/dedup/escalation 全受益（dedup embedding 含报错原文，召回质量↑）
- `attachments` 表（迁移 0012）+ sources 种子 `ai_cs`；非 image/超 `VISION_MAX_IMAGES_PER_TICKET`(5)/无 source_url 跳过；失败标 failed 不阻塞链
- **PII**：qwen-vl 同 DashScope 边界，无新增暴露 → **PII encryptor 不再是第③段前置**（仅接海外 LLM 才补）
- 开关 `VISION_ENABLED`（默认 false）；`VISION_API_KEY` 留空回落 `DASHSCOPE_API_KEY`；生产已配 qwen-vl-max 实测打通（成本 ~¥0.011/张）
- 文本分类仍用 deepseek-v4-flash（评测最优，不动）；storage_key(MinIO) 下载路径待 KSM 附件接入

## cascade 双向同步（2026-06-12，D4 第②段）

- **reply_sync（决策 15）**：`POST /api/hub-issues/{id}/reply`（require_supervisor，Operation-only）→ 回复版本化（`hub_issue_reply_history`）→ 级联全部关联工单 `cached_reply_content/version` → `sync_outbox` 入队（每个**有源**工单一行；Child 只缓存不入队）
- **status_cascade（决策 14）**：`services/cascade/status_cascade.py` `apply_hub_status` 是 hub 状态变更的**唯一入口**（linear_status_sync 已改走它）。保守级联：仅 `in_progress`/`released` 扇出到工单（双方同名状态）；终态工单（done/closed/rejected/superseded）不动；released 补 `actual_released_at`
- **sync_outbox（ADR-0007，迁移 0011）**：出站写队列。D4 生产者入队（kind='reply'/'status'，status='pending'），**D5 sender（KSM 反向/智齿）消费**——先积累是刻意解耦
- 前端：`/hub-issues` 4 出口类型分视图（tab + 类型专属列），详情页 Operation 回复编辑器（保存并级联）
- 生产实测：回复 v1 → 工单缓存 v1 + outbox(reply, ksm, pending) ✅

## KSM 出站回写 sender（2026-06-26，D4 第②段，Phase 2）

- **缺口澄清**：`adapters/ksm/KSMClient` 早有全套写方法（lock/handle/supply/return/get_order_detail）；Phase 2 只补**消费 sync_outbox 的 sender**，非移植 client
- `services/ksm/writeback.py` `drain_ksm_outbox`：drain `target_source_code='ksm' & status='pending'`，按 kind 映射 KSM 操作：
  - `reply` → lock → 重拉 node → `handleKsmOrder(is_deal=True)`（答复关单）
  - `status` `in_progress` → `lockKsmOrder`（接管受理）；`released` → lock→handle 关单（hub.reply_content 或默认话术）
  - `supply` → lock → 重拉 → `supplyKsmOrder`（补料）
- **时序**：KSM 要求先接管(lock)且 handle 的 `currentNodeID` 是接管后的新节点 → lock → 经 NoticeStore(Redis 24h) 重拉 subscribeCallback 刷新 node/product/version/module → handle/supply；notice 过期则回落入库节点，由 KSM 报错暴露（**绝不静默成功**）
- **字段来源**：`ticket.source_payload['_subscribe_callback']`（入库时存的 KSM raw）；bill_id 回落 `source_ticket_id`
- **灰度**：`ksm_writeback_enabled`(默认关) + `ksm_writeback_dry_run`(默认开，只组装标 skipped)；失败 attempts++/last_error，超 `ksm_writeback_max_attempts`(5) 标 failed 转人工；仅 pending 被 drain，成功翻 sent 幂等；已接管错误容错继续
- **handler 身份**：`KSM_HANDLER_NAME`/`KSM_HANDLER_NUMBER`（account/accountName/accountNumber）；未配则整轮跳过
- 触发：Celery beat `drain_ksm_writeback` 每 2min + 主管 `POST /api/supervisor/drain-ksm-writeback`（同步看成败）
- **补料入口**：`POST /api/hub-issues/{id}/request-supply`（require_supervisor）→ `cascade/supply_sync.request_supply` 每有源工单入队 supply outbox（迁移 0016 扩 `ck_sync_outbox_kind` 加 'supply'）
- **注意**：回复/补料文本是**对客出站**方向，**不过 pii_lite 遮罩**（遮罩只用于入库/喂模型方向，遮了会损坏答复）；~~智齿回写本期未做~~ **已实现**（`services/zhichi/writeback.py` + beat `drain_zhichi_writeback_every_2min`，含 400258「工单已关闭」终态收尾；见 [[zhichi_writeback_400016_fix]]）——zhichi outbox 行有 sender 消费，不会堆积
- **上线**（用户执行）：`git pull` + `alembic upgrade head`(0016) + 重启 3 个 systemd → `.env` 配 handler 身份 + 生产 `KSM_BASE_URL=ierp.kingdee.com` → 先 enabled+dry_run 观察 → 再翻 dry_run=false 真打。**尚未部署生产**

## 主管运营 UI：dedup 提案 + pending 重推（2026-06-12，D4 第①段）

- `services/agents/dedup_execute.py`：dedup_link 提案执行器（无 LLM，镜像 split 剧本）。**采纳 = 重复工单挂到原始工单的 hub_issue**（occurrence_count+1 / last_seen_at / ticket_hub_issue_history / decision.materialized）
- 守卫：目标工单未毕业 hub_issue → 409 提示先 create-hub-issue（绝不自动毕业）；subject 已链接 → 409 提示走 relink
- 端点（require_supervisor）：`GET /api/supervisor/dedup-proposals`、`POST execute-dedup`/`dismiss-dedup`、`GET pending-hub-issues`（带最新 pending 原因）、`POST repush-linear`（**同步执行**，主管要立即看到成败）
- 前端 SupervisorPage：琥珀色「Linear 推送待人工」卡片（原因+重推）、青色「重复工单提案」卡片（采纳合并/忽略，目标未毕业禁用）；用户管理页「从 Linear 同步」按钮
- 卡片色系约定：紫=拆单提案、青=重复提案、琥珀=pending 待人工、黄=配置警告

## Linear 推送 pending 待人工（2026-06-12）

- **个人处理人（有邮箱）在 Linear 查无此人** → 不推送，hub_issue `status='pending'` + status_history 记原因（含邮箱）；**组账号（无邮箱）不受影响**，仍优雅降级推默认 team
- **Linear API 推送失败**（网络/鉴权/业务错）→ 同样置 pending + 错误原文
- 重试仍失败不重复写 history（pending 幂等）；`linear_uuid` 始终留 NULL 可重推
- **修复路径**：人加入 Linear 工作区 → `POST /api/admin/users/sync-from-linear` 补映射 → 重推成功自动 `pending→created`（留审计「pending 解除」）
- 生产实测：分配给某内部用户（Linear 查无此人）→ 正确置 pending 不产生垃圾 issue ✅

---

# 2026-07 ~ 08 新增子系统（464 提交批次）

> 以下各节是要点速查。**完整设计意图与取舍见 `docs/superpowers/plans/` 下同名日期的计划文档**。

## 三道人工闸门（`docs/superpowers/plans/2026-08-12-human-gates-over-ai-pipeline.md`）

见「项目概述」的闸门表。补充实现细节：

- 闸门① 开时 auto 路径**全类型**（含 Operation/Internal_task）毕业后停 `pending_review`，不分流；关时才按类型自动走。`gate_classify_enabled` 是 `bool | None`，None 经 `model_validator(mode="after")` 回落 `require_review_before_linear`。
- `confirm-classification` 按类型分流：Operation → `op_status=processing/agent`（进答复链）；研发类 → 闸门③开则 `pending_linear_review`，关则直接推；Internal_task → `created`。`reclassify` 镜像同一套分流。
- 闸门③ 的 `pending_linear_review` 是新状态，但 **`hub_issues.status` 是无 CHECK 的 String(32)，加状态不需要迁移**。
- 推送 assignee 优先级：确认时手选 > 模块研发责任人（`peek/consume_module_owner`）> hub 责任人回落 > 默认 team 无 assignee。
- ⚠️ 运维提醒：闸门①全开后所有工单堆 `pending_review` 需人消化；`modules.dev_owners` 要尽量补全，否则「待推 Linear」默认负责人常空需手选。

## 运营派单引擎（`services/dispatch/` + `/api/admin/dispatch/*`，迁移 0029/0032/0041）

- **只服务 Operation 运营分派**，与研发责任田 `assignment_scopes_*`（Router 入库路由用）**正交，是两套独立机制**，别混。
- 时机：**ticket 入库阶段**调用，早于 module_resolve/triage → 所以规则的 `match_product_lines`/`match_modules` 两个维度**已停用**（此时还没判出产品线/模块），只按来源 + SLA 匹配；模型列和历史数据保留，API DTO 不再暴露。
- 两种模式：`count`（今日未达 `daily_cap` 者选最少）/ `ratio`（按 `alloc_value` 权重选「应得占比 − 实际占比」缺口最大者）。
- 三级兜底链：main 全满 → `overflow_rule_id` 溢出规则 → `default_operation_assignee` 配置（tier = `main`/`overflow`/`default`）。
- 按天计数口径：`dispatch_log.created_at >= 北京自然日零点`（存 UTC 换算）——**天然按天重置，无需定时清零**。
- 坑：`dispatch_handler` **绝不抛异常**（吞掉返回空），不阻断入库；命中规则取 `priority` 最小的第一条。
- 关键语义：派单**只写 `handler_user_id`，不覆盖 `assigned_user_id`**（2026-08-12 回退了之前的覆盖决策）。

## Operation 自动答复 + op_status 状态机

**自动答复**（`services/agents/operation_answer.py`，beat 每 2min drain）：

- drain 扫描口径：`type='Operation'` + 未删 + **非 ai_cs 来源** + `status='created'`（用它区分闸门①-parked）+ `op_status=processing` + `op_handler='agent'`。
- 双层判定：`answer_router` LLM 判 D(直答)/C(补料)/transfer + **确定性硬 floor** `_is_answer_sendable`（长度 < `operation_auto_reply_min_length` 或含「无法处理/转人工/请联系客服」等关键词直接降级）——防 LLM 误判 D 时把兜底话术发给真实客户。
- 准确率闸 `operation_answer_accuracy_mode`：`observe` 只打分记录 / `enforce` 低于阈值转 reviewing / `review` 全部转审核。打分器 `answer_accuracy.py` **异常或非法 JSON 一律兜底 accuracy=0**（安全侧）。
- 坑：**`cited_knowledge`/`skills_used` 必须当场存**（D 和 D_review 两条路径都存）——反思诊断要还原黄金三元组，晚存就永久丢了。
- 坑：C 分支（需补料）不直接置「补料中」，只写处理说明草稿 + 把 handler 设为人工名，**避免被 drain 的 `processing+agent` 口径重复重答**。
- 重试：仅对 `AiCsNetworkError` 重试 3 次；业务错直接抛并落 `OP_EXCEPTION`，不无限重扫。

**状态机**（`services/hub_issues/op_status.py`）：`apply_op_status` 是 op_status 的唯一入口（仿 `apply_hub_status`），改 `op_status/op_handler/op_status_changed_at` + 写 status_history，**不 commit**。状态集 `processing/answered/closed/supplementing/reviewing/exception`。**映射驱动的底层动作（answered→author_reply、closed→关单回写）刻意不放这里**，保持纯状态维护。`close_overdue_answered`（每日 03:17）只动 op_status 不动 `hub.status`/`ticket.status`——T+7 是纯超时关闭无外部事件；驳回会刷新 `op_status_changed_at` 故天然不被扫到。

## AI 产品模块归类（`module_classify` + `module_resolve`，迁移 0034/0035）

- `module_classify`：纯判定，两步 LLM（选产品线 → 在该线下选模块），照 hub_dedup 范式**校验 choice 必落在候选内**；`line_hint` 让源系统已明确产品线时跳过 step1；`confidence` 取两步较低者。不写库、永不抛。
- `module_resolve`：决定生效值，**保证必落在现有 active 目录内，绝不自建**。回退链：① AI 置信度够 → ② 源系统模块名精确匹配（反推产品线）→ ③ 相似匹配（去空白转小写，先相等后互相包含）→ ②b `line_locked`（源系统锁定产品线但结果落别的线 → 用该线兜底模块）→ ④ `module_fallback_*` 兜底（「其他非发票云问题」PROLINE6067）。
- 源系统原值**仅首次**存 `source_payload["_original_catalog"]`；AI 判定写 `predicted_*` + `AgentDecision(classify_module)`；**不 commit**。

## 转研发出口：飞书 webhook（默认）vs Linear 直连

`linear_webhook_enabled` **默认 True** —— Bug_fix/Demand 以 `{"fields": {...}}` POST 到飞书 webhook，由飞书侧落 Linear/建单；`push_hub_issue_to_linear` 内部按开关分流到 `webhook_push.py`。

- 成功后同样回写 `hub.linear_uuid/identifier`（无 Linear id 时用回执或占位标记），让幂等与状态回同步逻辑复用。
- 字段口径坑：`ticketNo` 用来源系统 `source_ticket_number`（无则回落 `source_ticket_id`）——**不是本系统 short_code**；`handleUser` 用模块研发责任人，查不到才回落 hub 责任人；`ticketType` 中文映射（Bug_fix→bug、Demand→需求）。
- `adapters/linear/webhook_client.py` **复用 Linear adapter 的异常体系**（401/403→AuthError，其他→BusinessError，超时→NetworkError），让 `linear_push` 能统一 except。
- `module_owner.py` 数据源是 **`modules.dev_owners`（目录管理页维护，顿号/逗号分隔）**，不是 `assignment_scopes_module`。双入口：`peek_module_owner`（只读预览，不推进游标）/ `consume_module_owner`（选定并推进，仅真推送时调）。游标取模天然容错 dev_owners 编辑越界。

## 附件流水线 + MinIO（迁移 0022，`attachment_pipeline_enabled` 默认关）

- 异步 download → MinIO → vision OCR，beat 每 5min drain。只处理 `vision_status=='queued'`（KSM 附件）；escalation 的 `'pending'` 仍归 ingest 链 `vision_extract`。
- 坑：**MinIO 未配置 → 整批标 failed 转人工，绝不静默成功**。
- 缩略图 `thumbnail.py` 是**下载端点按需生成**并缓存回 MinIO（存量 storage_key 已落地，pipeline 不再扫无法预生成）；最长边 240px，统一输出 JPEG（带 alpha 先合成白底），非图片/解码失败返回 None 让调用方回落原图，**绝不阻断下载**。
- `minio_store.py`：附件 kind 按扩展名判定（image/pdf/video/other，未知→other，保守不当图片去 OCR）。

## 智齿双向打通（`services/zhichi/`，`docs/superpowers/plans/2026-07-13-zhichi-integration.md`）

- base url 用 **`https://www.soboten.com`**（用户环境国内域名，虽然智齿原生文档写 sobot.com）。
- 出站比 KSM 简单：**一个 `reply_ticket` 搞定**，无 KSM 的 lock→refresh→handle 时序、无 NoticeStore 重拉。
- kind → ticket_status 映射：`reply`/`release_note`/`status(released)` → `'3'`（已解决关单）；`supply`/`progress_note` → `'2'`（等待回复不关单）；`status(in_progress)` → skip（智齿无接管概念）。
- 坐席必须带 `reply_agentid`，取 `source_payload.raw.deal_agent_name`，空则回落 `zhichi_fallback_agent_name`（默认「莉莉」）；**查不到坐席记 failure 转人工，绝不静默跳过**。
- 入站信封解析 `_flatten_envelope()`：fields 中文块主源 + raw 兜底 + extend_fields_list（field_type=6 取 field_text），存整个信封，向后兼容旧扁平格式。

## KSM 接管 / 退回 / 操作员身份（迁移 0033/0036/0038/0043/0044）

- **接管 `takeover.py`**（`ksm_auto_takeover_enabled` 默认关）：`lockKsmOrder → 重拉详情 → handleKsmOrder`。**严格顺序：lock 后 node.id 会流转，必须重拉拿新 node 才能 handle**，否则报「已流转至其他节点」。是否 handle 看**本系统是否已有该单**（新单完整受理，已存在的只 lock 不 handle）。时机 2026-09 改回派单后立即触发，不等人工审核。**写操作绝不自动重试**（超时可能已成功，重试会重复接管）；失败只记 `ticket.ksm_takeover_status='failed'`，不回滚（无法回滚）。
- **操作员身份 `identity.py`**：从全局固定配置改为按 `ticket.handler_user_id` 解析。账号取 `User.ksm_account` 优先，空则回落 `User.employee_no`——**实测 KSM 工号与飞书同步的 employee_no 是同一套编号**（2026-09 SIT 验证），飞书同步已免运维填好，`ksm_account` 只给极少数不一致者手动覆盖。都空才回落全局 `ksm_handler_*`（兜底容错，非长期方案）。
- **退回 `cascade/return_sync.py`**：入 `kind='return'` 的 outbox 行 → sender 消费成 `returnKsmOrder`（退回不关单）。是**工单级动作而非 hub 级 fan-out**（一 ticket ↔ 一 KSM billId）；仅 KSM 来源可退回；退回目标节点**执行时实时计算**（2026-09 改判，见 `writeback._refresh_for_return`）。
- **outbox 重试 `cascade/outbox_retry.py`**：失败行永久卡在 `status='failed'`，两个 sender 的 drain 只扫 pending 永远不会再碰——所以给处理人做了自助重试（复用 sender 内部 `_process_row`，不重新实现发送）。

## 统计看板（`/analytics` + `/analytics/daily`，迁移 0023）

- **综合看板 `metrics/analytics.py`**（领导层研发管理视角）：只用原生字段保证新旧口径一致；`SLA 达成 = handle_hours <= sla_standard_hours`。**dialect 分支**：PG 用 `timezone()` 真转北京再切月，SQLite 退化为对 UTC `strftime`；中位数/P90 移到 Python 侧算等价 `percentile_cont` 线性插值，不依赖数据库端 percentile 函数。有硬编码 `_NON_DEV_STAFF` 排除名单。
- **每日看板 `metrics/daily.py`**（运营视角，按天 + 按 `handler_user_id`）：实时查询非物化。口径坑：完成 = 研发类 `to_status='released'` 或 Operation `to_status='closed' AND changed_by LIKE 'op:%'`，**一 hub 多 ticket 各计一次**；**KSM 打回/补充资料无专门字段，靠 `status_history.reason` 文本匹配识别**——依赖 `ksm_ingester.py` 写入的固定文案，**改文案会打断统计**。

## SLA 监控调度（`services/sla/sla_task.py`，`sla_watcher_enabled` 默认关）

beat 每 10min 两阶段共享一个 session：① `SLAWatcher.scan()` 检测超期写 `sla_overdue` 通知；② `EscalationWorker.escalate_pending()` 把超窗未确认的通知改投副手/主管。**默认关是故意的**——watcher 代码早已上线但从未被调度，**首次启用会对当前超期存量爆发一批通知**，建议非高峰时段启用。

## 对外 AI 客服查询接口（`/api/ai-cs/answer`）

- 鉴权**不是 JWT**，用 `webhook_access_token` + `hmac.compare_digest`（与 KSM/智齿 webhook 同源）。
- 纯生成：拼问题 → `ai_cs.replay` → answer-router 判 D/C/transfer，**不写库、不级联、不关单**。
- 共享层 `services/ai_cs/query.py` 同时被内部 `operation_answer` 和这个对外接口消费。

## 前端新增页面要点

- **`OpsPanel`**（工作台内嵌，supervisor+）：KSM / 智齿 / 附件三行「立即 drain」，内联显示扫描/发送/跳过/失败 + 灰度态（「已启用」「（仅组装未真发）」）。
- **工单列表**改用 `@tanstack/react-table`：列宽拖拽 / 列顺序拖拽 / **列偏好持久化 localStorage**；筛选扩到处理人多选、类型多选、提单企业、超时状态、多组时间区间；批量操作「重新触发分配 / 批量指派 / 批量移交 / 批量补充资料」。
- **工单详情**「工单调整 V1.0」重排：左时间轴 + 右详情；新增回写失败横幅。⚠️ **附件展示、处理说明编辑、操作记录等多处只搭了 UI 骨架 + 「待后端支持」占位**。
- **hub 详情**：横向里程碑时间轴（节点可选中编辑解决方案）；⚠️ 逐节点解决方案落库、任务状态落库**均待后端**，目前只存本地草稿。
- **反思诊断训练 `/reflect-training`**：⚠️ **skill 名称/描述走真实 API，其余（编号、来源、准确率、校验列表）是本地 mock**；目标准确率双击编辑只存本地 state 不落库；「调整」面板的验证+替换**刻意不调用** `draft/promote`（防误改生产分类 agent）。
- 新增共享组件：`hubActions.tsx`（催办/发版通知/回访 + Modal 原语）、`OpStatusBadge.tsx`、`Drawer.tsx`、`Lightbox.tsx`（点遮罩**不**关闭，用于需谨慎操作的面板）。
