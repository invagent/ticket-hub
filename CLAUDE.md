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

## 当前技术债（D3-A 后）

- `make lint` 当前红灯：约 20 个 ruff 错 + 17 个 mypy 错（主要在 `app/api/webhooks.py`）
- `backend/tests/eval/dataset_v1.jsonl` 只有 4 条占位，classify 准确率未量化
- `HANDOFF.md` 严重过时（D0 时期），以 `docs/progress/2026-05-11-status.md` 为准
- PII encryptor 未实现（接外部 LLM 前必须补）

## 阶段进度

D0✅ D1✅ D2✅ D3🟡（A/B/C 完成，D/E 待开工）D4~收尾⬜。当前分支：`feat/d1-identity-routing`。
