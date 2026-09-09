# 统一主状态 · 单一责任人 · 产品内提单 Implementation Plan

> 设计决策见 `docs/adr/0017-unified-stage-single-owner-embedded-ticket.md`。本文件是任务拆解与验收口径。分支 `feat/unified-state-and-embedded-ticket`，基线：origin/main `13ac3e9`（迁移 head 0046），backend 单测 1444 passed。

**Goal:** ① ticket 上一个 `stage` 字段看全后续处理；② 责任人收敛为「工单一人 + 研发一人（模块绑死）」；③ 产品内提单门户（租户鉴权 + CRU + 统计 + H5）。

**Tech Stack:** FastAPI + SQLAlchemy 2.x（before_flush 事件）+ Alembic；React 18 + Vite 多入口；pytest（SQLite in-memory）+ vitest。

## Global Constraints

- 三道人工闸门、Celery beat 范式、事务边界约定（service 不 commit）全部维持。
- `stage` 无 CHECK 约束（与 `hub_issues.status` 同策略，加值不迁移）；`status_history.entity_type` CHECK 扩 `'ticket_stage'`（迁移，`DROP CONSTRAINT IF EXISTS`）。
- 改后端 API 后 `make gen-types` 提交 `openapi.json` + `types.ts`。
- 每个 Phase 一个 commit（中文 `feat:`），全绿 `make test` 再提交。

---

## Phase 1 · 统一主状态（映射层）

### Task 1.1 派生函数（纯函数，先测后写）

- New: `backend/app/services/state/__init__.py`、`stage.py`
- 常量：`TICKET_STAGES` / `HUB_STAGES` / `LINEAR_STAGES`（frozenset，断言包含关系）、`STAGE_ZH`、`STAGE_TONE`
- `derive_hub_stage(hub) -> str`、`derive_ticket_stage(ticket, hub | None) -> str`
- 优先级（与 `frontend/src/api/processStage.ts` 对齐）：
  1. 退回：`ticket.status=='transferred_return'` 或 `hub.status in ('returned',)` 或 `op_status=='transferred_return'` → `returned`
  2. `ticket.status=='split'` → `split`
  3. 无 hub：`predicted_type=='Complaint'` → `complaint`；`ticket.status in ('done',)` → `resolved`；`('closed','rejected','superseded')` → `closed`；否则 `received`
  4. 闸门：`hub.status pending_review→pending_classify`、`pending_linear_review→pending_push`、`pending→pending_dispatch`
  5. Operation（`op_status` 非空）：processing→processing / reviewing→pending_answer_review / supplementing→supplementing / answered→answered / closed→closed / exception→exception
  6. `hub.status resolved→resolved`、`closed→closed`
  7. 研发类：linear canceled→canceled；`in review`→dev_review；`hub.status released` 或 linear done/completed/released→released；`hub.status in_progress` 或 linear started/backlog/unstarted → in_dev；其余 → processing
  8. Internal_task/其它：released→released，否则 processing
  9. 兜底：`hub.status` 漏进的 `answered`/`processing` 原样归入对应 stage
- Test: `tests/unit/services/state/test_stage_derive.py` 表驱动覆盖以上 9 条 + 格包含断言

### Task 1.2 迁移 0047 + 模型列

- `tickets.stage` String(32) nullable + `ix_tickets_stage`；`tickets.stage_changed_at`
- `hub_issues.stage` + `ix_hub_issues_stage`；`hub_issues.stage_changed_at`
- `status_history` CHECK 扩 `ticket_stage`
- 存量回填：迁移内 Python 分批（1000/批）调用 `derive_*`（env.py 已 import app.models，可 import 派生函数）
- Test: `test_models_stage.py` 断言列存在、`Base.metadata.create_all` 通过

### Task 1.3 before_flush 监听器

- New: `backend/app/services/state/listeners.py`，在 `app/models.py` 末尾 `import app.services.state.listeners  # noqa` 注册到 `Session` 类级事件（覆盖所有 sessionmaker，含测试）
- 触发条件：session.new/dirty 里的 Ticket 或 HubIssue，且 watched 属性 `has_changes()`（Ticket: status/predicted_type/hub_issue_id/type；HubIssue: status/op_status/linear_status/type）
- hub 变化 → 用 `session.no_autoflush` 查 `Ticket.hub_issue_id==hub.id` 的 ticket 一并重算
- stage 变化 → `stage_changed_at=now` + `StatusHistory(entity_type='ticket_stage', changed_by=session.info.get('stage_actor','system:stage_sync'), reason='<驱动字段>: <from>→<to>')`
- Test: `test_stage_listener.py`：a) 新建 ticket flush 后 stage=received；b) hub.op_status 变 answered → ticket.stage=answered 且写一条 ticket_stage 历史；c) 无变化的 flush 不写历史（幂等）；d) `session.info['stage_actor']` 生效

### Task 1.4 对账脚本

- New: `scripts/state/reconcile_stage.py [--fix] [--limit N]`：全表比对，输出 `drift: ticket#id stored=X derived=Y`；`--fix` 写回并记 `system:stage_reconcile`
- Test: 纯函数 `find_drift(rows)` 单测

### Task 1.5 API 暴露

- `TicketSummary`/`TicketDetail`：`stage: str | None`、`stage_label: str | None`、`stage_changed_at`
- `HubIssueSummary`：同上三字段
- `GET /api/tickets`：新增 `stage: str | None` / `stages: list[str] | None` 筛选（repository 加 where）
- `GET /api/tickets/{id}/history`：`ticket_stage` 行以 `kind="stage"` 合并进 items（`from_status_zh/to_status_zh` 走 `STAGE_ZH`）
- `history_labels.py`：`STAGE_ZH` 引用 `services/state/stage.py`（单一定义）
- `make gen-types`
- Test: 扩 `test_tickets_api.py`（列表有 stage、stage 筛选生效）、`test_ticket_history_api.py`（stage 事件出现）

### Task 1.6 前端消费 stage

- `processStage.ts`：新增 `STAGE_LABEL/STAGE_TONE` 表 + `stageToProcessStage(stage)`；`computeProcessStage(input)` 当 `input.stage` 存在时直接查表，旧逻辑保留为回落
- `TicketsListPage`/`TicketDetailPage`/`HubIssuesListPage`/`HubIssueDetailPage` 传 `stage`
- Test: `processStage.test.ts` 加 stage 快路径用例

**Phase 1 验收**：`make test` 全绿；`reconcile_stage.py` 对测试库 0 drift；工单列表「处理状态」列改由 `stage` 驱动且显示不变。

---

## Phase 2 · 责任人收敛

### Task 2.1 模块单一研发责任人

- 迁移 0048：`modules.dev_owner_user_id` INT FK users nullable + index；Python 回填：`dev_owners` 第一个姓名精确匹配 `users.name`（active）→ 写 id，匹配不到留空并打印清单
- `module_owner.py`：新增 `resolve_module_owner(db, plc, module) -> User | None` 只读 `dev_owner_user_id`；`peek_module_owner`/`consume_module_owner` 改为它的别名（consume 不再推进游标），保留符号让 6 个调用点零改动
- `admin_catalog.py`：`ModuleOut/ModuleIn/ModulePatch` 加 `dev_owner_user_id` + `dev_owner_user_name`；`dev_owners` 字段保留输出但标 deprecated
- 前端目录管理页：研发责任人改为用户下拉单选（复用人员列表接口）
- Test: `test_module_owner.py` 改写；`test_admin_catalog.py` 加字段

### Task 2.2 停写 deprecated 字段

- 5 个 ingester：只写 `handler_user_id`，删除 `ticket.assigned_user_id = dr.user_id`
- `creator.py`：Operation 分派结果只写 `set_hub_tickets_handler`，不再写 `op_handler_user_id`；研发类分派写 `owner_user_id`（不写 hub.assigned_user_id）
- `op_status.resolve_op_handler`：改读 `Ticket.handler_user_id`（hub 下第一条 ticket），回落 default_pool
- `linear_push.py`/`webhook_push.py`：assignee 解析链 = override > `resolve_module_owner` > None（去掉 `hub.assigned_user_id` 回落，无人则置 `pending_push`）
- `TicketSummary.assigned_user_*`：保留字段，值改为「已毕业研发类 → hub.owner_user_id；否则 None」，注释标 deprecated；新增 `owner_user_id/owner_user_name`
- Test: 对应单测更新；`test_dispatch_integration.py` 断言 assigned 不再被写

### Task 2.3 AI actor 规范

- 检查 `status_history.changed_by` 写入点，Agent 侧统一 `agent:<name>`（现有 `agent:hub_issue_auto`/`agent:dispatch`/`agent:linear_push` 已合规；`op:agent` → 保留 `op:` 前缀但 handler 固定 `agent`）
- `history_labels._ACTOR_SLUG_ZH` 补齐新前缀中文

**Phase 2 验收**：新入库工单 `assigned_user_id IS NULL`；推研发 assignee 只来自模块单一责任人或手选；`make test` 全绿。

---

## Phase 3 · 产品内提单门户

### Task 3.1 模型与迁移 0049

- `tenants`：id / code(unique) / name / hmac_secret(String 128) / is_active / created_at / updated_at
- `tenant_users`：id / tenant_id FK / external_uid / name / mobile / email / customer_identity_id FK nullable / last_seen_at / created_at；`uq(tenant_id, external_uid)`
- sources 种子 `('embedded','产品内提单',true)`（`ON CONFLICT DO NOTHING`）
- config：`portal_jwt_ttl_seconds=7200`、`portal_sign_skew_seconds=300`、`portal_enabled=True`

### Task 3.2 鉴权

- New: `app/api/deps/portal_auth.py`：`PortalUser(tenant_id, tenant_user_id, external_uid)`；`require_portal_user` 校验 `aud=='portal'`；员工 JWT 无 aud → 401
- `POST /api/portal/auth/token`：body `{tenant_code, external_uid, name?, mobile?, email?, ts, sign}`；`sign = HMAC_SHA256(secret, f"{tenant_code}.{external_uid}.{ts}")` hex；`|now-ts| ≤ skew`；`hmac.compare_digest`；upsert `tenant_users` + IdentityResolver 落 `customer_identities`；返回 `{token, expires_in}`
- Admin：`/api/admin/tenants` GET/POST/PATCH + `POST /{id}/rotate-secret`（require_admin；secret 仅在创建/轮换响应中返回一次）
- Test: 签名正确/过期/错误、aud 隔离、admin 权限

### Task 3.3 门户工单接口（CRU）

- New: `app/services/ingest/portal_ingester.py`（镜像 zammad_ingester：identity → upsert_catalog → Ticket(source='embedded', source_ticket_id=f"{tenant_code}:{uuid4}") → dispatch_handler 只写 handler → status_history）
- New: `app/api/portal.py`，prefix `/api/portal`：
  - `POST /tickets` → ingester + `BackgroundTasks(run_post_ingest_agents)`
  - `GET /tickets?stage=&page=` 仅本 tenant_user 的单，返回 `short_code/title/stage/stage_label/created_at/updated_at`
  - `GET /tickets/{id}` 详情：body、stage、`cached_reply_content`（答复）、`timeline`（`ticket_stage` 历史投影：stage/label/at）
  - `PATCH /tickets/{id}` 更新 title/body，仅 `stage in (received, pending_classify, supplementing)`；写 `status_history(entity_type='ticket')` 审计
  - `POST /tickets/{id}/supplement` 追加补充资料段到 body（`[提单人补充 @时间]`）；若 hub `op_status=='supplementing'` → `apply_op_status(processing, handler=hub.op_handler or 'agent', reason='提单人补充资料')`
  - `GET /stats` 本人各 stage 计数 + 总数
  - 无 DELETE
- 行级隔离：所有查询 `Ticket.reporter->>'tenant_user_id' == me`？→ 改为落列：`tickets.tenant_user_id` INT FK nullable（迁移 0049 一并加），查询走索引
- Test: `tests/unit/api/test_portal_api.py` 覆盖 CRU、跨用户 404、D 不存在(405)、supplement 触发 op_status 回 processing

### Task 3.4 H5 门户前端

- `frontend/portal.html` + `src/portal/main.tsx`（HashRouter，独立 QueryClient，不用 tabs）
- 页面：`TokenGate`（读 `#token=` 或 `?token=` 落 `localStorage.portal_token`）→ `MyTicketsPage`（stage chip 分组 + 搜索）→ `TicketDetailPage`（竖向 stage 时间轴 + 答复 + 补充资料表单）→ `NewTicketPage`
- `vite.config.ts`：`build.rollupOptions.input = { main: 'index.html', portal: 'portal.html' }`
- `src/portal/api.ts` 独立 fetch 封装（`Authorization: Bearer <portal_token>`，401 → 提示重新进入）
- Test: `src/portal/stage.test.ts`（stage 分组）+ 1 个渲染测试

### Task 3.5 内部侧可见性

- 工单列表来源筛选加「产品内提单」；`history_labels` 来源中文
- 管理页新增「租户接入」tab（admin-only，`RequireAdmin`）：列表 / 新建 / 轮换密钥（密钥只显示一次）

**Phase 3 验收**：curl 走完 签名换 token → 建单 → 列表 → 详情 → 补充 → 统计；后台看到该单走完 AI 链并有 stage 时间轴；`make test` 全绿。

---

## 回滚

- Phase 1：`alembic downgrade 0046`；监听器 import 行删除即失效；前端 `computeProcessStage` 旧逻辑仍在。
- Phase 2：`dev_owner_user_id` 为空时 `resolve_module_owner` 回落 `dev_owners` 首名（过渡期保留一版）。
- Phase 3：`portal_enabled=False` 让 `/api/portal/*` 全 404；表可留。
