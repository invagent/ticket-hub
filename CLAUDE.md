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

## 当前技术债（D3-A 后）

- `make lint` 当前红灯：约 20 个 ruff 错 + 17 个 mypy 错（主要在 `app/api/webhooks.py`）
- `backend/tests/eval/dataset_v1.jsonl` 只有 4 条占位，classify 准确率未量化
- `HANDOFF.md` 严重过时（D0 时期），以 `docs/progress/2026-05-11-status.md` 为准
- PII encryptor 未实现（接外部 LLM 前必须补）

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

## 阶段进度

D0✅ D1✅ D2✅ D3🟡（A/B/C 完成，D/E 待开工）D4~收尾⬜。当前分支：`feat/d1-identity-routing`。
