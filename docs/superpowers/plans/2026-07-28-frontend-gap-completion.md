# 前后端缺口补全 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补全 3 个前后端未打通的缺口——单条/批量手动指派、relink 搜索重关联、hub 详情页补 3 个协同动作。

**Architecture:** 后端新增 1 个指派端点（镜像 reroute 分层）+ 给 hub-issues list 加 search 参数；前端加指派 UI（详情页+列表页）、relink 弹窗、抽共享协同动作组件。无数据库迁移。

**Tech Stack:** FastAPI + SQLAlchemy（backend）；React 18 + TypeScript + TanStack Query（frontend）。

## Global Constraints

- 后端改动后必须 `make gen-types` 同步 `frontend/src/api/openapi.json` + `types.ts`，否则 CI `make check-types` 失败
- 后端 `make lint`（ruff + mypy）+ `make unit`（覆盖率 ≥70%）
- 前端 `npm run type-check` + `npm run test` + `npm run build`
- 指派服务只 flush，端点内 `db.commit()`（镜像 reroute）
- `changed_by` 约定：手动指派用 `system:manual_assign`
- 指派对象角色限 `{assignee, supervisor, admin}`
- 无数据库迁移（复用 `assigned_user_id`，只加 query 参数）
- 测试隔离：`tests/conftest.py` 已清空 GLM/DASHSCOPE key，指派/search 不触发 LLM

---

## Task 1: 后端 — 手动指派服务 + 端点

**Files:**
- Create: `backend/app/services/supervisor/manual_assign.py`
- Modify: `backend/app/api/supervisor.py`（加 body/response schema + endpoint，靠近 reroute at :266）
- Test: `backend/tests/unit/services/test_manual_assign.py`
- Test: `backend/tests/unit/api/test_supervisor_assign.py`

**Interfaces:**
- Consumes: `TicketRepository.list_by_ids(ticket_ids) -> list[Ticket]`（`app/repositories/ticket.py:69`）；`UserRepository.get(user_id) -> User | None`（`app/repositories/user.py:62`）；`StatusHistoryRepository.record(...)`（`app/repositories/status_history.py:32`）；`Ticket.assigned_user_id`、`Ticket.status`、`Ticket.short_code`
- Produces:
  - `AssignRequest(ticket_ids: list[int], assigned_user_id: int, operator_user_id: int)`（frozen dataclass）
  - `AssignItemResult(ticket_id: int, short_code: str, success: bool, prev_assigned_user_id: int | None, message: str)`
  - `AssignResult(results: list[AssignItemResult], assigned_count: int, not_found_count: int)`
  - `ManualAssignService(db).assign(req: AssignRequest) -> AssignResult`
  - `TargetUserInvalidError`（Exception，端点映射 422）
  - HTTP：`POST /api/supervisor/assign`，body `{ticket_ids: [int](1-50), assigned_user_id: int}`，response `{results: [...], assigned_count, not_found_count}`

- [ ] **Step 1: 写 service 失败测试**

创建 `backend/tests/unit/services/test_manual_assign.py`。参考同目录 `test_reroute*.py` 的 fixture 风格（用 in-memory session、造 User/Ticket）。

```python
import pytest
from app.models import Ticket, User
from app.repositories.status_history import StatusHistoryRepository
from app.services.supervisor.manual_assign import (
    AssignRequest,
    ManualAssignService,
    TargetUserInvalidError,
)


def _mk_user(db, *, name: str, role: str, is_active: bool = True) -> User:
    u = User(feishu_uid=f"fs-{name}", name=name, role=role, is_active=is_active)
    db.add(u)
    db.flush()
    return u


def _mk_ticket(db, *, short_code: str, assigned_user_id=None) -> Ticket:
    t = Ticket(
        type="Raw",
        source_code="ksm",
        source_ticket_id=f"src-{short_code}",
        short_code=short_code,
        title="t",
        body="b",
        status="received",
        assigned_user_id=assigned_user_id,
    )
    db.add(t)
    db.flush()
    return t


def test_assign_single_success(db_session):
    op = _mk_user(db_session, name="op", role="supervisor")
    target = _mk_user(db_session, name="dev", role="assignee")
    t = _mk_ticket(db_session, short_code="T-1")

    res = ManualAssignService(db_session).assign(
        AssignRequest(ticket_ids=[t.id], assigned_user_id=target.id, operator_user_id=op.id)
    )

    assert res.assigned_count == 1
    assert res.not_found_count == 0
    assert res.results[0].success is True
    assert res.results[0].prev_assigned_user_id is None
    db_session.flush()
    assert db_session.get(Ticket, t.id).assigned_user_id == target.id


def test_assign_records_status_history(db_session):
    op = _mk_user(db_session, name="op", role="admin")
    target = _mk_user(db_session, name="dev", role="assignee")
    t = _mk_ticket(db_session, short_code="T-2")

    ManualAssignService(db_session).assign(
        AssignRequest(ticket_ids=[t.id], assigned_user_id=target.id, operator_user_id=op.id)
    )

    rows = StatusHistoryRepository(db_session).list_for_entity("ticket", t.id)
    assert any(r.changed_by == "system:manual_assign" for r in rows)


def test_assign_target_role_not_allowed(db_session):
    op = _mk_user(db_session, name="op", role="supervisor")
    member = _mk_user(db_session, name="m", role="member")
    t = _mk_ticket(db_session, short_code="T-3")

    with pytest.raises(TargetUserInvalidError):
        ManualAssignService(db_session).assign(
            AssignRequest(ticket_ids=[t.id], assigned_user_id=member.id, operator_user_id=op.id)
        )


def test_assign_target_inactive(db_session):
    op = _mk_user(db_session, name="op", role="supervisor")
    dead = _mk_user(db_session, name="x", role="assignee", is_active=False)
    t = _mk_ticket(db_session, short_code="T-4")

    with pytest.raises(TargetUserInvalidError):
        ManualAssignService(db_session).assign(
            AssignRequest(ticket_ids=[t.id], assigned_user_id=dead.id, operator_user_id=op.id)
        )


def test_assign_target_not_found(db_session):
    op = _mk_user(db_session, name="op", role="supervisor")
    t = _mk_ticket(db_session, short_code="T-5")

    with pytest.raises(TargetUserInvalidError):
        ManualAssignService(db_session).assign(
            AssignRequest(ticket_ids=[t.id], assigned_user_id=99999, operator_user_id=op.id)
        )


def test_assign_partial_ticket_not_found(db_session):
    op = _mk_user(db_session, name="op", role="supervisor")
    target = _mk_user(db_session, name="dev", role="assignee")
    t = _mk_ticket(db_session, short_code="T-6")

    res = ManualAssignService(db_session).assign(
        AssignRequest(ticket_ids=[t.id, 88888], assigned_user_id=target.id, operator_user_id=op.id)
    )
    assert res.assigned_count == 1
    assert res.not_found_count == 1
    by_id = {r.ticket_id: r for r in res.results}
    assert by_id[t.id].success is True
    assert by_id[88888].success is False
```

注意：确认 `StatusHistoryRepository` 有 `list_for_entity` 方法；若方法名不同，改用查询 `StatusHistory` 表按 entity_id 过滤。造 Ticket 时字段以 `ck_tickets_type_fields` 约束为准（Raw 需 source_code+source_ticket_id），若约束报错按报错补字段。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_manual_assign.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.services.supervisor.manual_assign'`

- [ ] **Step 3: 写 service 实现**

创建 `backend/app/services/supervisor/manual_assign.py`：

```python
"""ManualAssignService — 主管手动把工单直接指派给指定处理人（绕过 Router）。

对每个 ticket：
  1. 校验 ticket 存在
  2. update(Ticket).values(assigned_user_id=target)
  3. 写 status_history 审计（from==to，changed_by=system:manual_assign）
  4. 返回每条结果

目标用户先统一校验一次（存在 + is_active + 角色允许），不合法整批拒绝。
调用方（API 端点）负责 db.commit()。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import Ticket
from app.repositories.status_history import StatusHistoryRepository
from app.repositories.ticket import TicketRepository
from app.repositories.user import UserRepository

logger = get_logger(__name__)

_ASSIGNABLE_ROLES = frozenset({"assignee", "supervisor", "admin"})


class TargetUserInvalidError(Exception):
    """目标用户不存在 / 已停用 / 角色不允许被指派。"""


@dataclass(slots=True, frozen=True)
class AssignRequest:
    ticket_ids: list[int]
    assigned_user_id: int
    operator_user_id: int


@dataclass(slots=True, frozen=True)
class AssignItemResult:
    ticket_id: int
    short_code: str
    success: bool
    prev_assigned_user_id: int | None = None
    message: str = ""


@dataclass(slots=True, frozen=True)
class AssignResult:
    results: list[AssignItemResult]
    assigned_count: int
    not_found_count: int


class ManualAssignService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def assign(self, req: AssignRequest) -> AssignResult:
        user_repo = UserRepository(self._db)
        target = user_repo.get(req.assigned_user_id)
        if target is None or not target.is_active:
            raise TargetUserInvalidError(f"目标用户 {req.assigned_user_id} 不存在或已停用")
        if target.role not in _ASSIGNABLE_ROLES:
            raise TargetUserInvalidError(
                f"用户 {target.name}（{target.role}）不可被指派工单"
            )

        ticket_repo = TicketRepository(self._db)
        history_repo = StatusHistoryRepository(self._db)
        tickets = ticket_repo.list_by_ids(req.ticket_ids)
        found = {t.id: t for t in tickets}
        results: list[AssignItemResult] = []

        for tid in req.ticket_ids:
            if tid not in found:
                results.append(
                    AssignItemResult(
                        ticket_id=tid,
                        short_code="",
                        success=False,
                        message=f"工单 {tid} 不存在或已删除",
                    )
                )
                continue

            ticket = found[tid]
            prev = ticket.assigned_user_id
            self._db.execute(
                update(Ticket)
                .where(Ticket.id == ticket.id)
                .values(assigned_user_id=req.assigned_user_id)
            )
            history_repo.record(
                entity_type="ticket",
                entity_id=ticket.id,
                from_status=ticket.status,
                to_status=ticket.status,
                changed_by="system:manual_assign",
                reason=f"manual assign to user_id={req.assigned_user_id} by user_id={req.operator_user_id}",
                metadata={
                    "operator_user_id": req.operator_user_id,
                    "assigned_user_id": req.assigned_user_id,
                    "prev_assigned_user_id": prev,
                },
            )
            logger.info(
                "supervisor_manual_assign",
                ticket_id=ticket.id,
                assigned_user_id=req.assigned_user_id,
                prev_assigned_user_id=prev,
                operator_user_id=req.operator_user_id,
            )
            results.append(
                AssignItemResult(
                    ticket_id=ticket.id,
                    short_code=ticket.short_code,
                    success=True,
                    prev_assigned_user_id=prev,
                    message=f"已指派给用户 {req.assigned_user_id}",
                )
            )

        self._db.flush()
        assigned_count = sum(1 for r in results if r.success)
        return AssignResult(
            results=results,
            assigned_count=assigned_count,
            not_found_count=len(results) - assigned_count,
        )
```

- [ ] **Step 4: 跑 service 测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/services/test_manual_assign.py -v`
Expected: PASS（6 passed）。若 `list_for_entity` 不存在，先修测试的查询方式再跑。

- [ ] **Step 5: 写端点失败测试**

创建 `backend/tests/unit/api/test_supervisor_assign.py`。参考 `test_supervisor*.py`（用 TestClient + auth override 造 supervisor 身份）。

```python
def test_assign_endpoint_success(client, supervisor_auth, make_user, make_ticket):
    target = make_user(role="assignee")
    t = make_ticket(short_code="A-1")

    resp = client.post(
        "/api/supervisor/assign",
        json={"ticket_ids": [t.id], "assigned_user_id": target.id},
        headers=supervisor_auth,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["assigned_count"] == 1
    assert body["results"][0]["success"] is True


def test_assign_endpoint_role_not_allowed_422(client, supervisor_auth, make_user, make_ticket):
    member = make_user(role="member")
    t = make_ticket(short_code="A-2")
    resp = client.post(
        "/api/supervisor/assign",
        json={"ticket_ids": [t.id], "assigned_user_id": member.id},
        headers=supervisor_auth,
    )
    assert resp.status_code == 422


def test_assign_endpoint_requires_supervisor(client, member_auth, make_user, make_ticket):
    target = make_user(role="assignee")
    t = make_ticket(short_code="A-3")
    resp = client.post(
        "/api/supervisor/assign",
        json={"ticket_ids": [t.id], "assigned_user_id": target.id},
        headers=member_auth,
    )
    assert resp.status_code == 403
```

注意：fixture 名（`client`/`supervisor_auth`/`member_auth`/`make_user`/`make_ticket`）以 `tests/conftest.py` 或既有 `test_supervisor*.py` 实际提供的为准；对不上就复用既有测试里的造数方式。

- [ ] **Step 6: 跑端点测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/api/test_supervisor_assign.py -v`
Expected: FAIL —— 404（endpoint 未定义）

- [ ] **Step 7: 写端点实现**

在 `backend/app/api/supervisor.py` 中，`RerouteResponse`（:150-153）之后加 schema，`reroute_tickets`（:266-300）之后加 endpoint：

```python
class AssignBody(BaseModel):
    ticket_ids: list[int] = Field(..., min_length=1, max_length=50)
    assigned_user_id: int


class AssignItemOut(BaseModel):
    ticket_id: int
    short_code: str
    success: bool
    prev_assigned_user_id: int | None
    message: str


class AssignResponse(BaseModel):
    results: list[AssignItemOut]
    assigned_count: int
    not_found_count: int
```

endpoint（放在 reroute endpoint 之后）：

```python
@router.post("/assign", response_model=AssignResponse)
def assign_tickets(
    body: AssignBody,
    user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> AssignResponse:
    try:
        result = ManualAssignService(db).assign(
            AssignRequest(
                ticket_ids=body.ticket_ids,
                assigned_user_id=body.assigned_user_id,
                operator_user_id=user.user_id,
            )
        )
    except TargetUserInvalidError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    db.commit()
    logger.info(
        "supervisor_assign",
        ticket_ids=body.ticket_ids,
        assigned_user_id=body.assigned_user_id,
        assigned_count=result.assigned_count,
        operator_user_id=user.user_id,
    )
    return AssignResponse(
        results=[
            AssignItemOut(
                ticket_id=r.ticket_id,
                short_code=r.short_code,
                success=r.success,
                prev_assigned_user_id=r.prev_assigned_user_id,
                message=r.message,
            )
            for r in result.results
        ],
        assigned_count=result.assigned_count,
        not_found_count=result.not_found_count,
    )
```

在文件顶部 import 区加：

```python
from app.services.supervisor.manual_assign import (
    AssignRequest,
    ManualAssignService,
    TargetUserInvalidError,
)
```

确认 `HTTPException` 已 import（reroute 附近应已有；没有则从 `fastapi` 补）。

- [ ] **Step 8: 跑端点测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/api/test_supervisor_assign.py -v`
Expected: PASS（3 passed）

- [ ] **Step 9: lint + gen-types**

Run: `cd backend && make lint`
Expected: ruff + mypy clean

Run: `cd .. && make gen-types`
Expected: `frontend/src/api/openapi.json` + `types.ts` 更新（含 `/api/supervisor/assign`）

- [ ] **Step 10: Commit**

```bash
git add backend/app/services/supervisor/manual_assign.py backend/app/api/supervisor.py backend/tests/unit/services/test_manual_assign.py backend/tests/unit/api/test_supervisor_assign.py frontend/src/api/openapi.json frontend/src/api/types.ts
git commit -m "feat(supervisor): 手动指派工单端点 POST /api/supervisor/assign"
```

---

## Task 2: 后端 — hub-issues list 加 search 参数

**Files:**
- Modify: `backend/app/repositories/ticket.py:189-219`（`HubIssueRepository.list_paginated`）
- Modify: `backend/app/api/hub_issues.py:129-149`（`list_hub_issues`）
- Test: `backend/tests/unit/repositories/test_hub_issue_search.py`

**Interfaces:**
- Consumes: `HubIssue.short_code`、`HubIssue.title`；`sqlalchemy.or_`（已在 `ticket.py:9` import）
- Produces: `HubIssueRepository.list_paginated(..., search: str | None = None)`；`GET /api/hub-issues?search=` query 参数

- [ ] **Step 1: 写 repo 失败测试**

创建 `backend/tests/unit/repositories/test_hub_issue_search.py`：

```python
from app.models import HubIssue
from app.repositories.ticket import HubIssueRepository


def _mk_hub(db, *, short_code: str, title: str, type_: str = "Operation") -> HubIssue:
    h = HubIssue(short_code=short_code, type=type_, title=title, status="created")
    db.add(h)
    db.flush()
    return h


def test_search_matches_short_code(db_session):
    _mk_hub(db_session, short_code="HUB-000123", title="登录报错")
    _mk_hub(db_session, short_code="HUB-000999", title="导出失败")
    repo = HubIssueRepository(db_session)

    p = repo.list_paginated(search="000123")
    assert p.total == 1
    assert p.items[0].short_code == "HUB-000123"


def test_search_matches_title_case_insensitive(db_session):
    _mk_hub(db_session, short_code="HUB-000200", title="Login Error")
    repo = HubIssueRepository(db_session)

    p = repo.list_paginated(search="login")
    assert p.total == 1


def test_empty_search_returns_all(db_session):
    _mk_hub(db_session, short_code="HUB-000300", title="a")
    _mk_hub(db_session, short_code="HUB-000400", title="b")
    repo = HubIssueRepository(db_session)

    p = repo.list_paginated(search=None)
    assert p.total == 2
    p2 = repo.list_paginated(search="")
    assert p2.total == 2
```

注意：造 HubIssue 时字段以模型 NOT NULL 约束为准，报错就补必填字段。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/unit/repositories/test_hub_issue_search.py -v`
Expected: FAIL —— `TypeError: list_paginated() got an unexpected keyword argument 'search'`

- [ ] **Step 3: 改 repo**

`app/repositories/ticket.py`，`list_paginated` 签名加参数（在 `module` 后、`page` 前）：

```python
        module: str | None = None,
        search: str | None = None,
        page: int = 1,
```

在 `if module:` 块（:217-219）之后、`total =`（:221）之前插入：

```python
        if search:
            like = f"%{search}%"
            base = base.where(
                or_(HubIssue.short_code.ilike(like), HubIssue.title.ilike(like))
            )
            count_base = count_base.where(
                or_(HubIssue.short_code.ilike(like), HubIssue.title.ilike(like))
            )
```

确认 `or_` 在 import 行（`ticket.py:9`）；不在就加到 `from sqlalchemy import ...`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/unit/repositories/test_hub_issue_search.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 端点透传 search**

`app/api/hub_issues.py`，`list_hub_issues` 签名在 `module`（:137）后加：

```python
    search: str | None = Query(None),
```

调用 `list_paginated` 时（:141-149）加 `search=search,`（放在 `module=module,` 后）。

- [ ] **Step 6: lint + gen-types**

Run: `cd backend && make lint`
Expected: clean

Run: `cd .. && make gen-types`
Expected: openapi.json + types.ts 更新（`/api/hub-issues` 加 search query）

- [ ] **Step 7: Commit**

```bash
git add backend/app/repositories/ticket.py backend/app/api/hub_issues.py backend/tests/unit/repositories/test_hub_issue_search.py frontend/src/api/openapi.json frontend/src/api/types.ts
git commit -m "feat(hub-issues): list 加 search 参数（short_code/title ilike）"
```

---

## Task 3: 前端 — 抽共享协同动作组件 + 列表页迁移

**Files:**
- Create: `frontend/src/components/hubActions.tsx`
- Modify: `frontend/src/pages/hub-issues/HubIssuesListPage.tsx`（import 共享组件，删本地重复）
- Test: `frontend/src/components/__tests__/hubActions.test.tsx`

**Interfaces:**
- Consumes: `HubIssueSummary`（`@/api/client`）；`api`（`@/api/client`）；`isSupervisor`（`@/api/auth`）；react-query `useMutation`/`useQueryClient`
- Produces（从 hubActions.tsx 导出）：
  - `Modal`, `ModalHeader`, `ModalFooter`（通用弹窗原语）
  - `isDone(h): boolean`, `urgedRecently(h): boolean`, `dwellDays(h): number`, `hubErrMsg(e): string`
  - `UrgeButton({ hub, onDone }: { hub: HubIssueSummary; onDone?: () => void })`
  - `NotifyReleaseModal({ hub, onClose }: { hub: HubIssueSummary; onClose: () => void })`
  - `FeedbackModal({ hub, onClose }: { hub: HubIssueSummary; onClose: () => void })`
  - `HubCollabActions({ hub, onChange }: { hub: HubIssueSummary; onChange?: () => void })` — 组合催办/发版/回访三按钮 + 内部管理 modal 开关，按列表页相同条件显示；供详情页直接用

- [ ] **Step 1: 抽取——创建 hubActions.tsx**

从 `HubIssuesListPage.tsx` 把以下原样搬入新文件 `frontend/src/components/hubActions.tsx`（保持逻辑不变，只改成 export）：
- `Modal`/`ModalHeader`/`ModalFooter`（列表页 lines ~804-847）
- `isDone`/`urgedRecently`/`dwellDays`（lines ~67-80）
- `errMsg` → 重命名 export 为 `hubErrMsg`（lines ~57-63）
- `NotifyReleaseModal`（lines ~468-551）、`FeedbackModal`（lines ~555-641）
- 催办 urge 的 mutation 逻辑，封装成 `UrgeButton` 组件（内部自建 `useMutation` → `api.post` 到 `/api/hub-issues/{id}/urge`，24h throttle 用 `urgedRecently`）

再加一个组合组件（新代码，非搬迁）：

```tsx
const DEV_TYPES = new Set(["Bug_fix", "Demand"]);

export function HubCollabActions({
  hub,
  onChange,
}: {
  hub: HubIssueSummary;
  onChange?: () => void;
}) {
  const [modal, setModal] = useState<null | "notify" | "feedback">(null);
  if (!isSupervisor()) return null;

  const dev = DEV_TYPES.has(hub.type);
  const done = isDone(hub);
  const showUrge = dev && !done && !!hub.linear_identifier;
  const showNotify = dev && done && !hub.release_notified_at && !hub.self_found;
  const showFeedback = hub.feedback_status === "pending";

  return (
    <div className="flex flex-wrap items-center gap-2">
      {showUrge && <UrgeButton hub={hub} onDone={onChange} />}
      {showNotify && (
        <button className="hub-btn" onClick={() => setModal("notify")}>
          发版通知
        </button>
      )}
      {showFeedback && (
        <button className="hub-btn" onClick={() => setModal("feedback")}>
          记录回访
        </button>
      )}
      {modal === "notify" && (
        <NotifyReleaseModal
          hub={hub}
          onClose={() => {
            setModal(null);
            onChange?.();
          }}
        />
      )}
      {modal === "feedback" && (
        <FeedbackModal
          hub={hub}
          onClose={() => {
            setModal(null);
            onChange?.();
          }}
        />
      )}
    </div>
  );
}
```

注意：`hub-btn` className 用列表页动作按钮实际用的 class（复制过来），保持样式一致；`useState`/`isSupervisor` 记得 import。`NotifyReleaseModal`/`FeedbackModal` 的 mutation 成功后应 invalidate `["hub-issues"]` 查询，保持与列表页一致。

- [ ] **Step 2: 写组件测试**

创建 `frontend/src/components/__tests__/hubActions.test.tsx`，覆盖条件显示逻辑（用 `@testing-library/react` + query wrapper，参考既有前端测试）：

```tsx
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HubCollabActions } from "../hubActions";

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient();
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const baseHub: any = {
  id: 1, short_code: "HUB-1", title: "t", type: "Bug_fix", status: "created",
  linear_identifier: "ENG-1", release_notified_at: null, self_found: false,
  feedback_status: null, last_urged_at: null,
};

beforeEach(() => {
  localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
});

it("shows 催办 for dev + not done + has linear", () => {
  wrap(<HubCollabActions hub={baseHub} />);
  expect(screen.getByText("催办")).toBeInTheDocument();
});

it("hides all for non-supervisor", () => {
  localStorage.setItem("auth_user", JSON.stringify({ role: "member" }));
  const { container } = wrap(<HubCollabActions hub={baseHub} />);
  expect(container.firstChild).toBeNull();
});

it("shows 记录回访 when feedback pending", () => {
  wrap(<HubCollabActions hub={{ ...baseHub, feedback_status: "pending" }} />);
  expect(screen.getByText("记录回访")).toBeInTheDocument();
});
```

注意：`UrgeButton` 显示的文案若不是「催办」，按实际文案改断言。`auth_user` 的 localStorage key 以 `@/api/auth` 实际读取的为准（勘察显示是 `auth_user` 的 `.role`）。

- [ ] **Step 3: 跑组件测试**

Run: `cd frontend && npm run test -- hubActions`
Expected: 3 passed

- [ ] **Step 4: 列表页迁移到共享组件**

改 `HubIssuesListPage.tsx`：
- 删除本地的 `Modal`/`ModalHeader`/`ModalFooter`、`isDone`/`urgedRecently`/`dwellDays`、`NotifyReleaseModal`/`FeedbackModal`、`errMsg`（改 import `hubErrMsg`）、催办 mutation
- 从 `@/components/hubActions` import 上述 + `UrgeButton`
- 保留列表页的 `SelfBugModal`（自修复不搬）和「登记自修复」头部按钮
- 行内动作列改用 import 的组件/函数（催办按钮用 `<UrgeButton>`，发版/回访 modal 用 import 的）。**保持原有行为与条件不变**——这是纯搬迁

注意：列表页的自修复 modal 依赖 `Modal` 原语，现在从共享文件 import 即可。若列表页 `errMsg` 在自修复 modal 也用到，统一换 `hubErrMsg`。

- [ ] **Step 5: type-check + build + 全量前端测试**

Run: `cd frontend && npm run type-check && npm run test && npm run build`
Expected: type-check clean、test 全绿、build 成功。若列表页行为回归（某动作不出现/报错），对照搬迁前逻辑修正。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/hubActions.tsx frontend/src/components/__tests__/hubActions.test.tsx frontend/src/pages/hub-issues/HubIssuesListPage.tsx
git commit -m "refactor(frontend): 抽 hubActions 共享协同动作组件 + 列表页迁移"
```

---

## Task 4: 前端 — hub 详情页接入 3 协同动作

**Files:**
- Modify: `frontend/src/pages/hub-issues/HubIssueDetailPage.tsx`

**Interfaces:**
- Consumes: `HubCollabActions`（`@/components/hubActions`）；`HubIssueDetail` 结构满足 `HubIssueSummary`
- Produces: 详情页渲染 3 动作，成功后 invalidate 详情查询

- [ ] **Step 1: 详情页渲染 HubCollabActions**

在 `HubIssueDetailPage.tsx` 的 Header 区（负责人/状态附近）加入：

```tsx
import { HubCollabActions } from "@/components/hubActions";
// ...
<HubCollabActions
  hub={data as unknown as HubIssueSummary}
  onChange={() => qc.invalidateQueries({ queryKey: ["hub-issue", id] })}
/>
```

注意：`qc` 用页面已有的 `useQueryClient()`（没有就加）；详情查询的 queryKey 以页面实际用的为准（勘察未确认精确 key，用页面里 `useQuery` 那个 key）。`data` 到 `HubIssueSummary` 的转换：因 Detail 是结构超集，`as unknown as HubIssueSummary` 安全；若已有共享类型别名可直接传。

- [ ] **Step 2: 补「查看闭环」链接（可选一致性）**

若列表页在 `feedback_status === "resolved"` 时显示「查看闭环」链接，详情页可省略（详情页本身就在闭环上下文）。**本步跳过**，除非详情页缺 feedback 结果展示——若缺，加只读一行显示 `feedback_status` + `feedback_note`。

- [ ] **Step 3: type-check + build**

Run: `cd frontend && npm run type-check && npm run build`
Expected: clean

- [ ] **Step 4: 手动核对（浏览器）**

用 `/browse` 或本地 dev 打开一个 Bug_fix hub 详情页，确认：未完成时显示催办、已完成未通知时显示发版通知、feedback pending 时显示回访；非主管登录时都不显示。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/hub-issues/HubIssueDetailPage.tsx
git commit -m "feat(frontend): hub 详情页接入催办/发版/回访协同动作"
```

---

## Task 5: 前端 — relink 重关联弹窗

**Files:**
- Create: `frontend/src/pages/tickets/RelinkModal.tsx`
- Modify: `frontend/src/pages/tickets/TicketDetailPage.tsx`

**Interfaces:**
- Consumes: `api`（`@/api/client`）；`Modal`/`ModalHeader`/`ModalFooter`/`hubErrMsg`（`@/components/hubActions`）；`GET /api/hub-issues?search=`；`POST /api/supervisor/relink`
- Produces: `RelinkModal({ ticketId, currentHubId, onClose }: { ticketId: number; currentHubId: number; onClose: () => void })`

- [ ] **Step 1: 写 RelinkModal**

创建 `frontend/src/pages/tickets/RelinkModal.tsx`：

```tsx
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { Modal, ModalHeader, ModalFooter, hubErrMsg } from "@/components/hubActions";

const TYPE_LABELS: Record<string, string> = {
  Operation: "运营", Bug_fix: "Bug 修复", Demand: "需求", Internal_task: "内部任务",
};

export function RelinkModal({
  ticketId,
  currentHubId,
  onClose,
}: {
  ticketId: number;
  currentHubId: number;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const [err, setErr] = useState("");

  const search = useQuery({
    queryKey: ["hub-issues", "relink-search", q],
    queryFn: () => api.get("/api/hub-issues", { search: q, page_size: 20 }),
    enabled: q.trim().length >= 2,
    staleTime: 10_000,
  });

  const relink = useMutation({
    mutationFn: () =>
      api.post("/api/supervisor/relink", {
        ticket_id: ticketId,
        new_hub_issue_id: selected,
        reason,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ticket-detail", ticketId] });
      onClose();
    },
    onError: (e) => setErr(hubErrMsg(e)),
  });

  const items = ((search.data?.items ?? []) as any[]).filter(
    (h) => h.id !== currentHubId,
  );

  return (
    <Modal onClose={onClose}>
      <ModalHeader title="重新关联到其他 hub" onClose={onClose} />
      <div className="p-4 space-y-3">
        <input
          autoFocus
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索 short_code 或标题（≥2 字）"
          className="w-full px-2 py-1.5 border border-hub-border rounded-[7px] text-[12.5px]"
        />
        <div className="max-h-60 overflow-auto border border-hub-border rounded-[7px]">
          {search.isLoading && <div className="p-2 text-hub-muted text-[12px]">搜索中…</div>}
          {items.map((h) => (
            <button
              key={h.id}
              onClick={() => setSelected(h.id)}
              className={`w-full text-left px-3 py-2 text-[12.5px] border-b border-hub-border last:border-0 ${
                selected === h.id ? "bg-hub-teal/10" : "hover:bg-black/[0.02]"
              }`}
            >
              <span className="font-mono">{h.short_code}</span>
              <span className="ml-2 text-hub-muted">[{TYPE_LABELS[h.type] ?? h.type}]</span>
              <span className="ml-2">{h.title}</span>
            </button>
          ))}
          {q.trim().length >= 2 && !search.isLoading && items.length === 0 && (
            <div className="p-2 text-hub-muted text-[12px]">无匹配</div>
          )}
        </div>
        <input
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="重关联原因（可选，建议填写）"
          className="w-full px-2 py-1.5 border border-hub-border rounded-[7px] text-[12.5px]"
        />
        {err && <div className="text-[12px] text-red-600">{err}</div>}
      </div>
      <ModalFooter>
        <button className="hub-btn" onClick={onClose}>取消</button>
        <button
          className="hub-btn-primary"
          disabled={selected == null || relink.isPending}
          onClick={() => {
            setErr("");
            relink.mutate();
          }}
        >
          确认重关联
        </button>
      </ModalFooter>
    </Modal>
  );
}
```

注意：`hub-btn`/`hub-btn-primary`/`hub-*` 色 class 以项目实际 Tailwind class 为准（对照 hubActions 里的按钮 class 用同款）。`ModalFooter` 若不接受 children 结构不同，按其实际签名调整。ticket-detail 的 queryKey 以 TicketDetailPage 实际用的为准。

- [ ] **Step 2: TicketDetailPage 接入按钮**

在 `TicketDetailPage.tsx` 基本信息区的 hub_issue 链接旁，当 `hub_issue_id != null` 且 `isSupervisor()` 时加按钮 + modal 开关：

```tsx
import { RelinkModal } from "./RelinkModal";
import { isSupervisor } from "@/api/auth";
// 组件内 state:
const [relinkOpen, setRelinkOpen] = useState(false);
// hub_issue 链接旁:
{ticket.hub_issue_id != null && isSupervisor() && (
  <button className="hub-btn ml-2" onClick={() => setRelinkOpen(true)}>
    重新关联
  </button>
)}
{relinkOpen && ticket.hub_issue_id != null && (
  <RelinkModal
    ticketId={ticket.id}
    currentHubId={ticket.hub_issue_id}
    onClose={() => setRelinkOpen(false)}
  />
)}
```

注意：`ticket` 变量名/字段以页面实际为准（勘察显示用 inline getByPath 拿 TicketDetail，含 `hub_issue_id`）。

- [ ] **Step 3: type-check + build**

Run: `cd frontend && npm run type-check && npm run build`
Expected: clean

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/tickets/RelinkModal.tsx frontend/src/pages/tickets/TicketDetailPage.tsx
git commit -m "feat(frontend): 工单详情页 relink 重关联弹窗（搜索选目标 hub）"
```

---

## Task 6: 前端 — 手动指派 UI（详情页单条 + 列表页批量）

**Files:**
- Modify: `frontend/src/pages/tickets/TicketDetailPage.tsx`
- Modify: `frontend/src/pages/tickets/TicketsListPage.tsx`（列表页文件名以实际为准）

**Interfaces:**
- Consumes: `UserSelect`（`@/components/selectors.tsx`）；`POST /api/supervisor/assign`；`isSupervisor`（`@/api/auth`）
- Produces: 详情页单条指派控件；列表页批量指派按钮（复用现有多选浮动栏）

- [ ] **Step 1: selectors.tsx 的 UserSelect 加 roles 过滤**

改 `frontend/src/components/selectors.tsx`，`UserSelect` 加可选 `roles` prop：

```tsx
export function UserSelect({
  value, onChange, placeholder = "选择用户", className, disabled, required,
  roles,
}: SelectProps<number> & { roles?: string[] }) {
  const q = useUserOptions();
  const opts = (q.data ?? []).filter(
    (u: UserOpt) => !roles || roles.includes(u.role),
  );
  return (
    <select
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))}
      disabled={disabled || q.isLoading}
      className={className ?? "px-2 py-1.5 border border-hub-border rounded-[7px] bg-white outline-none focus:border-hub-teal text-[12.5px] min-w-[12rem]"}
    >
      {!required && <option value="">{placeholder}</option>}
      {opts.map((u: UserOpt) => (
        <option key={u.id} value={u.id}>{labelForUser(u)}</option>
      ))}
    </select>
  );
}
```

- [ ] **Step 2: 详情页单条指派**

`TicketDetailPage.tsx`「负责人」只读处（lines ~80-84）旁，supervisor 时加指派控件：

```tsx
import { UserSelect } from "@/components/selectors";
// state:
const [assignOpen, setAssignOpen] = useState(false);
const [assignTo, setAssignTo] = useState<number | undefined>(undefined);
const assign = useMutation({
  mutationFn: (uid: number) =>
    api.post("/api/supervisor/assign", { ticket_ids: [ticket.id], assigned_user_id: uid }),
  onSuccess: () => {
    qc.invalidateQueries({ queryKey: ["ticket-detail", ticket.id] });
    setAssignOpen(false);
    setAssignTo(undefined);
  },
});
// 负责人字段旁:
{isSupervisor() && !assignOpen && (
  <button className="hub-btn ml-2" onClick={() => setAssignOpen(true)}>指派</button>
)}
{isSupervisor() && assignOpen && (
  <span className="inline-flex items-center gap-2 ml-2">
    <UserSelect value={assignTo} onChange={setAssignTo} roles={["assignee", "supervisor", "admin"]} />
    <button className="hub-btn-primary" disabled={assignTo == null || assign.isPending}
      onClick={() => assignTo != null && assign.mutate(assignTo)}>确认</button>
    <button className="hub-btn" onClick={() => setAssignOpen(false)}>取消</button>
  </span>
)}
```

注意：`qc`/`api`/`ticket` 用页面既有的。ticket-detail queryKey 以实际为准。

- [ ] **Step 3: 列表页批量指派**

`TicketsListPage.tsx`（含现有「仅未分配」筛选 + 多选 + 底部浮动栏 + reroute 的页面）。在浮动操作栏（reroute 按钮旁）加批量指派：

```tsx
import { UserSelect } from "@/components/selectors";
// state:
const [bulkAssignTo, setBulkAssignTo] = useState<number | undefined>(undefined);
const bulkAssign = useMutation({
  mutationFn: (uid: number) =>
    api.post("/api/supervisor/assign", { ticket_ids: selectedIds, assigned_user_id: uid }),
  onSuccess: (res) => {
    qc.invalidateQueries({ queryKey: ["tickets"] });
    // 结果弹窗复用页面既有 result modal 模式；至少展示 res.assigned_count
    setBulkAssignTo(undefined);
  },
});
// 浮动栏内（selectedIds.length > 0 时）:
<span className="inline-flex items-center gap-2">
  <UserSelect value={bulkAssignTo} onChange={setBulkAssignTo} roles={["assignee", "supervisor", "admin"]} placeholder="指派给…" />
  <button className="hub-btn-primary" disabled={bulkAssignTo == null || bulkAssign.isPending}
    onClick={() => bulkAssignTo != null && bulkAssign.mutate(bulkAssignTo)}>批量指派</button>
</span>
```

注意：`selectedIds`/`qc`/结果弹窗 以页面既有实现为准（勘察记录列表页已有 checkbox 多选 + reroute + 结果弹窗）。tickets 列表 queryKey 以实际为准。

- [ ] **Step 4: type-check + build + test**

Run: `cd frontend && npm run type-check && npm run test && npm run build`
Expected: clean、全绿

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/selectors.tsx frontend/src/pages/tickets/TicketDetailPage.tsx frontend/src/pages/tickets/TicketsListPage.tsx
git commit -m "feat(frontend): 手动指派 UI（详情页单条 + 列表页批量）"
```

---

## Task 7: 全量验证 + 收尾

**Files:** 无新增

- [ ] **Step 1: 后端全量**

Run: `cd backend && make lint && make unit`
Expected: lint clean、unit 全绿、覆盖率 ≥70%

- [ ] **Step 2: 类型同步门槛**

Run: `cd .. && make check-types`
Expected: PASS（openapi.json + types.ts 与后端一致）

- [ ] **Step 3: 前端全量**

Run: `cd frontend && npm run type-check && npm run test && npm run build`
Expected: 全绿

- [ ] **Step 4: 更新记忆**

更新 `frontend_gap_completion.md`：记录 3 项完成状态、新端点 `POST /api/supervisor/assign`、`GET /api/hub-issues?search=`、共享组件 `hubActions.tsx`。

- [ ] **Step 5: 最终 commit（若有记忆/文档改动）**

```bash
git add -A && git commit -m "chore: 缺口补全收尾（验证 + 记忆更新）"
```

---

## Self-Review

**Spec coverage:**
- ① 手动指派 → Task 1（后端）+ Task 6（前端）✅
- ② relink 搜索 → Task 2（后端 search）+ Task 5（前端弹窗）✅
- ③ 详情页 3 动作 → Task 3（抽组件）+ Task 4（详情页接入）✅
- 无迁移 / gen-types / 角色限制 / changed_by 约定 → Global Constraints + 各 Task ✅

**Placeholder scan:** 无 TBD/TODO；每个 code step 有完整代码；"以实际为准" 的注释均指向勘察已确认的模式（fixture 名、queryKey、className），非逻辑占位。

**Type consistency:** `AssignRequest`/`AssignItemResult`/`AssignResult` 在 Task 1 定义并被端点消费，名字一致；`HubCollabActions`/`UrgeButton`/`hubErrMsg`/`Modal` 在 Task 3 定义，Task 4/5 消费，签名一致；`UserSelect` 加 `roles` prop 在 Task 6 Step 1 定义后即用。
