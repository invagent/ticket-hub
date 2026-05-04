# ticket-hub

跨源工单枢纽（KSM / 智齿 / zammad / Linear），Agent 全自动 + 主管事后修正。

> 本仓库由 [feishu-workorder](https://github.com/invagent/feishu-workorder) 演进而来。
> 详细背景与 7 阶段路线图见 `feishu-workorder/upgrade_plan.md`（v0.5.6, 2398 行）。

## 状态

- **当前阶段**：D0 — 仓库初始化 + SSO POC + 工具链奠基
- **起始日**：2026-05-04
- **目标完工**：2027-03-19（10.5 个月，1 人 + Claude）
- **方案文档**：`/Users/shaobin/.claude/plans/upgrade-plan-cheerful-kitten.md`

## 仓库结构

```
ticket-hub/
├── backend/              FastAPI + SQLAlchemy + Alembic + Celery
├── frontend/             Vite + React + TS + Tailwind
├── cli/                  Typer CLI（OpenAPI-driven）
├── mcp_server/           FastMCP（D5 启用）
├── scripts/
│   ├── feishu_pg_diff.py       双跑对账（D1~D6 每 4h）
│   ├── eval/run_eval.py        Agent 评测集执行器（D3 起）
│   └── perf/webhook_locust.py  D6 性能压测
├── backend/config/mappings/    KSM 问题类型映射 yaml（D0~D1 早期联调）
├── docs/
│   ├── adr/                    架构决策记录
│   ├── spec/                   data_model / api / routing 三份规格
│   └── runbook/                D6 运维剧本
├── docker-compose.yml          PG16+pgvector / Redis7 / MinIO
└── .github/workflows/ci.yml    lint + unit + pii-cov + integration
```

## 快速开始

```bash
# 1. 起本地依赖
docker compose up -d pg redis minio

# 2. backend
cd backend
make install             # 创建 .venv 并安装
cp .env.example .env     # 填飞书/KSM/智齿凭证（D1 才用得到）
.venv/bin/alembic upgrade head
make unit                # 跑单测
make pii-cov             # PII 模块 ≥95% 覆盖率门槛
.venv/bin/uvicorn app.main:app --reload --port 8080

# 3. frontend
cd ../frontend
npm install
npm run dev              # http://localhost:5173

# 4. CLI（可选）
cd ../cli
pip install -e ".[dev]"
ticket-hub --help
```

## 量化目标（v0.5.6 §12）

| 指标 | 目标 |
|------|------|
| 自动分配命中率（D20）| ≥ 95% |
| 主管调整率（reverted）| < 10% |
| SLA 健康度 | ≥ 90% |
| LLM 平均成本/工单 | < $0.05 |
| PII 漏脱率（季度）| 0 |

## 关键决策（D1~D20 + R1/R2 + 铁律）

详见 `feishu-workorder/upgrade_plan.md` §11.2 已决策项清单。

## 测试纪律

- 单测 ≥ 70%，PII 模块 ≥ 95%
- LLM Provider 切换 / 主干合并 → 跑评测集
- D1~D6 双跑对账，差异 > 0.1% 阻断发布
- D6 切流 5/25/50/100 四档 + 混沌测试

## 许可

内部项目。
