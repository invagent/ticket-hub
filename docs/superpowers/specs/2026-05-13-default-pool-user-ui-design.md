# 兜底处理人页面配置 — 设计文档

**日期**：2026-05-13  
**状态**：已批准  
**作者**：panda_li

## 背景

`DEFAULT_POOL_USER_ID` 目前只能在服务器 `.env` 文件中配置，修改后需要联系运维并重启服务。主管工作台的配置警告 Banner 提示"请联系运维设置 .env"，体验差。本设计将该配置迁移到数据库，允许 supervisor/admin 直接在主管工作台的警告 Banner 内联设置，立即生效。

## 目标

- 管理员/主管可在主管工作台直接选择兜底处理人，无需改 `.env` 或重启服务
- 配置立即生效，影响后续所有路由决策
- 向后兼容：已有 `.env` 配置的部署无需改动

## 不在范围内

- 其他系统配置的页面化（本次只做 `default_pool_user_id`）
- 配置变更审计日志（`updated_by` / `updated_at` 字段已记录，暂不在 UI 展示）

---

## 数据层

### 新建 `system_settings` 表

```sql
CREATE TABLE system_settings (
    key         VARCHAR(64) PRIMARY KEY,
    value       TEXT,
    updated_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at  TIMESTAMP NOT NULL DEFAULT now()
);
```

初始数据：无（未配置时 `value` 为 NULL）。

### ORM 模型（`app/models.py`）

```python
class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())
```

### 读取优先级

```
数据库 system_settings.value  （优先）
  ↓ 若 NULL
settings.default_pool_user_id  （.env 兜底）
```

封装工具函数 `get_default_pool_user_id(db: Session) -> int | None`，统一处理优先级，供 webhooks、reroute、config_warnings 调用。

---

## 后端 API

### 新文件：`app/api/admin_settings.py`

挂载到 `/api/admin/settings`。

#### `GET /api/admin/settings/default-pool-user`

权限：`require_supervisor`

响应：
```json
{
  "user_id": 42,
  "user_name": "张三"
}
```
`user_id` 为 null 表示未配置（数据库和 .env 均未设置）。

#### `PUT /api/admin/settings/default-pool-user`

权限：`require_supervisor`

请求体：
```json
{ "user_id": 42 }
```
`user_id` 传 null 表示清除配置。

响应：同 GET，返回更新后的值。

写入 `system_settings` 表，记录 `updated_by`（当前用户 ID）和 `updated_at`。若 `user_id` 不存在或已停用，返回 422。

### 修改现有文件

**`app/services/supervisor/config_warnings.py`**：`get_config_warnings()` 改为调用 `get_default_pool_user_id(db)`，数据库和 .env 均未配置时才触发 `no_default_pool` 警告。

**`app/api/webhooks.py`** / **`app/services/supervisor/reroute.py`**：调用 `get_default_pool_user_id(db)` 替换直接读 `settings.default_pool_user_id`。

---

## 前端 UI

### 改造位置

`frontend/src/pages/supervisor/SupervisorPage.tsx` 中的 `ConfigWarningsBanner` 组件，针对 `code === "no_default_pool"` 的警告项特殊渲染。

### 交互流程

1. 主管工作台加载，`config-warnings` 接口返回 `no_default_pool` 警告
2. Banner 显示："系统未配置兜底处理人，无分工匹配的工单将无人处理"
3. 右侧显示用户下拉选择器（从 `GET /api/admin/users` 拉活跃用户）+ 「保存」按钮
4. 用户选择后点保存，调用 `PUT /api/admin/settings/default-pool-user`
5. 成功后重新 fetch `config-warnings`，Banner 消失
6. 保存中：按钮 loading 状态，下拉禁用
7. 失败：按钮下方显示红色错误文字

### 新增 API 调用

- `GET /api/admin/settings/default-pool-user`：页面加载时获取当前值，预填下拉选择器
- `PUT /api/admin/settings/default-pool-user`：保存时调用

---

## Alembic 迁移

新建迁移文件，创建 `system_settings` 表。迁移幂等，`upgrade` / `downgrade` 均安全。

---

## 测试

- 单测：`get_default_pool_user_id()` 优先级逻辑（数据库有值 / 无值 fallback .env / 两者均无）
- 单测：`GET` / `PUT` API 正常路径 + 422（用户不存在）
- 单测：`config_warnings` 在数据库已配置时不触发 `no_default_pool`

---

## 向后兼容

已有 `.env` 中设置了 `DEFAULT_POOL_USER_ID` 的部署：数据库初始无记录，`get_default_pool_user_id()` fallback 读 `.env`，行为不变。管理员首次在页面保存后，数据库值生效，`.env` 值被覆盖（但不删除，仍作兜底）。
