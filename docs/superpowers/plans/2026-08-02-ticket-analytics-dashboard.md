# 工单维度统计看板 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给领导层建一个工单维度研发管理统计看板（主管/管理员专属），基于系统原生字段统计所有工单。

**Architecture:** 迁移加 handle_hours/sla_standard_hours 列并回填历史工单 → 后端 analytics 聚合服务（实时多维 SQL）→ `/api/metrics/ticket-analytics`（require_supervisor）→ 前端 recharts 看板页 `/analytics`。

**Tech Stack:** FastAPI + SQLAlchemy + Alembic（PG16）；React + TypeScript + TanStack Query + recharts（新引入）。

## Global Constraints

- 后端聚合用纯 SQL（GROUP BY + percentile_cont），实时查询非物化；参照 `app/services/metrics/workbench.py` 模式
- 时区：received_at 按北京时区切月（`app/services/sla/workday.py` 的 `BEIJING`）
- 指标口径：只用系统原生字段（tickets 原生列 + handle_hours/sla_standard_hours 回填列），不读 source_payload 富字段
- SLA 达成 = `handle_hours <= sla_standard_hours`；耗时统计只计 handle_hours 非空的工单
- 权限：analytics API + 前端页均 require_supervisor（supervisor|admin）
- 迁移：下一个版本号 0023，down_revision=0022_attachment_queued
- 改后端 API 后必须 `make gen-types` 更新 frontend/src/api/types.ts
- 前端角色 gate 用 `frontend/src/api/auth.ts` 的 `isSupervisor()`；导航加 `roles: ["supervisor","admin"]`（`Layout.tsx` navItems 模式）
- 图表配色遵循 AI 分类色系：Bug_fix=红/Demand=蓝/Operation=黄/Internal_task=灰
- 参考设计：`docs/superpowers/specs/2026-08-02-ticket-analytics-dashboard-design.md`
- SIT 库真值：ticket_hub_sit @ 106.55.57.40，5888 条历史工单；SIT 代码烘进镜像，脚本用 docker cp 进容器 /app 跑

---

### Task 1: 迁移加列 + 回填历史工单耗时

**Files:**
- Create: `backend/migrations/versions/0023_ticket_handle_hours.py`
- Create: `scripts/backfill_handle_hours.py`
- Modify: `backend/app/models.py`（Ticket 加两列）

**Interfaces:**
- Produces: `tickets.handle_hours` (Numeric|None)、`tickets.sla_standard_hours` (Numeric|None) 列；历史工单回填值

- [ ] **Step 1: models.py 加两列**

在 `app/models.py` Ticket 类的 `predicted_confidence` 附近（AI 分类字段区）加：
```python
    # 研发管理统计（handle_hours 回填自飞书耗时/未来由 SLA watcher 计算）
    handle_hours: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)
    sla_standard_hours: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)
```

- [ ] **Step 2: 写迁移**

Create `backend/migrations/versions/0023_ticket_handle_hours.py`：
```python
"""ticket: add handle_hours + sla_standard_hours for analytics dashboard

Revision ID: 0023_ticket_handle_hours
Revises: 0022_attachment_queued
"""

import sqlalchemy as sa
from alembic import op

revision = "0023_ticket_handle_hours"
down_revision = "0022_attachment_queued"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("handle_hours", sa.Numeric(7, 2), nullable=True))
    op.add_column("tickets", sa.Column("sla_standard_hours", sa.Numeric(7, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "sla_standard_hours")
    op.drop_column("tickets", "handle_hours")
```

- [ ] **Step 3: 写回填脚本**

Create `scripts/backfill_handle_hours.py`（沿用 migrate_ksm_reporter.py 的 init_engine/get_session 模式，从 backend/ 运行）：
```python
"""回填历史飞书导入工单的 handle_hours / sla_standard_hours / actual_resolved_at。

从 source_payload._feishu_import 读「工单耗用时间」「处理时长标准」，写入原生列；
handle_hours 有值且 actual_resolved_at 空 → actual_resolved_at = received_at + handle_hours。
幂等（handle_hours 已填则跳过）。

用法（backend/ 目录）：
    python3 ../scripts/backfill_handle_hours.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from decimal import Decimal, InvalidOperation

sys.path.insert(0, ".")
from sqlalchemy import select

from app.db import get_session, init_engine
from app.models import Ticket


def _num(v) -> Decimal | None:
    s = str(v or "").strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def main(dry_run: bool) -> None:
    init_engine()
    db = next(get_session())
    try:
        tickets = (
            db.execute(select(Ticket).where(Ticket.source_payload.is_not(None)))
            .scalars()
            .all()
        )
        filled = skipped = 0
        for t in tickets:
            fi = (t.source_payload or {}).get("_feishu_import")
            if not fi or t.handle_hours is not None:
                skipped += 1
                continue
            hh = _num(fi.get("工单耗用时间"))
            std = _num(fi.get("处理时长标准"))
            if hh is None and std is None:
                skipped += 1
                continue
            if not dry_run:
                t.handle_hours = hh
                t.sla_standard_hours = std
                if hh is not None and t.actual_resolved_at is None and t.received_at is not None:
                    t.actual_resolved_at = t.received_at + timedelta(hours=float(hh))
            filled += 1
        if not dry_run:
            db.commit()
        print(f"{'[dry-run] ' if dry_run else ''}filled={filled} skipped={skipped}")
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    main(ap.parse_args().dry_run)
```

- [ ] **Step 4: 本地跑迁移 + 单测确认模型可用**

Run: `cd backend && .venv/bin/alembic upgrade head && .venv/bin/pytest tests/unit -k ticket -q 2>&1 | tail -5`
Expected: 迁移成功，现有 ticket 相关单测不因加列而失败。

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/migrations/versions/0023_ticket_handle_hours.py scripts/backfill_handle_hours.py
git commit -m "feat(analytics): tickets 加 handle_hours/sla_standard_hours 列 + 回填脚本"
```

### Task 2: 后端 analytics 聚合服务

**Files:**
- Create: `backend/app/services/metrics/analytics.py`
- Test: `backend/tests/unit/services/test_analytics.py`

**Interfaces:**
- Consumes: Task 1 的 handle_hours/sla_standard_hours 列
- Produces: `compute_ticket_analytics(db, *, start=None, end=None, product_line=None) -> TicketAnalytics`（dataclass，字段见下）

- [ ] **Step 1: 写失败测试**

Create `backend/tests/unit/services/test_analytics.py`：
```python
from datetime import UTC, datetime
from decimal import Decimal

from app.models import Ticket
from app.services.metrics.analytics import compute_ticket_analytics


def _tk(db, **kw):
    n = db.query(Ticket).count() + 1
    defaults = dict(
        short_code=f"TKT-{n:06d}", source_code="ksm", source_ticket_id=f"S{n}",
        type="Raw", status="done", received_at=datetime(2026, 4, 1, tzinfo=UTC),
    )
    defaults.update(kw)
    t = Ticket(**defaults)
    db.add(t); db.flush(); return t


def test_kpi_counts_by_type_and_sla(db_session):
    _tk(db_session, predicted_type="Bug_fix", handle_hours=Decimal("6"), sla_standard_hours=Decimal("8"))
    _tk(db_session, predicted_type="Operation", handle_hours=Decimal("50"), sla_standard_hours=Decimal("40"))
    db_session.commit()
    r = compute_ticket_analytics(db_session)
    assert r.kpi.total == 2
    assert r.kpi.by_type["Bug_fix"] == 1
    assert r.kpi.by_type["Operation"] == 1
    # 1 达标(6<=8), 1 超期(50>40) → sla_rate=0.5
    assert abs(r.kpi.sla_rate - 0.5) < 1e-6


def test_by_product_line_and_assignee(db_session):
    _tk(db_session, product_line_code="发票云", predicted_type="Bug_fix", assigned_user_id=None, handle_hours=Decimal("4"))
    _tk(db_session, product_line_code="发票云", predicted_type="Operation", handle_hours=Decimal("10"))
    db_session.commit()
    r = compute_ticket_analytics(db_session)
    pl = next(x for x in r.by_product_line if x["product_line"] == "发票云")
    assert pl["total"] == 2


def test_trend_by_month(db_session):
    _tk(db_session, received_at=datetime(2026, 4, 15, tzinfo=UTC), handle_hours=Decimal("10"))
    _tk(db_session, received_at=datetime(2026, 5, 15, tzinfo=UTC), handle_hours=Decimal("20"))
    db_session.commit()
    r = compute_ticket_analytics(db_session)
    months = {x["month"] for x in r.trend}
    assert "2026-04" in months and "2026-05" in months
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_analytics.py -q`
Expected: FAIL（ImportError: compute_ticket_analytics 未定义）

- [ ] **Step 3: 实现 analytics 服务**

Create `backend/app/services/metrics/analytics.py`：
```python
"""工单维度统计看板聚合（领导层研发管理）。

只用系统原生字段（tickets 列 + handle_hours/sla_standard_hours 回填列），
新旧工单口径一致。实时多维聚合 SQL，非物化。received_at 按北京时区切月。
SLA 达成 = handle_hours <= sla_standard_hours；耗时统计只计 handle_hours 非空。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import Numeric, and_, cast, func, select
from sqlalchemy.orm import Session

from app.models import Ticket, User
from app.services.sla.workday import BEIJING

_TYPES = ("Operation", "Bug_fix", "Demand", "Internal_task")
_HIST_BUCKETS = [(0, 4), (4, 8), (8, 24), (24, 72), (72, None)]


@dataclass(slots=True, frozen=True)
class KpiBlock:
    total: int
    by_type: dict[str, int]
    avg_handle_hours: float | None
    sla_rate: float | None


@dataclass(slots=True)
class TicketAnalytics:
    kpi: KpiBlock
    by_product_line: list[dict[str, Any]] = field(default_factory=list)
    by_assignee: list[dict[str, Any]] = field(default_factory=list)
    trend: list[dict[str, Any]] = field(default_factory=list)
    handle_hours_hist: list[dict[str, Any]] = field(default_factory=list)


def _base_filter(start, end, product_line):
    conds = [Ticket.deleted_at.is_(None)]
    if start is not None:
        conds.append(Ticket.received_at >= start)
    if end is not None:
        conds.append(Ticket.received_at < end)
    if product_line:
        conds.append(Ticket.product_line_code == product_line)
    return and_(*conds)


def compute_ticket_analytics(
    db: Session, *, start: datetime | None = None, end: datetime | None = None,
    product_line: str | None = None,
) -> TicketAnalytics:
    flt = _base_filter(start, end, product_line)

    total = db.execute(select(func.count()).select_from(Ticket).where(flt)).scalar() or 0

    type_rows = db.execute(
        select(Ticket.predicted_type, func.count()).where(flt).group_by(Ticket.predicted_type)
    ).all()
    by_type = {t: 0 for t in _TYPES}
    for t, c in type_rows:
        if t in by_type:
            by_type[t] = c

    avg_hh = db.execute(
        select(func.avg(Ticket.handle_hours)).where(and_(flt, Ticket.handle_hours.is_not(None)))
    ).scalar()
    avg_handle_hours = float(avg_hh) if avg_hh is not None else None

    # SLA 达成率：handle_hours 与 sla_standard_hours 都非空的工单里，handle<=std 占比
    sla_base = db.execute(
        select(func.count()).select_from(Ticket).where(
            and_(flt, Ticket.handle_hours.is_not(None), Ticket.sla_standard_hours.is_not(None))
        )
    ).scalar() or 0
    sla_ok = db.execute(
        select(func.count()).select_from(Ticket).where(
            and_(flt, Ticket.handle_hours.is_not(None), Ticket.sla_standard_hours.is_not(None),
                 Ticket.handle_hours <= Ticket.sla_standard_hours)
        )
    ).scalar() or 0
    sla_rate = (sla_ok / sla_base) if sla_base else None

    kpi = KpiBlock(total=total, by_type=by_type, avg_handle_hours=avg_handle_hours, sla_rate=sla_rate)

    # 产品线 × 类型
    pl_rows = db.execute(
        select(Ticket.product_line_code, Ticket.predicted_type, func.count())
        .where(flt).group_by(Ticket.product_line_code, Ticket.predicted_type)
    ).all()
    pl_map: dict[str, dict[str, Any]] = {}
    for pl, ptype, c in pl_rows:
        key = pl or "(未知)"
        d = pl_map.setdefault(key, {"product_line": key, "total": 0, "by_type": {t: 0 for t in _TYPES}})
        d["total"] += c
        if ptype in d["by_type"]:
            d["by_type"][ptype] += c
    by_product_line = sorted(pl_map.values(), key=lambda x: x["total"], reverse=True)[:10]

    # 处理人负载 top15
    as_rows = db.execute(
        select(Ticket.assigned_user_id, User.name, func.count(), func.avg(Ticket.handle_hours))
        .join(User, User.id == Ticket.assigned_user_id, isouter=True)
        .where(flt).group_by(Ticket.assigned_user_id, User.name)
    ).all()
    by_assignee = sorted(
        [
            {"user_id": uid, "name": name or "(未分配)", "total": c,
             "avg_handle_hours": float(avg) if avg is not None else None}
            for uid, name, c, avg in as_rows
        ],
        key=lambda x: x["total"], reverse=True,
    )[:15]

    # 月度趋势（北京时区切月）
    month_expr = func.to_char(func.timezone(BEIJING.key, Ticket.received_at), "YYYY-MM")
    tr_rows = db.execute(
        select(
            month_expr.label("m"), func.count(),
            func.percentile_cont(0.5).within_group(cast(Ticket.handle_hours, Numeric)),
            func.percentile_cont(0.9).within_group(cast(Ticket.handle_hours, Numeric)),
        ).where(flt).group_by(month_expr).order_by(month_expr)
    ).all()
    trend = [
        {"month": m, "total": c,
         "median_handle_hours": float(med) if med is not None else None,
         "p90_handle_hours": float(p90) if p90 is not None else None}
        for m, c, med, p90 in tr_rows
    ]

    # 耗时区间直方图
    hist = []
    for lo, hi in _HIST_BUCKETS:
        cond = [flt, Ticket.handle_hours.is_not(None), Ticket.handle_hours >= lo]
        if hi is not None:
            cond.append(Ticket.handle_hours < hi)
        c = db.execute(select(func.count()).select_from(Ticket).where(and_(*cond))).scalar() or 0
        label = f"{lo}-{hi}h" if hi is not None else f"{lo}h+"
        hist.append({"bucket": label, "count": c})

    return TicketAnalytics(
        kpi=kpi, by_product_line=by_product_line, by_assignee=by_assignee,
        trend=trend, handle_hours_hist=hist,
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_analytics.py -q`
Expected: PASS（3 passed）。若 SQLite 不支持 `func.timezone`/`percentile_cont`，测试用例的 trend 断言改为宽松校验（见注），或标记这些用例 PG-only；但 KPI/product_line/assignee 用例必须过。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/metrics/analytics.py backend/tests/unit/services/test_analytics.py
git commit -m "feat(analytics): 工单维度聚合服务 compute_ticket_analytics"
```

### Task 3: analytics API 端点

**Files:**
- Modify: `backend/app/api/metrics.py`（加 ticket-analytics 端点 + Out schema）
- Test: `backend/tests/unit/api/test_metrics_analytics.py`

**Interfaces:**
- Consumes: Task 2 的 `compute_ticket_analytics`
- Produces: `GET /api/metrics/ticket-analytics?start=&end=&product_line=`（require_supervisor）→ TicketAnalyticsOut

- [ ] **Step 1: 写失败测试**

Create `backend/tests/unit/api/test_metrics_analytics.py`：
```python
def test_ticket_analytics_requires_supervisor(client, member_token):
    r = client.get("/api/metrics/ticket-analytics", headers={"Authorization": f"Bearer {member_token}"})
    assert r.status_code == 403


def test_ticket_analytics_ok_for_supervisor(client, supervisor_token):
    r = client.get("/api/metrics/ticket-analytics", headers={"Authorization": f"Bearer {supervisor_token}"})
    assert r.status_code == 200
    body = r.json()
    assert "kpi" in body and "total" in body["kpi"]
    assert "by_product_line" in body and "trend" in body
```
（复用现有 conftest 的 client + member_token/supervisor_token fixture；若命名不同，按 test_admin_users.py 等现有测试的 fixture 名对齐。）

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/api/test_metrics_analytics.py -q`
Expected: FAIL（404 端点不存在）

- [ ] **Step 3: 实现端点**

在 `backend/app/api/metrics.py` 加（import `require_supervisor`、`compute_ticket_analytics`、`datetime`）：
```python
class KpiOut(BaseModel):
    total: int
    by_type: dict[str, int]
    avg_handle_hours: float | None
    sla_rate: float | None


class TicketAnalyticsOut(BaseModel):
    kpi: KpiOut
    by_product_line: list[dict]
    by_assignee: list[dict]
    trend: list[dict]
    handle_hours_hist: list[dict]


@router.get("/ticket-analytics", response_model=TicketAnalyticsOut)
def ticket_analytics(
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    product_line: str | None = Query(None),
    _user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> TicketAnalyticsOut:
    r = compute_ticket_analytics(db, start=start, end=end, product_line=product_line)
    return TicketAnalyticsOut(
        kpi=KpiOut(**asdict(r.kpi)),
        by_product_line=r.by_product_line,
        by_assignee=r.by_assignee,
        trend=r.trend,
        handle_hours_hist=r.handle_hours_hist,
    )
```
（`require_supervisor` from `app.api.deps.auth`；`asdict` 已在文件顶部 import。）

- [ ] **Step 4: 运行测试确认通过 + 重新生成类型**

Run: `cd backend && .venv/bin/pytest tests/unit/api/test_metrics_analytics.py -q && cd .. && make gen-types 2>&1 | tail -3`
Expected: PASS；types.ts 更新含 ticket-analytics。

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/metrics.py backend/tests/unit/api/test_metrics_analytics.py frontend/src/api/types.ts frontend/src/api/openapi.json
git commit -m "feat(analytics): GET /api/metrics/ticket-analytics (require_supervisor)"
```

### Task 4: 前端看板页 + recharts

**Files:**
- Modify: `frontend/package.json`（加 recharts 依赖）
- Create: `frontend/src/pages/analytics/AnalyticsPage.tsx`
- Modify: `frontend/src/main.tsx`（加路由 /analytics）
- Modify: `frontend/src/components/Layout.tsx`（导航加「统计看板」，roles supervisor+）
- Test: `frontend/src/pages/analytics/AnalyticsPage.test.tsx`

**Interfaces:**
- Consumes: Task 3 的 `/api/metrics/ticket-analytics`（types.ts 已生成）

- [ ] **Step 1: 装 recharts**

Run: `cd frontend && npm install recharts`
Expected: package.json + lock 更新，recharts 出现在 dependencies。

- [ ] **Step 2: 写页面**

Create `frontend/src/pages/analytics/AnalyticsPage.tsx`：用 TanStack Query 拉 `/api/metrics/ticket-analytics`，recharts 渲染四类卡片：
- KPI 行：total / 类型饼图(PieChart) / avg_handle_hours / sla_rate（百分比，<0.8 红色）
- 产品线×类型：堆叠柱状(BarChart，dataKey 每个类型一 Bar，stackId 同一个)
- 处理人负载：横向柱状(BarChart layout="vertical")，tooltip 显示 avg_handle_hours
- 月度趋势(LineChart total + median/p90 双轴) + 耗时直方图(BarChart)
- 顶部时间范围切换（最近3月/全部），产品线下拉（可选）
- 配色常量 `TYPE_COLORS = {Bug_fix:"#ef4444", Demand:"#3b82f6", Operation:"#eab308", Internal_task:"#6b7280"}`

（组件用现有 api client 的 get 方法，参照 WorkbenchPage.tsx 的 useQuery 用法；页面结构参照现有页的卡片布局与 Tailwind class。）

- [ ] **Step 3: 加路由 + 导航**

`frontend/src/main.tsx` 加 `<Route path="/analytics" element={<AnalyticsPage />} />`（RequireAuth 内，import AnalyticsPage）。
`frontend/src/components/Layout.tsx` navItems 加：
```tsx
  { to: "/analytics", label: "统计看板", icon: <某现有icon>, roles: ["supervisor", "admin"] },
```

- [ ] **Step 4: 写组件测试**

Create `frontend/src/pages/analytics/AnalyticsPage.test.tsx`：mock `/api/metrics/ticket-analytics` 返回样本数据，断言 KPI 数字、图表容器渲染（recharts 用 ResponsiveContainer 需给固定尺寸或 mock，参照现有测试对 Sparkline 的处理；断言文本/testid 出现即可）。

- [ ] **Step 5: 运行前端测试 + type-check + build**

Run: `cd frontend && npm run test -- AnalyticsPage && npm run type-check && npm run build 2>&1 | tail -5`
Expected: 测试通过，type-check 无错，build 成功。

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/pages/analytics/ frontend/src/main.tsx frontend/src/components/Layout.tsx
git commit -m "feat(analytics): 前端统计看板页 /analytics (recharts, supervisor+)"
```

### Task 5: SIT 部署 + 回填 + 端到端验证

**Files:** 无（部署 + 验证）

**Interfaces:**
- Consumes: Task 1-4 全部

- [ ] **Step 1: push + SIT 部署（含新迁移）**

```bash
git push origin <branch>
ssh root@sit "cd /data/hub-issue && git pull && docker compose -f deploy/docker-compose.sit.yml up -d --build"
ssh root@sit "cd /data/hub-issue && docker compose -f deploy/docker-compose.sit.yml run --rm backend alembic upgrade head"
```
Expected: 迁移 0023 应用成功。

- [ ] **Step 2: 回填历史工单**

回填脚本需连库，用 docker cp 进容器跑（SIT 代码烘进镜像）：
```bash
ssh root@sit "docker cp /data/hub-issue/scripts/backfill_handle_hours.py hub-issue-sit-backend:/app/bf.py"
ssh root@sit "docker exec -w /app hub-issue-sit-backend python3 /app/bf.py --dry-run"
ssh root@sit "docker exec -w /app hub-issue-sit-backend python3 /app/bf.py"
```
Expected: dry-run 显示 filled≈5417，正式跑后 committed。

- [ ] **Step 3: 验证回填 + API**

```bash
ssh root@sit "PGPASSWORD=difyai123456 psql -h 106.55.57.40 -U postgres -d ticket_hub_sit -tAc \"SELECT 'handle_hours非空='||count(*) FROM tickets WHERE handle_hours IS NOT NULL; SELECT 'sla_std非空='||count(*) FROM tickets WHERE sla_standard_hours IS NOT NULL; SELECT 'actual_resolved回填='||count(*) FROM tickets WHERE actual_resolved_at IS NOT NULL;\""
```
Expected: handle_hours≈5417、sla_standard≈5857、actual_resolved≈5417。

- [ ] **Step 4: 前端部署 + 看板验证**

```bash
ssh root@sit "cd /data/hub-issue && deploy/build-frontend.sh /data/hub-issue/frontend-dist"
```
用 supervisor 账号登录 `http://43.139.250.182/hub-issue/analytics`，确认：KPI 显示 total=5888、类型饼图(Operation 占多)、SLA 达成率有值、产品线柱状、处理人负载、月度趋势(4/5/6月)、耗时直方图。与 SQL 直查抽样核对一致。
Expected: 看板渲染真实数据，图表正常。

- [ ] **Step 5: 记录 memory**

更新 memory：工单统计看板已上线 SIT（/analytics，supervisor+，recharts）。

## Self-Review

- **Spec 覆盖**：回填列(§5.1)→Task1；聚合服务(§5.2)→Task2；API(§5.3)→Task3；前端 recharts 看板(§6)→Task4；验证(§7)→各任务测试+Task5。全覆盖。
- **占位符**：Task4 Step2 页面用文字描述 + 明确的 dataKey/配色常量/参照组件，非"TODO"；图表实现细节交实现者按 recharts API + 参照 WorkbenchPage，属合理（recharts 是标准库）。
- **类型一致**：`compute_ticket_analytics` 签名 Task2 定义、Task3 一致引用；TicketAnalytics/KpiBlock 字段 → KpiOut/TicketAnalyticsOut 一致；handle_hours/sla_standard_hours 列名全程一致。
- **口径**：SLA 达成 handle<=std、耗时只计非空、北京时区切月，服务与 spec 一致。
