# 研发人员维度看板 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 /analytics 看板加研发人员维度：研发三类工单(Bug_fix/Internal_task/Demand)按人员的工单量(类型堆叠) + 处理耗时(中位/平均)。

**Architecture:** 后端 `compute_ticket_analytics` 新增 `by_dev_staff` 聚合字段(两图共用一份数据) → API 响应加字段 → 前端 AnalyticsPage 底部加「研发人员维度」区(堆叠柱状 + 耗时表格)。纯增量，无新端点/迁移。

**Tech Stack:** FastAPI + SQLAlchemy；React + TypeScript + recharts。

## Global Constraints

- 研发三类：`_DEV_TYPES = ("Bug_fix", "Internal_task", "Demand")`（顺序固定，Internal_task 当前 0 条但保留）
- 人员维度用 `assigned_user_id`（NULL → name="(未分配)"），同现有 by_assignee
- 耗时只计 handle_hours 非空的工单；中位数用现有 `_percentile(sorted_values, p)`（入参必须**已排序**的 float 列表），平均用 SQL `func.avg`
- 研发区随现有 start/end/月份筛选一起生效（复用同一 `flt`，即 `_base_filter` 的结果）
- top 20 研发人员，按研发工单 total 降序
- 类型配色/标签复用前端 TYPE_COLORS / TYPE_LABELS；fmtHours 复用
- 改后端 API 后运行 `make gen-types` 更新 types.ts
- 现有文件：`backend/app/services/metrics/analytics.py`（有 `_TYPES`/`_percentile`/`_month_expr`/`_base_filter`/by_module/by_assignee 可参照）、`backend/app/api/metrics.py`（TicketAnalyticsOut）、`frontend/src/pages/analytics/AnalyticsPage.tsx`
- 测试 `_tk(db, **kw)` helper：默认 type=Raw/status=done/received_at=2026-04-01；传 predicted_type/assigned_user_id/handle_hours 覆盖
- 参考设计：`docs/superpowers/specs/2026-08-03-dev-staff-analytics-design.md`
- SIT 库 ticket_hub_sit @ 106.55.57.40；SIT 代码烘进镜像，验证用 docker exec 容器内 python3

---

### Task 1: 后端 by_dev_staff 聚合 + API 字段

**Files:**
- Modify: `backend/app/services/metrics/analytics.py`（TicketAnalytics 加字段 + 聚合逻辑）
- Modify: `backend/app/api/metrics.py`（TicketAnalyticsOut 加字段 + 透传）
- Test: `backend/tests/unit/services/test_analytics.py`（加 by_dev_staff 用例）

**Interfaces:**
- Produces: `TicketAnalytics.by_dev_staff: list[dict]`，每项 `{user_id, name, total, by_type:{Bug_fix,Internal_task,Demand}, median_handle_hours, avg_handle_hours}`；API 响应含同名字段

- [ ] **Step 1: 写失败测试**

在 `backend/tests/unit/services/test_analytics.py` 末尾加：
```python
def test_by_dev_staff(db_session):
    # 研发人员 A：2 Bug + 1 需求，耗时 [4, 8, 12] → 中位 8
    _tk(db_session, predicted_type="Bug_fix", assigned_user_id=1, handle_hours=Decimal("4"))
    _tk(db_session, predicted_type="Bug_fix", assigned_user_id=1, handle_hours=Decimal("8"))
    _tk(db_session, predicted_type="Demand", assigned_user_id=1, handle_hours=Decimal("12"))
    # 研发人员 B：1 需求
    _tk(db_session, predicted_type="Demand", assigned_user_id=2, handle_hours=Decimal("20"))
    # Operation 不算研发，不进 by_dev_staff
    _tk(db_session, predicted_type="Operation", assigned_user_id=1, handle_hours=Decimal("99"))
    db_session.add(User(id=1, feishu_uid="ou_a", name="研发甲", role="assignee"))
    db_session.add(User(id=2, feishu_uid="ou_b", name="研发乙", role="assignee"))
    db_session.commit()

    r = compute_ticket_analytics(db_session)
    a = next(x for x in r.by_dev_staff if x["user_id"] == 1)
    assert a["total"] == 3  # 不含 Operation
    assert a["by_type"]["Bug_fix"] == 2
    assert a["by_type"]["Demand"] == 1
    assert abs(a["median_handle_hours"] - 8.0) < 1e-6  # [4,8,12] 中位
    assert abs(a["avg_handle_hours"] - 8.0) < 1e-6  # (4+8+12)/3
    # 按 total 降序：甲(3) 在 乙(1) 前
    dev_ids = [x["user_id"] for x in r.by_dev_staff]
    assert dev_ids.index(1) < dev_ids.index(2)
```
测试顶部确认已 `from app.models import Ticket, User`（若只 import 了 Ticket，补 User）。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_analytics.py::test_by_dev_staff -q`
Expected: FAIL（AttributeError: 'TicketAnalytics' object has no attribute 'by_dev_staff'）

- [ ] **Step 3: 实现聚合**

在 `analytics.py` 顶部常量区（`_TYPES` 附近）加：
```python
_DEV_TYPES = ("Bug_fix", "Internal_task", "Demand")
```

TicketAnalytics dataclass 加字段（在 available_months 附近）：
```python
    by_dev_staff: list[dict[str, Any]] = field(default_factory=list)
```

在 `compute_ticket_analytics` 里，available_months 查询之后、return 之前加聚合（参照 by_assignee/trend 套路）：
```python
    # 研发人员维度：研发三类工单(Bug_fix/Internal_task/Demand)按处理人聚合
    # 工单数 + 类型构成
    dev_flt = and_(flt, Ticket.predicted_type.in_(_DEV_TYPES))
    dev_rows = db.execute(
        select(Ticket.assigned_user_id, User.name, Ticket.predicted_type, func.count())
        .join(User, User.id == Ticket.assigned_user_id, isouter=True)
        .where(dev_flt)
        .group_by(Ticket.assigned_user_id, User.name, Ticket.predicted_type)
    ).all()
    dev_map: dict[Any, dict[str, Any]] = {}
    for uid, name, ptype, c in dev_rows:
        d = dev_map.setdefault(
            uid,
            {
                "user_id": uid,
                "name": name or "(未分配)",
                "total": 0,
                "by_type": dict.fromkeys(_DEV_TYPES, 0),
                "median_handle_hours": None,
                "avg_handle_hours": None,
            },
        )
        d["total"] += c
        if ptype in d["by_type"]:
            d["by_type"][ptype] += c
    # 每人耗时（中位 Python 侧算，平均一并算）
    hh_rows = db.execute(
        select(Ticket.assigned_user_id, Ticket.handle_hours)
        .where(and_(dev_flt, Ticket.handle_hours.is_not(None)))
    ).all()
    hh_by_user: dict[Any, list[float]] = {}
    for uid, hh in hh_rows:
        hh_by_user.setdefault(uid, []).append(float(hh))
    for uid, values in hh_by_user.items():
        if uid in dev_map:
            sv = sorted(values)
            dev_map[uid]["median_handle_hours"] = _percentile(sv, 0.5)
            dev_map[uid]["avg_handle_hours"] = sum(sv) / len(sv)
    by_dev_staff = sorted(dev_map.values(), key=lambda x: x["total"], reverse=True)[:20]
```
把 `by_dev_staff=by_dev_staff` 加进 `return TicketAnalytics(...)`。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_analytics.py -q`
Expected: PASS（全部，含新 test_by_dev_staff）

- [ ] **Step 5: API 加字段**

`backend/app/api/metrics.py`：`TicketAnalyticsOut` 加 `by_dev_staff: list[dict]`；构造处加 `by_dev_staff=r.by_dev_staff,`。

- [ ] **Step 6: API 测试 + 跑通 + gen-types**

在 `backend/tests/unit/api/test_metrics_analytics.py` 的 supervisor 成功用例加断言 `assert "by_dev_staff" in body`。
Run: `cd backend && .venv/bin/pytest tests/unit/api/test_metrics_analytics.py -q && .venv/bin/ruff check app/services/metrics/analytics.py app/api/metrics.py && cd .. && make gen-types 2>&1 | tail -2`
Expected: PASS，lint 过，types.ts 更新含 by_dev_staff。

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/metrics/analytics.py backend/app/api/metrics.py backend/tests/unit/services/test_analytics.py backend/tests/unit/api/test_metrics_analytics.py frontend/src/api/types.ts frontend/src/api/openapi.json
git commit -m "feat(analytics): 后端 by_dev_staff 研发人员维度聚合(工单量+类型+中位/平均耗时)"
```

### Task 2: 前端研发人员维度区（堆叠柱状 + 耗时表格）

**Files:**
- Modify: `frontend/src/pages/analytics/AnalyticsPage.tsx`（底部加研发区）
- Test: `frontend/src/pages/analytics/AnalyticsPage.test.tsx`（mock by_dev_staff + 断言）

**Interfaces:**
- Consumes: Task 1 的 `data.by_dev_staff`

- [ ] **Step 1: 加研发人员维度区**

在 `AnalyticsBody` 里，④ 月度趋势+直方图那个 grid 之后，加「研发人员维度」区。先取数据（在 `AnalyticsBody` 顶部其他 const 附近）：
```tsx
  const devStaff = (data.by_dev_staff ?? []) as Array<Record<string, any>>;
  const devChartData = devStaff.map((row) => ({
    name: row.name,
    total: row.total,
    median_handle_hours: row.median_handle_hours,
    avg_handle_hours: row.avg_handle_hours,
    ...row.by_type,
  }));
```
区块 JSX（放在 ④ grid 结束的 `</div>` 之后、`AnalyticsBody` 最外层 `</div>` 之前）：
```tsx
      {/* ⑤ 研发人员维度（Bug修复/内部任务/需求 三类研发工单） */}
      <div>
        <div className="text-xs font-semibold text-hub-textSecondary mb-2">
          研发人员维度（Bug修复 / 需求 / 内部任务）
        </div>
        {devChartData.length === 0 ? (
          <div className="bg-white border border-hub-border rounded-[10px] p-4 text-xs text-hub-textFaint">
            暂无研发工单
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-4">
            {/* 工单量堆叠柱状 */}
            <div className="bg-white border border-hub-border rounded-[10px] p-4">
              <div className="text-[11.5px] text-hub-textMuted mb-2">研发工单量（按类型）</div>
              <div
                style={{ width: "100%", height: Math.max(200, devChartData.length * 30) }}
                data-testid="dev-staff-bar-chart"
              >
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={devChartData} layout="vertical" margin={{ left: 40 }}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis type="number" tick={{ fontSize: 11 }} />
                    <YAxis type="category" dataKey="name" tick={{ fontSize: 11 }} width={80} />
                    <Tooltip />
                    <Legend wrapperStyle={{ fontSize: 10.5 }} />
                    {HUB_TYPES.filter((t) => t !== "Operation").map((t) => (
                      <Bar
                        key={t}
                        dataKey={t}
                        name={TYPE_LABELS[t]}
                        stackId="dev"
                        fill={TYPE_COLORS[t]}
                      />
                    ))}
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
            {/* 耗时表格 */}
            <div className="bg-white border border-hub-border rounded-[10px] p-4">
              <div className="text-[11.5px] text-hub-textMuted mb-2">研发人员处理耗时</div>
              <table className="w-full text-[11.5px]" data-testid="dev-staff-table">
                <thead>
                  <tr className="text-hub-textMuted border-b border-hub-borderLight">
                    <th className="text-left py-1 font-medium">研发人员</th>
                    <th className="text-right py-1 font-medium">工单数</th>
                    <th className="text-right py-1 font-medium">中位耗时</th>
                    <th className="text-right py-1 font-medium">平均耗时</th>
                  </tr>
                </thead>
                <tbody>
                  {devChartData.map((d) => (
                    <tr key={d.name} className="border-b border-hub-borderLight/50">
                      <td className="py-1">{d.name}</td>
                      <td className="text-right py-1 font-mono">{d.total}</td>
                      <td className="text-right py-1 font-mono">{fmtHours(d.median_handle_hours)}</td>
                      <td className="text-right py-1 font-mono">{fmtHours(d.avg_handle_hours)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
```
（`HUB_TYPES.filter((t) => t !== "Operation")` 得到研发三类；HUB_TYPES/TYPE_LABELS/TYPE_COLORS/fmtHours 均文件内现有。）

- [ ] **Step 2: 加测试 mock + 断言**

`AnalyticsPage.test.tsx` 的 `sampleAnalytics` 加：
```tsx
  by_dev_staff: [
    {
      user_id: 1,
      name: "研发甲",
      total: 42,
      by_type: { Bug_fix: 30, Demand: 12, Internal_task: 0 },
      median_handle_hours: 180.0,
      avg_handle_hours: 224.2,
    },
    {
      user_id: 2,
      name: "研发乙",
      total: 15,
      by_type: { Bug_fix: 5, Demand: 10, Internal_task: 0 },
      median_handle_hours: 40.0,
      avg_handle_hours: 50.3,
    },
  ],
```
在 supervisor 渲染用例加断言：
```tsx
    expect(screen.getByTestId("dev-staff-bar-chart")).toBeInTheDocument();
    const devTable = screen.getByTestId("dev-staff-table");
    expect(devTable).toHaveTextContent("研发甲");
    expect(devTable).toHaveTextContent("224.2h");
    expect(devTable).toHaveTextContent("研发乙");
```

- [ ] **Step 3: 前端测试 + type-check + build**

Run: `cd frontend && npm run test -- AnalyticsPage 2>&1 | tail -6 && npm run type-check 2>&1 | tail -2 && npm run build 2>&1 | tail -2`
Expected: 测试通过，type-check 无错，build 成功。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/analytics/AnalyticsPage.tsx frontend/src/pages/analytics/AnalyticsPage.test.tsx
git commit -m "feat(analytics): 前端研发人员维度区(工单量堆叠柱状 + 耗时表格)"
```

### Task 3: SIT 部署 + 验证

**Files:** 无（部署 + 验证）

**Interfaces:**
- Consumes: Task 1-2

- [ ] **Step 1: 合并 main + push + SIT 部署**

（按项目惯例本任务直接合 main）
```bash
git checkout main && git merge <feature-branch> && git push origin main
ssh root@sit "cd /data/hub-issue && git pull origin main && docker compose -f deploy/docker-compose.sit.yml up -d --build backend && deploy/build-frontend.sh /data/hub-issue/frontend-dist"
```
Expected: 部署成功。

- [ ] **Step 2: 验证 by_dev_staff 真实数据**

```bash
ssh root@sit "docker exec -w /app hub-issue-sit-backend python3 -c '
from app.db import init_engine, get_session
from app.services.metrics.analytics import compute_ticket_analytics
init_engine(); db=next(get_session())
for d in compute_ticket_analytics(db).by_dev_staff[:8]:
    print(d[\"name\"], \"总\", d[\"total\"], d[\"by_type\"], \"中位\", d[\"median_handle_hours\"], \"平均\", round(d[\"avg_handle_hours\"],1) if d[\"avg_handle_hours\"] else None)
db.close()'"
```
Expected: 汪意/魏文浩/梁瑞然等研发人员，total + by_type(Bug/需求) + 中位/平均耗时，与 SQL 直查一致。

- [ ] **Step 3: SQL 交叉核对**

```bash
ssh root@sit "PGPASSWORD=difyai123456 psql -h 106.55.57.40 -U postgres -d ticket_hub_sit -tAc \"SELECT u.name, count(*), round(avg(t.handle_hours)::numeric,1) FROM tickets t JOIN users u ON u.id=t.assigned_user_id WHERE t.predicted_type IN ('Bug_fix','Internal_task','Demand') GROUP BY u.name ORDER BY count(*) DESC LIMIT 5;\""
```
Expected: 与 Step 2 服务输出的 total/平均耗时对得上。

- [ ] **Step 4: 记录 memory**

更新 memory：/analytics 加研发人员维度区（工单量堆叠 + 耗时中位/平均）。

## Self-Review

- **Spec 覆盖**：by_dev_staff 聚合(§4.1)→Task1；API(§4.2)→Task1；前端堆叠柱状+耗时表(§5)→Task2；验证(§6)→Task1/2 测试+Task3。全覆盖。
- **占位符**：无 TBD；代码块完整（聚合逻辑、JSX、测试均给全）。
- **类型一致**：`by_dev_staff` 字段名全程一致；每项结构(user_id/name/total/by_type/median_handle_hours/avg_handle_hours)在后端产出、API、前端消费、测试 mock 四处一致；`_percentile(sorted_values, p)` 入参已排序符合现有签名；`_DEV_TYPES` 定义后在聚合+前端 filter 呼应。
- **口径**：研发三类、assigned_user_id、handle_hours 非空、中位 Python 侧算，与设计和现有看板一致。
