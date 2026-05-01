# ADR-0001: 技术栈与 Monorepo 结构

- 状态：Accepted (D0)
- 日期：2026-05-04
- 决策依据：upgrade_plan.md v0.5.6 §10（10 项决策）+ §附录 A

## 背景

需要从 feishu-workorder（Python + SQLite + 飞书多维表格）演进为多源工单
枢纽 ticket-hub。核心要求：API-first、CLI-first、MCP-ready；多 LLM Provider；
PII 合规；7 阶段递进上线。

## 决策

- **Monorepo**：单仓库 `invagent/ticket-hub`，子目录 backend / frontend /
  cli / mcp_server / scripts / docs（决策 R1）。
- **Backend**：Python 3.11+ / FastAPI / SQLAlchemy 2.x / Alembic / Celery /
  PostgreSQL 16 + pgvector / Redis 7 / MinIO（决策 10）。
- **Frontend**：Vite + React 18 + TS + Tailwind + TanStack Query +
  React Router；openapi-typescript 自动生成 API 类型。
- **CLI**：Typer + httpx；映射 OpenAPI（决策 10）。
- **Auth**：飞书 SSO 唯一入口，JWT 短票（决策 D19）。
- **PII**：自实现 Sanitizer/Restorer + AES-GCM；env `PII_MASTER_KEY`，季度轮换；
  阶段 5 评估接公司 KMS（决策 D4 / D-D4）。
- **LLM**：纯配置驱动 Router，4 家 Provider（OpenAI/DeepSeek/GLM/Anthropic）（决策 5）。

## 后果

- 1 套依赖管理（pyproject.toml + package.json）；语言间通过 OpenAPI 对齐。
- 部署形态：backend 容器 + Celery worker/beat 容器 + frontend 静态托管。
- 飞书凭证、PII 主密钥、LLM Keys 必须从 Secret 管理注入；`.env` 不入仓。
