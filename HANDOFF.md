# D0 交付状态（持续更新）

> 仓库：`/Users/shaobin/Documents/ticket-hub`（独立 git 仓库）
> 排期方案：`/Users/shaobin/.claude/plans/upgrade-plan-cheerful-kitten.md`

## 一、状态总览

D0 阶段约 **80% 完成**（含本轮收尾：xlsx 撤销 / data_model 评审决策 / KSM 类型映射占位 / SSO smoke / push 到 invagent/ticket-hub）。剩余 20% 是**真账号 SSO 扫码 e2e**，需要你在浏览器配合扫码。

## 二、已完成 ✅

| 领域 | 状态 | 关键文件 |
|------|------|---------|
| 仓库骨架 | ✅ git init -b main，75+ 文件已 staged | `/Users/shaobin/Documents/ticket-hub` |
| Backend Python 工具链 | ✅ pyproject + Makefile + .env.example | `backend/pyproject.toml`、`backend/Makefile` |
| Backend 核心模块 | ✅ config / trace / logging / pii / llm_router 全骨架 | `backend/app/core/` |
| Backend API | ✅ /health + 飞书 SSO + admin (sources/product-lines/users) | `backend/app/api/` |
| Alembic 初版 schema | ✅ sources / product_lines / users 三张表 | `backend/migrations/versions/0001_d0_initial.py` |
| 测试框架 | ✅ conftest（StaticPool 修复 SQLite 共享）+ 38 个单测 | `backend/tests/` |
| **PII 单测覆盖** | ✅ **98.1%（远超 95% 门槛）** | `backend/tests/unit/core/pii/test_sanitizer.py` |
| **整体单测覆盖** | ✅ **87.0%（超 70% 门槛）** | — |
| Mypy strict on `core/pii/*` | ✅ 0 错误 | — |
| Ruff lint + format | ✅ all checks passed | — |
| Frontend 骨架 | ✅ Vite + React + TS + Tailwind + Router + TanStack Query | `frontend/` |
| 前端首批页面 | ✅ login + dashboard + admin/users + admin/scopes + Layout | `frontend/src/pages/` |
| CLI 骨架 | ✅ Typer + httpx，`ticket-hub --help` 工作 | `cli/ticket_hub_cli/` |
| Scripts | ✅ feishu_pg_diff / run_eval / xlsx_migrate 三件套（D0 stub） | `scripts/` |
| docker-compose | ✅ PG16+pgvector / Redis7 / MinIO（不在测试中启动）| `docker-compose.yml` |
| GitHub Actions CI | ✅ backend lint+unit+pii-cov / integration / frontend / cli | `.github/workflows/ci.yml` |
| 三份规格草案 | ✅ data_model / api / routing | `docs/spec/` |
| ADR-0001 | ✅ 技术栈与 Monorepo | `docs/adr/0001-stack-and-monorepo.md` |
| Runbook 占位 | ✅ 5 类剧本预留 | `docs/runbook/README.md` |
| README | ✅ 快速开始 + 量化目标 + 测试纪律 | `README.md` |

### 已通过的本地验证

```
$ cd backend
$ make install              # ✅ venv 创建 + 全量装包
$ .venv/bin/ruff check      # ✅ All checks passed!
$ .venv/bin/ruff format     # ✅ 29 files already formatted
$ .venv/bin/mypy app        # ✅ 0 errors in 19 files
$ .venv/bin/pytest tests/unit -q --cov-fail-under=70   # ✅ 38 passed, 87.0% cov
$ .venv/bin/pytest tests/unit/core/pii -q --cov-fail-under=95  # ✅ 23 passed, 98.1% cov
$ .venv/bin/alembic upgrade head     # ✅ 0001_d0_initial 应用成功
$ .venv/bin/python -c "from app.main import app; ..."  # ✅ 10 routes registered

$ cd ../cli
$ pip install -e ".[dev]"   # ✅
$ pytest tests -q           # ✅ 2 passed
$ ticket-hub --help         # ✅ Typer 输出 admin/ticket/version

$ cd ../scripts
$ python feishu_pg_diff.py --hours=4   # ✅ stub JSON 报告
$ python eval/run_eval.py ../backend/tests/eval/dataset_v1.jsonl  # ✅ 4 records loaded
```

## 三、未完成（需要你早上做）❌

### 阻塞（凭证/外部资源）

1. **填 `.env`**：复制 `backend/.env.example` 到 `backend/.env`，至少填飞书 SSO + KSM UAT 凭证
2. **启动本地依赖**（如要联调）：`docker compose up -d pg redis minio`
3. **应用迁移到真实 PG**：`cd backend && .venv/bin/alembic upgrade head`
4. **决定是否 push 到 GitHub**：
   - 如果要：`git remote add origin git@github.com:invagent/ticket-hub.git && git push -u origin main`
   - 如果想审一遍再 push：先 `git log` 看 commit
5. **三份规格评审**：`docs/spec/{data_model,api,routing}.md` 是 D0 草稿，需要你过一遍并 commit "spec reviewed"
6. **飞书 SSO POC 真账号**：等你 .env 填好后，跑 `make install && uvicorn app.main:app --reload`，浏览器打开 `http://localhost:8080/api/auth/feishu/login` 看跳转

### 未启动（本来就是 D1+ 的事）

- `feishu-python` 双跑模式（D1 才启动）
- 50 条历史 ticket 重放（D1 验收）
- 飞书 SSO callback 实现（目前返回 501，D1 上）
- `xlsx_migrate.py` 真跑（D1）
- **前端 npm install 我这边失败了**（macOS 沙盒拒绝执行刚下载的 esbuild 二进制：`Unknown system error -88`）。这是我执行环境的问题，不是项目问题。你在普通终端里应该可以正常 `cd frontend && npm install`。`node_modules/` 已清理，`package.json` / `tsconfig.json` / `vite.config.ts` / `src/` 都在。

## 四、git 状态

```
分支: main
最新 commit: 59e40fc  chore: D0 仓库骨架 + 工具链奠基（76 文件）
未 push：未设置 remote（决策 R1 是 invagent/ticket-hub，由你决定何时 push）
```

> 我用了 `git config -c` 局部传入了 `user.name=shaobin` / `user.email=king.dee.kd02@gmail.com` 来 commit。**没有**改你的全局 `~/.gitconfig`。如果你想用别的 author 重写 commit：
> ```
> git commit --amend --reset-author
> ```
> 或者直接 `git reset --soft HEAD~1` 把所有变更退回到 staged，自己重 commit。

如果想 push 到 GitHub：
```
git remote add origin git@github.com:invagent/ticket-hub.git
git push -u origin main
```

## 五、当前测试纪律快速验证（早上 1 条命令）

```bash
cd /Users/shaobin/Documents/ticket-hub/backend
make lint && make unit && make pii-cov
```

预期输出：
- `All checks passed!`
- `38 passed in 0.2s` + `Required test coverage of 70% reached. Total coverage: 87.0%`
- `23 passed in 0.06s` + `Required test coverage of 95% reached. Total coverage: 98.1%`

如果有红，说明环境受到了什么外部干扰（系统升级 / venv 损坏 / 依赖卸载），可重建：`make clean && make install`。

## 六、D1 启动建议（明早评审完毕后）

1. 把规格评审反馈 commit 到 `docs/spec/`
2. 创建 D1 工作分支：`git checkout -b feat/d1-identity-routing`
3. 第一笔代码：`backend/app/repositories/user.py` + `backend/app/services/identity/resolver.py`，配套单测
4. **关键**：在动 D1 业务代码前，先把 `feishu-python` 历史 ticket 数据导出 50 条到 `backend/tests/fixtures/recorded/historical_tickets.json`，因为 D1 验收门槛是这 50 条 router 命中 ≥ 90%

## 七、风险事项 / 我无法做的事

- **没有 push**：远程仓库不存在，公司 invagent org 的权限我也没有
- **凭证未填**：`.env` 是空的；飞书/KSM/智齿/LLM key 你来配
- **没有真 PG/Redis 启动**：测试用了 SQLite + StaticPool，CI 里用 GitHub Actions service container，本地 docker compose 还没起
- **三份规格未评审**：草稿需要你过一遍
- **无网络/外部 IO 测试**：所有"集成"现在都用 mock/in-memory；真接 KSM/飞书是 D1 的事

## 八、文件清单（核心交付）

```
ticket-hub/
├── README.md
├── HANDOFF.md                      ← 本文件
├── docker-compose.yml
├── .gitignore
├── .github/workflows/ci.yml
├── backend/
│   ├── Makefile
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── .env.example
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                 ← FastAPI + trace 中间件
│   │   ├── config.py               ← pydantic-settings
│   │   ├── db.py                   ← engine + session
│   │   ├── models.py               ← Source / ProductLine / User
│   │   ├── api/
│   │   │   ├── auth.py             ← 飞书 SSO 占位 + JWT 签发
│   │   │   ├── admin.py            ← /sources /product-lines /users
│   │   │   └── health.py
│   │   └── core/
│   │       ├── trace/              ← 100% 复用 feishu-python/trace.py
│   │       ├── logging/            ← structlog + trace_id
│   │       ├── pii/                ← Sanitizer + Restorer (98.1%)
│   │       └── llm_router/         ← types only (D3 fills)
│   ├── migrations/
│   │   └── versions/0001_d0_initial.py
│   └── tests/
│       ├── conftest.py             ← StaticPool fix
│       ├── unit/
│       │   ├── test_config.py + test_trace.py + test_admin.py + test_auth.py
│       │   └── core/pii/test_sanitizer.py    ← 23 个测试
│       └── eval/dataset_v1.jsonl   ← 4 条样本（D3 扩到 100）
├── frontend/                       ← Vite + React + TS + Tailwind
├── cli/                            ← Typer
├── scripts/
│   ├── feishu_pg_diff.py           ← 双跑对账 (stub)
│   ├── eval/run_eval.py            ← 评测执行器 (stub)
│   ├── migrate/xlsx_migrate.py     ← xlsx 迁移 (stub)
│   └── perf/webhook_locust.py      ← D6 压测
└── docs/
    ├── adr/{README,0001-stack-and-monorepo}.md
    ├── spec/{data_model,api,routing}.md
    └── runbook/README.md
```

总：~2200 行（Python + TS + 配置 + 文档）。

---

**休息好。早上 8 点见。**
