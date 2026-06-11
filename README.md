# ticket-hub

跨源工单枢纽（KSM / 智齿 / zammad / Linear），Agent 全自动 + 主管事后修正。

> 本仓库由 [feishu-workorder](https://github.com/invagent/feishu-workorder) 演进而来。
> 详细背景与 7 阶段路线图见 `feishu-workorder/upgrade_plan.md`（v0.5.6, 2398 行）。

## 状态

- **当前阶段**：D3🟡（A/B/C 完成，D/E 待开工）
- **起始日**：2026-05-04
- **目标完工**：2027-03-19（10.5 个月，1 人 + Claude）
- **进度详情**：`docs/progress/2026-05-11-status.md`

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

## 飞书应用配置

本项目依赖飞书开放平台应用，需在 [飞书开放平台](https://open.feishu.cn/) 创建自建应用并完成以下配置。

### 必须开通的 API 权限

| 权限标识 | 用途 |
|---------|------|
| `contact:contact.base:readonly` | 读取通讯录基本信息（用户列表、部门列表） |
| `contact:user.base:readonly` | 读取用户基本信息（姓名、头像） |
| `contact:user.employee_number:read` | 读取用户工号（同步 employee_no 字段必须） |
| `contact:user.email:readonly` | 读取用户邮箱 |
| `contact:user.phone:readonly` | 读取用户手机号 |
| `contact:department.base:readonly` | 读取部门名称等基本字段 |
| `contact:department.organize:readonly` | 读取部门人数（member_count） |

> 以上权限在「权限管理 → API 权限」中搜索开通，开通后需**重新发布应用版本**才能生效。

### 通讯录数据范围

在「安全设置 → 通讯录权限范围」中，将可见范围设为**全部员工**（或指定需要同步的部门）。  
未配置此项时，即使 API 权限已开通，调用通讯录接口也会返回 `40004 no dept authority`。

### SSO 登录权限

| 权限标识 | 用途 |
|---------|------|
| `authen:user_info:read` | 获取登录用户信息（SSO 回调） |

### 回调地址配置

在「安全设置 → 重定向 URL」中添加：

```
https://<your-domain>/ticket-hub-v2/api/auth/feishu/callback
```

### .env 配置项

```bash
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 首次登录注意

第一个通过飞书 SSO 登录的用户默认 `role='member'`，无法访问管理后台。需手动提权：

```sql
UPDATE users SET role = 'admin' WHERE id = 1;
```

改完后重新登录（旧 JWT 里的 role 是快照，不会自动刷新）。

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
