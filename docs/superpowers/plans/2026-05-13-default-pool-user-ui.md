# 兜底处理人页面配置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `DEFAULT_POOL_USER_ID` 从 `.env` 迁移到数据库，允许 supervisor/admin 在主管工作台警告 Banner 内联选择兜底处理人，立即生效。

**Architecture:** 新建 `system_settings` 键值表存储系统配置；封装 `get_default_pool_user_id(db)` 工具函数统一读取优先级（数据库优先，`.env` 兜底）；新增 `admin_settings.py` API router；改造前端 `ConfigWarningsBanner` 组件，对 `no_default_pool` 警告内联用户下拉选择器。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2 / Alembic / React 18 / TanStack Query / Tailwind CSS

---

## File Map

**新建：**
- `backend/app/api/admin_settings.py` — GET/PUT `/api/admin/settings/default-pool-user`
- `backend/app/services/system_settings.py` — `get_default_pool_user_id()` 工具函数
- `backend/migrations/versions/0002_system_settings.py` — Alembic 迁移
- `backend/tests/unit/api/test_admin_settings.py` — API 单测
- `backend/tests/unit/services/test_system_settings.py` — 工具函数单测

**修改：**
- `backend/app/models.py` — 新增 `SystemSetting` ORM 模型
- `backend/app/main.py` — 注册 `admin_settings` router
- `backend/app/services/supervisor/config_warnings.py` — 改用 `get_default_pool_user_id(db)`
- `backend/app/api/webhooks.py` — 改用 `get_default_pool_user_id(db)`
- `backend/app/services/supervisor/reroute.py` — 改用 `get_default_pool_user_id(db)`
- `frontend/src/pages/supervisor/SupervisorPage.tsx` — 改造 `ConfigWarningsBanner`

---

## Task 1: 新增 SystemSetting ORM 模型 + Alembic 迁移

**Files:**
- Modify: `backend/app/models.py`
- Create: `backend/migrations/versions/0002_system_settings.py`

- [ ] **Step 1: 在 models.py 末尾追加 SystemSetting 模型**

打开 `backend/app/models.py`，在文件末尾追加：

```python
class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
```

同时在文件顶部 import 行补充缺少的类型（`Text`, `ForeignKey` 已在其他模型中使用，确认已导入）。

- [ ] **Step 2: 创建 Alembic 迁移文件**

创建 `backend/migrations/versions/0002_system_settings.py`：

```python
"""Add system_settings table.

Revision ID: 0002_system_settings
Revises: 0001_d0_initial
Create Date: 2026-05-13 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_system_settings"
down_revision: str | Sequence[str] | None = "0001_d0_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column(
            "updated_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("system_settings")
```

- [ ] **Step 3: 验证迁移文件语法**

```bash
cd backend && .venv/bin/alembic check
```

期望输出：无报错（或提示 "Target database is not up to date"，正常）。

- [ ] **Step 4: Commit**

```bash
git add backend/app/models.py backend/migrations/versions/0002_system_settings.py
git commit -m "feat(db): add system_settings table for runtime config"
```

---

## Task 2: 封装 get_default_pool_user_id 工具函数 + 单测

**Files:**
- Create: `backend/app/services/system_settings.py`
- Create: `backend/tests/unit/services/test_system_settings.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/unit/services/test_system_settings.py`：

```python
"""Unit tests for get_default_pool_user_id priority logic."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models import SystemSetting, User
from app.services.system_settings import get_default_pool_user_id


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(User(id=99, feishu_uid="ou_pool", name="pool-user", role="assignee"))
    db_session.commit()
    return db_session


def test_returns_db_value_when_set(world: Session) -> None:
    world.add(SystemSetting(key="default_pool_user_id", value="99"))
    world.commit()
    assert get_default_pool_user_id(world) == 99


def test_falls_back_to_env_when_db_null(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings
    monkeypatch.setenv("DEFAULT_POOL_USER_ID", "42")
    get_settings.cache_clear()
    # No DB record
    assert get_default_pool_user_id(world) == 42
    get_settings.cache_clear()


def test_returns_none_when_both_unset(world: Session) -> None:
    assert get_default_pool_user_id(world) is None


def test_db_overrides_env(world: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings
    monkeypatch.setenv("DEFAULT_POOL_USER_ID", "42")
    get_settings.cache_clear()
    world.add(SystemSetting(key="default_pool_user_id", value="99"))
    world.commit()
    assert get_default_pool_user_id(world) == 99
    get_settings.cache_clear()
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
cd backend && .venv/bin/pytest tests/unit/services/test_system_settings.py -v
```

期望：`ImportError: cannot import name 'get_default_pool_user_id'`

- [ ] **Step 3: 实现 get_default_pool_user_id**

创建 `backend/app/services/system_settings.py`：

```python
"""system_settings.py — runtime config stored in DB with .env fallback."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import SystemSetting


def get_default_pool_user_id(db: Session) -> int | None:
    """Return the default pool user ID.

    Priority: DB system_settings > .env DEFAULT_POOL_USER_ID > None.
    """
    row = db.execute(
        select(SystemSetting).where(SystemSetting.key == "default_pool_user_id")
    ).scalar_one_or_none()
    if row is not None and row.value is not None:
        try:
            return int(row.value)
        except (ValueError, TypeError):
            return None
    return get_settings().default_pool_user_id
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
cd backend && .venv/bin/pytest tests/unit/services/test_system_settings.py -v
```

期望：4 tests PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/system_settings.py backend/tests/unit/services/test_system_settings.py
git commit -m "feat(services): add get_default_pool_user_id with DB>env priority"
```

---

## Task 3: 更新 config_warnings / webhooks / reroute 使用新工具函数

**Files:**
- Modify: `backend/app/services/supervisor/config_warnings.py`
- Modify: `backend/app/api/webhooks.py`
- Modify: `backend/app/services/supervisor/reroute.py`

- [ ] **Step 1: 修改 config_warnings.py**

打开 `backend/app/services/supervisor/config_warnings.py`，将：

```python
from app.config import get_settings
```

替换为：

```python
from app.services.system_settings import get_default_pool_user_id
```

将：

```python
    if get_settings().default_pool_user_id is None:
        warnings.append(
            ConfigWarning(
                code="no_default_pool",
                product_line_code=None,
                module=None,
                detail="系统未配置兜底处理人（DEFAULT_POOL_USER_ID），无分工匹配的工单将无人处理。请联系运维在服务器 .env 文件中设置 DEFAULT_POOL_USER_ID=<用户ID>，用户ID可在「管理后台 → 用户管理」中查看。",
            )
        )
```

替换为：

```python
    if get_default_pool_user_id(db) is None:
        warnings.append(
            ConfigWarning(
                code="no_default_pool",
                product_line_code=None,
                module=None,
                detail="系统未配置兜底处理人，无分工匹配的工单将无人处理。请在主管工作台配置警告处直接选择兜底处理人。",
            )
        )
```

- [ ] **Step 2: 修改 webhooks.py**

在 `backend/app/api/webhooks.py` 中，将所有 `default_pool_user_id=get_settings().default_pool_user_id` 和 `default_pool_user_id=settings.default_pool_user_id` 替换为 `default_pool_user_id=get_default_pool_user_id(db)`。

在文件顶部 import 区域添加：

```python
from app.services.system_settings import get_default_pool_user_id
```

并移除对 `get_settings` 的导入（如果仅用于 `default_pool_user_id`）。

- [ ] **Step 3: 修改 reroute.py**

打开 `backend/app/services/supervisor/reroute.py`，将：

```python
router = Router(self._db, default_pool_user_id=settings.default_pool_user_id)
```

替换为：

```python
from app.services.system_settings import get_default_pool_user_id
...
router = Router(self._db, default_pool_user_id=get_default_pool_user_id(self._db))
```

- [ ] **Step 4: 运行全量单测**

```bash
cd backend && .venv/bin/pytest tests/unit/ -v --tb=short
```

期望：全部 PASSED（无新增失败）

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/supervisor/config_warnings.py backend/app/api/webhooks.py backend/app/services/supervisor/reroute.py
git commit -m "refactor: use get_default_pool_user_id(db) in routing/warnings/reroute"
```

---

## Task 4: 新增 admin_settings API + 单测

**Files:**
- Create: `backend/app/api/admin_settings.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/unit/api/test_admin_settings.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/unit/api/test_admin_settings.py`：

```python
"""Unit tests for GET/PUT /api/admin/settings/default-pool-user."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import SystemSetting, User


def _auth_header(client: TestClient, role: str = "supervisor") -> dict[str, str]:
    from jose import jwt
    from app.config import get_settings
    token = jwt.encode(
        {"sub": "1", "name": "test", "role": role},
        get_settings().jwt_secret,
        algorithm=get_settings().jwt_algorithm,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world(db_session: Session) -> Session:
    db_session.add(User(id=1, feishu_uid="ou_super", name="supervisor", role="supervisor"))
    db_session.add(User(id=2, feishu_uid="ou_pool", name="pool-user", role="assignee"))
    db_session.commit()
    return db_session


def test_get_returns_null_when_unset(app_client: TestClient, world: Session) -> None:
    resp = app_client.get(
        "/api/admin/settings/default-pool-user",
        headers=_auth_header(app_client),
    )
    assert resp.status_code == 200
    assert resp.json()["user_id"] is None


def test_put_sets_value(app_client: TestClient, world: Session) -> None:
    resp = app_client.put(
        "/api/admin/settings/default-pool-user",
        json={"user_id": 2},
        headers=_auth_header(app_client),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == 2
    assert data["user_name"] == "pool-user"


def test_get_returns_set_value(app_client: TestClient, world: Session) -> None:
    world.add(SystemSetting(key="default_pool_user_id", value="2", updated_by=1))
    world.commit()
    resp = app_client.get(
        "/api/admin/settings/default-pool-user",
        headers=_auth_header(app_client),
    )
    assert resp.status_code == 200
    assert resp.json()["user_id"] == 2
    assert resp.json()["user_name"] == "pool-user"


def test_put_invalid_user_returns_422(app_client: TestClient, world: Session) -> None:
    resp = app_client.put(
        "/api/admin/settings/default-pool-user",
        json={"user_id": 9999},
        headers=_auth_header(app_client),
    )
    assert resp.status_code == 422


def test_put_null_clears_value(app_client: TestClient, world: Session) -> None:
    world.add(SystemSetting(key="default_pool_user_id", value="2", updated_by=1))
    world.commit()
    resp = app_client.put(
        "/api/admin/settings/default-pool-user",
        json={"user_id": None},
        headers=_auth_header(app_client),
    )
    assert resp.status_code == 200
    assert resp.json()["user_id"] is None


def test_member_role_forbidden(app_client: TestClient, world: Session) -> None:
    resp = app_client.get(
        "/api/admin/settings/default-pool-user",
        headers=_auth_header(app_client, role="member"),
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
cd backend && .venv/bin/pytest tests/unit/api/test_admin_settings.py -v
```

期望：`404 Not Found`（路由未注册）

- [ ] **Step 3: 实现 admin_settings.py**

创建 `backend/app/api/admin_settings.py`：

```python
"""Admin settings API — runtime system configuration.

  GET  /api/admin/settings/default-pool-user  — get current default pool user
  PUT  /api/admin/settings/default-pool-user  — set default pool user

Requires role IN ('supervisor', 'admin').
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps.auth import AuthedUser, require_supervisor
from app.core.logging import get_logger
from app.db import get_session
from app.models import SystemSetting, User
from app.services.system_settings import get_default_pool_user_id

router = APIRouter()
logger = get_logger(__name__)

_KEY = "default_pool_user_id"


class DefaultPoolUserOut(BaseModel):
    user_id: int | None
    user_name: str | None


class DefaultPoolUserIn(BaseModel):
    user_id: int | None


def _build_out(db: Session) -> DefaultPoolUserOut:
    uid = get_default_pool_user_id(db)
    if uid is None:
        return DefaultPoolUserOut(user_id=None, user_name=None)
    user = db.execute(select(User).where(User.id == uid)).scalar_one_or_none()
    return DefaultPoolUserOut(
        user_id=uid,
        user_name=user.name if user else None,
    )


@router.get("/default-pool-user", response_model=DefaultPoolUserOut)
def get_default_pool_user(
    _: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> DefaultPoolUserOut:
    return _build_out(db)


@router.put("/default-pool-user", response_model=DefaultPoolUserOut)
def put_default_pool_user(
    body: DefaultPoolUserIn,
    current_user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> DefaultPoolUserOut:
    if body.user_id is not None:
        user = db.execute(
            select(User).where(User.id == body.user_id, User.is_active.is_(True))
        ).scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=422, detail="user not found or inactive")

    row = db.execute(
        select(SystemSetting).where(SystemSetting.key == _KEY)
    ).scalar_one_or_none()

    new_value = str(body.user_id) if body.user_id is not None else None
    if row is None:
        db.add(SystemSetting(key=_KEY, value=new_value, updated_by=current_user.user_id))
    else:
        row.value = new_value
        row.updated_by = current_user.user_id

    db.commit()
    logger.info("system_setting_updated", key=_KEY, value=new_value, by=current_user.user_id)
    return _build_out(db)
```

- [ ] **Step 4: 注册 router 到 main.py**

打开 `backend/app/main.py`，在 import 区域添加：

```python
from app.api import (
    admin,
    admin_catalog,
    admin_scopes,
    admin_settings,
    admin_users,
    ...
)
```

在 `app.include_router(admin_users.router, ...)` 后添加：

```python
app.include_router(admin_settings.router, prefix="/api/admin/settings", tags=["admin-settings"])
```

- [ ] **Step 5: 运行测试，确认通过**

```bash
cd backend && .venv/bin/pytest tests/unit/api/test_admin_settings.py -v
```

期望：6 tests PASSED

- [ ] **Step 6: 运行全量单测**

```bash
cd backend && .venv/bin/pytest tests/unit/ -v --tb=short
```

期望：全部 PASSED

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/admin_settings.py backend/app/main.py backend/tests/unit/api/test_admin_settings.py
git commit -m "feat(api): add GET/PUT /api/admin/settings/default-pool-user"
```

---

## Task 5: 生成前端类型

**Files:**
- Modify: `frontend/src/api/openapi.json`
- Modify: `frontend/src/api/types.ts`

- [ ] **Step 1: 启动后端**

```bash
cd backend && .venv/bin/uvicorn app.main:app --reload --port 8080 &
sleep 3
```

- [ ] **Step 2: 生成类型**

```bash
cd frontend && npm run gen:api:live
```

期望：`frontend/src/api/openapi.json` 和 `frontend/src/api/types.ts` 更新，包含 `/api/admin/settings/default-pool-user` 路径。

- [ ] **Step 3: 停止后端**

```bash
kill %1
```

- [ ] **Step 4: 验证类型文件包含新路径**

```bash
grep "default-pool-user" frontend/src/api/types.ts
```

期望：有输出（路径已生成）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/openapi.json frontend/src/api/types.ts
git commit -m "chore: regenerate frontend types for admin settings API"
```

---

## Task 6: 改造前端 ConfigWarningsBanner

**Files:**
- Modify: `frontend/src/pages/supervisor/SupervisorPage.tsx`

- [ ] **Step 1: 修改 SupervisorPage.tsx**

将 `SupervisorPage.tsx` 中的 `ConfigWarningsBanner` 组件替换为以下实现（保留文件其余部分不变）：

```tsx
function ConfigWarningsBanner({ warnings, onWarningsChange }: {
  warnings: ConfigWarningItem[];
  onWarningsChange: () => void;
}) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="border border-yellow-400 bg-yellow-50 dark:bg-yellow-950 dark:border-yellow-700 rounded-lg p-4 space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-yellow-800 dark:text-yellow-200">
          配置警告（{warnings.length} 项）
        </span>
        <button
          onClick={() => setCollapsed((c) => !c)}
          className="text-xs text-yellow-700 dark:text-yellow-300 hover:underline"
        >
          {collapsed ? "展开" : "收起"}
        </button>
      </div>
      {!collapsed && (
        <ul className="space-y-2">
          {warnings.map((w, i) =>
            w.code === "no_default_pool" ? (
              <DefaultPoolWarningItem key={i} onSaved={onWarningsChange} />
            ) : (
              <li
                key={i}
                className="text-xs text-yellow-700 dark:text-yellow-300 flex gap-2"
              >
                <span className="font-mono bg-yellow-100 dark:bg-yellow-900 px-1 rounded shrink-0">
                  {w.code}
                </span>
                <span>{w.detail}</span>
              </li>
            )
          )}
        </ul>
      )}
    </div>
  );
}

function DefaultPoolWarningItem({ onSaved }: { onSaved: () => void }) {
  const [selectedUserId, setSelectedUserId] = useState<string>("");
  const [saveError, setSaveError] = useState<string | null>(null);

  const users = useQuery({
    queryKey: ["admin", "users"],
    queryFn: () => api.get("/api/admin/users"),
    staleTime: 60_000,
  });

  const currentSetting = useQuery({
    queryKey: ["admin", "settings", "default-pool-user"],
    queryFn: () => api.get("/api/admin/settings/default-pool-user"),
    staleTime: 30_000,
  });

  // Pre-fill selector with current value
  useState(() => {
    if (currentSetting.data?.user_id != null && selectedUserId === "") {
      setSelectedUserId(String(currentSetting.data.user_id));
    }
  });

  const save = useMutation({
    mutationFn: () =>
      api.put("/api/admin/settings/default-pool-user", {
        user_id: selectedUserId ? Number(selectedUserId) : null,
      }),
    onSuccess: () => {
      setSaveError(null);
      onSaved();
    },
    onError: (e) => setSaveError(e instanceof ApiError ? e.message : String(e)),
  });

  return (
    <li className="text-xs text-yellow-700 dark:text-yellow-300 space-y-2">
      <div className="flex gap-2 items-start">
        <span className="font-mono bg-yellow-100 dark:bg-yellow-900 px-1 rounded shrink-0 mt-0.5">
          no_default_pool
        </span>
        <span>系统未配置兜底处理人，无分工匹配的工单将无人处理。</span>
      </div>
      <div className="flex gap-2 items-center ml-0">
        <select
          value={selectedUserId}
          onChange={(e) => setSelectedUserId(e.target.value)}
          disabled={save.isPending || users.isLoading}
          className="text-xs border border-yellow-400 dark:border-yellow-600 rounded px-2 py-1 bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200 disabled:opacity-50"
        >
          <option value="">— 选择处理人 —</option>
          {users.data?.users.map((u: { id: number; name: string }) => (
            <option key={u.id} value={String(u.id)}>
              {u.name}
            </option>
          ))}
        </select>
        <button
          onClick={() => save.mutate()}
          disabled={save.isPending || !selectedUserId}
          className="text-xs px-3 py-1 bg-yellow-600 hover:bg-yellow-700 text-white rounded disabled:opacity-50"
        >
          {save.isPending ? "保存中…" : "保存"}
        </button>
      </div>
      {saveError && <p className="text-xs text-red-600 ml-0">{saveError}</p>}
    </li>
  );
}
```

同时将 `SupervisorPage` 中调用 `ConfigWarningsBanner` 的地方改为传入 `onWarningsChange`：

```tsx
{warnings.data && warnings.data.warnings.length > 0 && (
  <ConfigWarningsBanner
    warnings={warnings.data.warnings}
    onWarningsChange={() => warnings.refetch()}
  />
)}
```

并在文件顶部 import 中补充 `useMutation`（已有）和 `ApiError`（已有）。

- [ ] **Step 2: 类型检查**

```bash
cd frontend && npm run type-check
```

期望：无 TypeScript 错误

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/supervisor/SupervisorPage.tsx
git commit -m "feat(ui): inline default pool user selector in supervisor warning banner"
```

---

## Task 7: 全量验证

- [ ] **Step 1: 后端全量单测**

```bash
cd backend && .venv/bin/pytest tests/unit/ -v --tb=short
```

期望：全部 PASSED

- [ ] **Step 2: 前端类型检查 + 单测**

```bash
cd frontend && npm run type-check && npm run test
```

期望：无错误，测试全部通过

- [ ] **Step 3: 最终 Commit（如有未提交变更）**

```bash
git status
# 若有未提交文件：
git add -p
git commit -m "chore: final cleanup for default-pool-user-ui feature"
```
