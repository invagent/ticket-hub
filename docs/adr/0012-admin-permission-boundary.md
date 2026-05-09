# ADR-0012: admin 权限边界与飞书用户同步策略

- 状态：Accepted (D2-E)
- 日期：2026-05-09
- 决策依据：D2-E 飞书用户同步上线时遇到的权限/隐私权衡

## 背景

D2-E 阶段引入了两类管理员能力：

1. **users CRUD**：查看 / 编辑 role / 停用 / 设主管 / 配 partner
2. **从飞书批量同步用户**（按部门浏览 / 多选 → 写入本地 `users` 表）

这些操作触及组织敏感数据（员工档案、上下级关系），需要明确：

- 谁可以做（role 边界）
- 写入什么（哪些字段被覆盖、哪些被保护）
- 审计什么（谁改了谁的什么）
- 飞书数据范围与本地数据的关系

## 决策

### 1. 操作分级

| 操作                                   | 最低 role     | 备注 |
|--------------------------------------|-------------|------|
| 看 `/admin/users` 列表 / 详情              | `admin`      | 不开放给 supervisor 防越权 |
| 改 user 的 role                         | `admin`      | 自己不能把自己降级（防最后一个 admin 锁死） |
| 停用 (`DELETE`，soft-delete)              | `admin`      | 自己不能停用自己 |
| 改 user 的 profile 字段（name/email/...）   | `admin`      | 不影响 role |
| 设/清主管（`/supervisor`）                  | `admin`      | supervisor 自己不能编辑下级关系 |
| 加/减 partner                          | `admin`      | 对称配对，加减都同时操作两端 |
| 改 module/feature scope (D1 已有)        | `admin`      | 对应路由分工（决策 D20） |
| 调用 `/sync-from-feishu`                 | `admin`      | 写库高敏感操作 |
| 浏览 `/feishu/departments[/{id}/users]`  | `admin`      | 只读飞书 API；本地数据不变 |

**没有"二级管理员"概念**。多 admin 平等。如果未来有部门级 admin，新增 `department_admin` role 在新 ADR 里讨论。

### 2. 飞书同步的"不破坏"策略

`feishu_user_sync.py` 中的 upsert 规则：

| 字段                                | 同步行为 |
|-----------------------------------|----------|
| `feishu_uid` (open_id)            | 不变（lookup key） |
| `name` / `email` / `mobile` / `employee_no` | 飞书有非空值时**覆盖**，否则保留 |
| `role`                             | **永不覆盖**，admin 必须手动改 |
| `is_active` / `deleted_at`         | 飞书还活跃 → 自动 revive；飞书已停用且本地有 → 不动（admin 决策） |
| `ksm_account` / `zhichi_agent_id` / `linear_user_id` | 永不覆盖（飞书没这些字段） |

理由：

- role 是业务决策（谁是 admin、谁是 supervisor），HR 系统（飞书）不应该决定
- 多源 ID（KSM/Zhichi/Linear）由 admin 在 `/admin/users/:id` 编辑或由 IdentityResolver 自动填充

### 3. 审计

D2-E **暂不**新增 `user_role_history` 表。所有 admin 写操作通过 structured logger 输出：

```
admin_user_updated     target_user_id=X by=Y fields=[...]
admin_user_soft_deleted target_user_id=X by=Y
admin_user_supervisor_set target_user_id=X supervisor_id=S deputy_supervisor_id=D by=Y
admin_user_supervisor_cleared
admin_user_partner_added/removed
admin_user_feishu_sync_done by=Y new=N updated=U revived=R errors=E
```

升级路径（如有审计合规要求）：D6 时基于 `status_history` 加一类 `entity_type='user'`，
或单独引入 `user_role_history` 表。当前 logger 已经能满足 PR 复盘 + 应急溯源。

### 4. 自我保护逻辑

- `PATCH /api/admin/users/{id}` 中如果 `id == 当前 admin`，且 `body.role` 不为 `"admin"`，返回 400（"cannot demote yourself"）
- `DELETE /api/admin/users/{id}` 中如果 `id == 当前 admin`，返回 400（"cannot soft-delete yourself"）
- `set_supervisor` 校验 `supervisor_id != user_id` 和 `deputy_supervisor_id != user_id`
- `add_partner` 校验 `partner_id != user_id`

这些是**最后一道软防护**；管理员之间互相恶意降级 / 停用仍然可行（有 audit log 兜底）。

### 5. 飞书数据范围 vs 本地数据范围

飞书的"通讯录数据范围"决定**哪些用户的 name 字段会返回给应用**。本地 `users`
表则可包含数据范围之外的用户（例如：通过 KSM 工号识别后由 IdentityResolver 自动建账）。

**结论**：本地 users 表是 **superset of 飞书可见员工**。同步只是其中一种来源。
不会因为飞书把某员工移出数据范围就删本地 users 行（仅 `sync` 时新数据进不来）。

### 6. 飞书 tenant 权限要求

为了让 `/sync-from-feishu` 真正拿到 `name` 字段，飞书应用必须配置：

- **应用身份权限**（不是用户身份）：
  - `contact:contact:readonly_as_app`（"获取通讯录"，高敏感，需管理员审批）
  - 或 `contact:user.base:readonly`
- **数据范围**：通讯录数据范围至少包含要同步的部门

仅配 `contact:user.basic_profile:readonly`（用户身份权限）**不够** —— 它只对
OAuth 用户授权（SSO 登录）流程生效，对 tenant_access_token 调用 contact API 无效。

如果上述权限暂时拿不到，**前端会 fallback 用工号 / 邮箱前缀作为 name 占位符**
（admin 后续可手动改名）。

## 后果

- /admin/users 系统按这个规则上线，前端约束 + 后端兜底
- 有新 admin 加入流程后续可能需要"双 admin 互审"机制（暂不做）
- 飞书权限申请方迭代发布前必须重新走"版本审核 → 发布上线"流程
- 大型组织（>1k 员工）后续如要走全员同步，需要把 `/sync-from-feishu` 改成 Celery 异步任务（D6 之前补）
