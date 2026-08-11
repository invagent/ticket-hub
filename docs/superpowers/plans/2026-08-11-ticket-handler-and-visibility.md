# 工单「处理人」字段 + 可见性权限 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** 给 Ticket 加 `handler_user_id`(处理人,区别于路由分工的责任人 assigned_user_id),让处理人随入库/毕业分派/答复/转交流动,并做行级可见性(admin+主管看全部,其余只看自己的)。

**Architecture:** 后端加列(迁移0030,回填=assigned)+ 处理人四条写入路径 + list/detail 行级过滤 + 转交改写 handler。前端列表处理人列/筛选切到 handler,去责任人列/筛选。

**Tech Stack:** FastAPI + SQLAlchemy + Alembic(后端);React + TanStack Query + Vitest(前端);openapi 经 make gen-types 同步。

## Global Constraints

- 迁移 head=`0029_dispatch_engine`;新建 `0030_ticket_handler`,down_revision=`0029_dispatch_engine`,目录 `backend/migrations/versions/`。
- 责任人=`ticket.assigned_user_id`(不动语义);处理人=新增 `ticket.handler_user_id`(int nullable FK users.id index)。
- 可见性:role ∈ {admin, supervisor} 看全部;否则强制 `handler_user_id == 当前用户`。角色判定用 `app/api/deps/auth.py` 的 AuthedUser.role。
- 改 TicketSummary 响应 schema → 必须 `make gen-types` 并提交 openapi.json+types.ts。
- 后端测试:`cd backend && .venv/bin/pytest tests/unit/...`;前端:`cd frontend && npm run test/type-check/build`。
- 迁移应用后端:`.venv/bin/alembic upgrade head`(本地/CI 用 SQLite,SIT 用 PG)。

---

### Task 1: 迁移 0030 + 模型加 handler_user_id + 回填

**Files:**
- Create: `backend/migrations/versions/0030_ticket_handler.py`
- Modify: `backend/app/models.py`(Ticket,:435 assigned_user_id 之后)
- Test: `backend/tests/unit/test_ticket_handler_model.py`(Create)

**Interfaces:**
- Produces: `Ticket.handler_user_id: Mapped[int | None]`(FK users.id, index)。迁移回填存量 `handler_user_id = assigned_user_id`。

- [ ] **Step 1: 写模型字段**

`app/models.py` Ticket 内 `assigned_user_id` 定义之后加:
```python
    handler_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )  # 处理人（当前实际持有人；入库=责任人，毕业分派/答复/转交时流动）
```

- [ ] **Step 2: 写迁移**

`backend/migrations/versions/0030_ticket_handler.py`:
```python
"""ticket handler_user_id (处理人，区别于路由分工责任人 assigned_user_id)."""
from alembic import op
import sqlalchemy as sa

revision = "0030_ticket_handler"
down_revision = "0029_dispatch_engine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("handler_user_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_tickets_handler_user_id", "tickets", "users", ["handler_user_id"], ["id"]
    )
    op.create_index("ix_tickets_handler_user_id", "tickets", ["handler_user_id"])
    # 回填：存量处理人 = 责任人
    op.execute("UPDATE tickets SET handler_user_id = assigned_user_id WHERE handler_user_id IS NULL")


def downgrade() -> None:
    op.drop_index("ix_tickets_handler_user_id", table_name="tickets")
    op.drop_constraint("fk_tickets_handler_user_id", "tickets", type_="foreignkey")
    op.drop_column("tickets", "handler_user_id")
```

- [ ] **Step 3: 应用迁移 + 冒烟测试**

Run: `cd backend && .venv/bin/alembic upgrade head`
Expected: 0030 应用无误。

写 `test_ticket_handler_model.py`:
```python
from app.db import make_session
from app.models import Ticket

def test_ticket_has_handler_user_id(db_session):
    t = Ticket(short_code="TKT-H1", source_code="ksm", source_ticket_id="h1", type="Raw", status="received", title="x", assigned_user_id=None, handler_user_id=7)
    db_session.add(t); db_session.commit()
    got = db_session.query(Ticket).filter_by(short_code="TKT-H1").one()
    assert got.handler_user_id == 7
```

- [ ] **Step 4: 跑测试**

Run: `cd backend && .venv/bin/pytest tests/unit/test_ticket_handler_model.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/models.py backend/migrations/versions/0030_ticket_handler.py backend/tests/unit/test_ticket_handler_model.py
git commit -m "feat(model): Ticket.handler_user_id 处理人字段 + 迁移0030(回填=责任人)"
```

---

### Task 2: 入库时 handler 默认=责任人（5 个 ingester）

**Files:**
- Modify: `backend/app/services/ingest/{ksm,zhichi,escalation,feishu_ai,zammad}_ingester.py`(各 `ticket.assigned_user_id = route.assigned_user_ids[0]` 处)
- Test: `backend/tests/unit/`（找现有 zhichi ingest 测试追加,或建 test_ingest_handler_init.py）

**Interfaces:**
- Consumes: Task 1 的 handler_user_id。
- Produces: 入库后 `ticket.handler_user_id == ticket.assigned_user_id`（含都为 None 的情况）。

- [ ] **Step 1: 写失败测试(zhichi,用户实际来源)**

在 zhichi ingest 测试(定位现有文件,如 `tests/unit/test_zhichi_ingester*.py`)追加:路由命中时,入库 ticket 的 `handler_user_id == assigned_user_id`。若无现成 ingest 测试,建最小用例调用 ingester 后断言。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit -k "zhichi and handler" -v`
Expected: FAIL(handler_user_id 为 None,assigned 非 None)。

- [ ] **Step 3: 实现**

在 5 个 ingester 各自 `ticket.assigned_user_id = route.assigned_user_ids[0]` 那行之后,补一行(或在构造 Ticket 后统一设):
```python
            ticket.handler_user_id = ticket.assigned_user_id  # 处理人初始=责任人
```
注意:有的 ingester 是先构造 Ticket 再路由。统一原则——**路由写完 assigned_user_id 后,让 handler=assigned**。若某 ingester assigned 可能为 None,handler 也随之 None(符合预期)。

- [ ] **Step 4: 跑测试确认通过 + 全 ingest 回归**

Run: `cd backend && .venv/bin/pytest tests/unit -k "ingest or ingester" -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/ingest/ backend/tests/unit/
git commit -m "feat(ingest): 入库时处理人默认=责任人(5个ingester)"
```

---

### Task 3: 毕业分派把处理人写进 ticket + helper

**Files:**
- Modify: `backend/app/services/hub_issues/op_status.py`(加 `set_hub_tickets_handler`)、`backend/app/services/hub_issues/creator.py`(dispatch 分支)
- Test: `backend/tests/unit/services/test_ticket_handler_flow.py`(Create)

**Interfaces:**
- Produces: `set_hub_tickets_handler(db, hub, user_id: int) -> int` — 把 hub 下所有未删关联 ticket 的 `handler_user_id` 置为 user_id,返回条数。creator 毕业分派命中时调用。

- [ ] **Step 1: 写失败测试**

```python
from app.models import HubIssue, Ticket
from app.services.hub_issues.op_status import set_hub_tickets_handler

def test_set_hub_tickets_handler(db_session):
    hub = HubIssue(id=600, short_code="HUB-000600", type="Operation", title="t", status="created")
    db_session.add(hub); db_session.flush()
    db_session.add(Ticket(id=900, short_code="TKT-000900", source_code="ksm", source_ticket_id="k900", type="Raw", status="received", title="x", hub_issue_id=600, assigned_user_id=3, handler_user_id=3))
    db_session.commit()
    n = set_hub_tickets_handler(db_session, hub, 42); db_session.commit()
    assert n == 1
    assert db_session.get(Ticket, 900).handler_user_id == 42
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_ticket_handler_flow.py -v`
Expected: FAIL(ImportError)。

- [ ] **Step 3: 实现 helper**

`op_status.py` 加(仿 record_ticket_action 的遍历):
```python
def set_hub_tickets_handler(db: Session, hub: HubIssue, user_id: int) -> int:
    """把 hub 下所有未删关联 ticket 的处理人(handler_user_id)置为 user_id。不 commit。返回条数。"""
    from app.models import Ticket
    tickets = db.query(Ticket).filter(Ticket.hub_issue_id == hub.id, Ticket.deleted_at.is_(None)).all()
    for t in tickets:
        t.handler_user_id = user_id
    return len(tickets)
```

`creator.py` dispatch 分支(现 `hub.op_handler_user_id = dr.user_id`)之后加:
```python
        if dr.user_id is not None:
            hub.op_handler_user_id = dr.user_id
            set_hub_tickets_handler(db, hub, dr.user_id)  # 处理人随分派流动到工单
```
import：`from app.services.hub_issues.op_status import set_hub_tickets_handler`（creator 已 import op_status 相关?若无则加）。dispatch 无结果时不调用 → handler 保持责任人(入库已设)。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_ticket_handler_flow.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/hub_issues/op_status.py backend/app/services/hub_issues/creator.py backend/tests/unit/services/test_ticket_handler_flow.py
git commit -m "feat(dispatch): 毕业分派把处理人写进关联工单"
```

---

### Task 4: 答复把处理人改成答复者

**Files:**
- Modify: `backend/app/api/hub_issues.py`(author_reply_endpoint)
- Test: `backend/tests/unit/test_hub_issue_reply_api.py`(追加)

**Interfaces:**
- Consumes: `set_hub_tickets_handler`(Task 3)。
- Produces: POST /reply 成功后,hub 下所有关联 ticket 的 handler_user_id == 答复者 user_id。

- [ ] **Step 1: 写失败测试**

```python
def test_reply_sets_handler_to_author(app_client, reply_world):
    from app.models import Ticket
    r = app_client.post("/api/hub-issues/90/reply", json={"content": "已处理"}, headers=_bearer(2))
    assert r.status_code == 200
    # user 2 = carol；关联 ticket 300 处理人应变 carol(uid 2)
    assert reply_world.get(Ticket, 300).handler_user_id == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/test_hub_issue_reply_api.py -k handler -v`
Expected: FAIL。

- [ ] **Step 3: 实现**

`author_reply_endpoint` 里,现有 `record_ticket_action(...)` 之后(同一 Operation 分支内)加:
```python
        set_hub_tickets_handler(db, hub, user.user_id)  # 答复者成为处理人
```
import set_hub_tickets_handler。保持在 `db.commit()` 之前。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/test_hub_issue_reply_api.py -v`
Expected: PASS(不回归)。

- [ ] **Step 5: 提交**

```bash
git add backend/app/api/hub_issues.py backend/tests/unit/test_hub_issue_reply_api.py
git commit -m "feat(reply): 答复者成为工单处理人(hub下所有单同步)"
```

---

### Task 5: 转交改写 handler_user_id + 放开目标角色

**Files:**
- Modify: `backend/app/services/supervisor/manual_assign.py`
- Test: `backend/tests/unit/`（现有 manual_assign / supervisor assign 测试）

**Interfaces:**
- Produces: `POST /api/supervisor/assign` 改写 `handler_user_id`(不再改 assigned_user_id);目标用户放开到任意 is_active 用户(含 member)。

- [ ] **Step 1: 写失败测试**

在现有 assign 测试文件追加:assign 后目标 ticket 的 `handler_user_id == 目标 uid`(且 assigned_user_id 不变);member 角色可作为目标(不再 400)。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit -k "assign and handler" -v`
Expected: FAIL。

- [ ] **Step 3: 实现**

`manual_assign.py`:
- 目标校验去掉 `_ASSIGNABLE_ROLES` 白名单(:67-68),仅保留 `is_active` 校验(:65-66)。
- `update(Ticket).values(assigned_user_id=...)` → `.values(handler_user_id=req.assigned_user_id)`(:93)。
- `prev = ticket.assigned_user_id` → `prev = ticket.handler_user_id`(:89)。
- status_history reason 改 `f"转交处理人 to user_id={...} by user_id={...}"`,metadata key 改 handler_user_id/prev_handler_user_id。
- 请求体字段名 `assigned_user_id` 可保留(前端已传该名),仅语义改为"目标处理人";或加注释说明。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit -k "assign or manual_assign" -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/supervisor/manual_assign.py backend/tests/unit/
git commit -m "feat(transfer): 转交改处理人(handler_user_id)+放开目标角色到member"
```

---

### Task 6: 行级可见性 + handler 筛选 + 响应字段

**Files:**
- Modify: `backend/app/repositories/ticket.py`(list_paginated)、`backend/app/api/tickets.py`(TicketSummary + list_tickets + get_ticket + get_ticket_history)
- Test: `backend/tests/unit/test_tickets_api.py`(追加)

**Interfaces:**
- Produces: `list_paginated(..., visible_to_user_id: int | None = None, handler_user_ids: list[int] | None = None)`;TicketSummary 加 `handler_user_id` + `handler_user_name`;非 admin/主管强制只见自己处理的。

- [ ] **Step 1: 写失败测试**

```python
def test_member_sees_only_own_handled(app_client, world):
    # member 角色登录,只看到 handler_user_id==自己 的工单
    r = app_client.get("/api/tickets", headers=_bearer(9, name="mm", role="member"))
    ids = {it["id"] for it in r.json()["items"]}
    # world 里需有 handler=9 与 handler!=9 的工单,断言只含前者
    ...

def test_admin_sees_all(app_client, world):
    r = app_client.get("/api/tickets", headers=_bearer(1, name="a", role="admin"))
    assert r.json()["total"] >= 4

def test_summary_exposes_handler(app_client, world):
    r = app_client.get("/api/tickets", headers=_bearer(1, name="a", role="admin"))
    it = next(x for x in r.json()["items"] if x["hub_issue_id"])
    assert "handler_user_id" in it and "handler_user_name" in it

def test_filter_handler_user_ids(app_client, world):
    r = app_client.get("/api/tickets?handler_user_ids=3", headers=_bearer(1, name="a", role="admin"))
    assert all(it["handler_user_id"] == 3 for it in r.json()["items"])

def test_member_get_others_ticket_404(app_client, world):
    r = app_client.get("/api/tickets/<id_handled_by_other>", headers=_bearer(9, name="mm", role="member"))
    assert r.status_code == 404
```
(world fixture 需补 handler_user_id 值:给几个 ticket 设不同 handler。)

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/test_tickets_api.py -k "handler or member or admin_sees" -v`
Expected: FAIL。

- [ ] **Step 3: 实现**

`repositories/ticket.py list_paginated` 签名加 `visible_to_user_id: int | None = None` 和 `handler_user_ids: list[int] | None = None`:
```python
        if visible_to_user_id is not None:
            base = base.where(Ticket.handler_user_id == visible_to_user_id)
            count_base = count_base.where(Ticket.handler_user_id == visible_to_user_id)
        if handler_user_ids:
            base = base.where(Ticket.handler_user_id.in_(handler_user_ids))
            count_base = count_base.where(Ticket.handler_user_id.in_(handler_user_ids))
```

`api/tickets.py`:
- `TicketSummary` 加 `handler_user_id: int | None = None` + `handler_user_name: str | None = None`。
- `list_tickets`:注入登录用户(把 `_user` 改成 `user: AuthedUser = Depends(require_user)`),`is_priv = user.role in ("admin","supervisor")`;调 list_paginated 传 `visible_to_user_id = None if is_priv else user.user_id`,`handler_user_ids=<query param>`。加 query 参数 `handler_user_ids: list[int] | None = Query(None)`。
- batch-load handler 名:hub 名批查里把 handler_user_id 并入 user_ids 集合,`_to_summary` 里 `s.handler_user_id = t.handler_user_id; s.handler_user_name = user_name_map.get(t.handler_user_id)`。
- `get_ticket` / `get_ticket_history`:注入 user,非 priv 且 `ticket.handler_user_id != user.user_id` → `raise HTTPException(404)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/test_tickets_api.py -q`
Expected: PASS。

- [ ] **Step 5: gen-types + 提交**

```bash
cd .. && make gen-types
git add backend/app/api/tickets.py backend/app/repositories/ticket.py backend/tests/unit/test_tickets_api.py frontend/src/api/openapi.json frontend/src/api/types.ts
git commit -m "feat(tickets): 处理人响应字段 + 行级可见性 + handler 筛选"
```

---

### Task 7: 前端列表处理人列/筛选切到 handler

**Files:**
- Modify: `frontend/src/pages/tickets/TicketsListPage.tsx`
- Test: `frontend/src/pages/tickets/TicketsListPage.test.tsx`

**Interfaces:**
- Consumes: TicketSummary.handler_user_id/handler_user_name + 后端 handler_user_ids 过滤。
- Produces: 处理人列读 handler_user_name;筛选绑 handler_user_ids;去掉责任人列与 assigned 筛选。

- [ ] **Step 1: 写失败测试**

```tsx
  it("处理人列显示 handler_user_name", async () => {
    server.use(http.get("*/api/tickets", () => HttpResponse.json({
      ...sample,
      items: [{ ...sample.items[0], assigned_user_name: "杨慧莉", handler_user_id: 42, handler_user_name: "苗一琳" }],
    })));
    renderPage();
    expect(await screen.findByText("TKT-1")).toBeInTheDocument();
    // 处理人列显示苗一琳(handler),不是杨慧莉(责任人)
    const table = screen.getByRole("table");
    expect(Array.from(table.querySelectorAll("tbody td")).some((td) => td.textContent?.includes("苗一琳"))).toBe(true);
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run TicketsListPage -t 处理人列`
Expected: FAIL。

- [ ] **Step 3: 实现**

- 处理人列 `accessorKey` 改 `handler_user_name`,回落 `#${handler_user_id}`;头像字母取 handler_user_name。
- 去掉责任人列(原「处理人」列若是唯一一列则直接改字段;spec 说去责任人列——即不再单列 assigned)。
- 筛选:MultiUserSelect value 绑 `handlerUserIds`(URL `handler_user_ids`),onChange 写 `handler_user_ids`;请求参数 `handler_user_ids`。删除 `assigned_user_ids` 筛选相关 state/参数。
- 「仅未分配」若基于 assigned,可改为基于 handler 或保留(按现状,unassigned_only 仍走 assigned;本任务不强制改,注释说明)。

- [ ] **Step 4: 跑测试确认通过 + 全量**

Run: `cd frontend && npm run type-check && npx vitest run TicketsListPage`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/tickets/TicketsListPage.tsx frontend/src/pages/tickets/TicketsListPage.test.tsx
git commit -m "feat(tickets-list): 处理人列/筛选切到 handler,去责任人列"
```

---

### Task 8: 全量验证

- [ ] **Step 1: 后端 lint + 全量单测**

Run: `cd backend && make lint && .venv/bin/pytest tests/unit -q`
Expected: 我的文件 lint clean;单测全绿(注意既有测试若断言旧 assign 行为需同步更新)。

- [ ] **Step 2: 类型同步**

Run: `make check-types`(根)
Expected: openapi/types 同步无 drift。

- [ ] **Step 3: 前端全量**

Run: `cd frontend && npm run type-check && npm run test && npm run build`
Expected: 全绿。

- [ ] **Step 4: 提交(若有修正)**

```bash
git add -A && git commit -m "chore: 处理人+可见性 通过全量校验"
```

---

## Self-Review

**Spec coverage:**
- handler_user_id 字段 + 迁移回填 → Task 1 ✅
- 入库默认=责任人 → Task 2 ✅
- 毕业分派写 handler → Task 3 ✅
- 答复者变处理人(hub下所有单) → Task 4 ✅
- 转交改 handler + 放开 member → Task 5 ✅
- 可见性(admin/主管全部,其余自己)+ handler 筛选 + 响应字段 → Task 6 ✅
- 前端处理人列/筛选切换、去责任人列/筛选 → Task 7 ✅
- 验证 → Task 8 ✅

**Placeholder scan:** Task 2/5/6 的测试用例定位"现有测试文件"处,执行时先读实际文件名再追加(已注明)。world fixture 需补 handler_user_id 值(Task 6 Step1 注明)。

**Type consistency:** `set_hub_tickets_handler(db, hub, user_id)` 在 Task 3 定义、Task 4 复用一致;`handler_user_id`/`handler_user_name`/`handler_user_ids` 三处命名贯穿后端 schema、过滤参数、前端一致。

**风险提示:** 既有 manual_assign / assign 测试断言的是 assigned_user_id 改变;Task 5 改成 handler 后,这些旧断言会失败——执行 Task 5/8 时需同步更新既有测试为断言 handler_user_id。
