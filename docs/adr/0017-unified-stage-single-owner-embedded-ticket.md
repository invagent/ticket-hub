# ADR-0017：统一工单主状态（stage）· 单一责任人 · 产品内提单门户

- 状态：已采纳（2026-09-09）
- 分支：`feat/unified-state-and-embedded-ticket`
- 实施计划：`docs/superpowers/plans/2026-09-09-unified-state-and-embedded-ticket.md`

## 背景

### 状态字段碎片化

截至 2026-09（迁移 head 0046），一条工单的「处于什么阶段」要同时看 4 个字段：

| 字段 | 取值（实测代码中出现过的） | 谁在写 |
|---|---|---|
| `tickets.status` | received / linked / waiting_reply / in_progress / replied / done / closed / split / released / rejected / superseded / transferred_return | ingester、split、ksm/zhichi writeback、supervisor、status_cascade |
| `hub_issues.status` | created / pending_review / pending_linear_review / pending / in_progress / released / resolved / closed **/ answered / processing / returned** | 27 处裸赋值（supervisor.py 10 处、hub_issues.py 7 处、creator.py 3 处、linear_push.py 3 处、writeback 4 处）+ `apply_hub_status` |
| `hub_issues.op_status` | processing / answered / closed / supplementing / reviewing / exception / transferred_return | `apply_op_status`（基本收敛） |
| `hub_issues.linear_status` | 镜像 Linear 列名 | linear_status_sync |

问题：

1. `hub_issues.status` 已经漏进 Operation 的 `answered`/`processing`（子任务确认接口写的），与 `op_status` 双写同一语义。
2. 「单一入口」`apply_hub_status` 只覆盖 4 个调用点，其余 27 处绕过，级联/审计各写各的。
3. 前端 `processStage.ts` 用 150 行优先级规则把 4 个字段折成一个展示态，后端没有对应物 → 列表筛选、统计口径、对外接口各自再算一遍。
4. Ticket 是大全集（毕业前只有 ticket；毕业后挂 hub；推研发后挂 Linear），但没有任何一个字段能在 ticket 上一眼看到「后续处理走到哪了」。

### 责任人字段冗余

| 字段 | 当前语义 | 写入方 |
|---|---|---|
| `Ticket.assigned_user_id` | 入库路由责任人（语义固定） | 5 个 ingester（与 handler 同值双写） |
| `Ticket.handler_user_id` | 当前实际处理人 | ingester、manual_assign、set_hub_tickets_handler |
| `HubIssue.assigned_user_id` | 入库路由责任人（hub 侧镜像） | creator、subtask confirm、dispatch |
| `HubIssue.owner_user_id` | 研发责任人 | linear_push、_mark_pending_linear_review |
| `HubIssue.op_handler_user_id` | Operation 处理人镜像 | creator（Operation 分派） |
| `HubIssue.op_handler` | 字符串（'agent' 或姓名） | apply_op_status |
| `modules.dev_owners` + `dev_owner_rotation_cursor` | 逗号分隔姓名 + 轮询 | 目录管理页 |
| `assignment_scopes_module/feature` | 旧 Router 分工 | 管理页（主链已不用） |

「一个模块多研发轮询」违背研发责任一致性；按**姓名**字串匹配用户既脆弱又无法审计。

### 产品内提单

现有 5 个来源全是外部系统 webhook 推入。缺少「在我们自己的产品内提单、并以提单人视角跟踪」的能力：租户鉴权、个人身份、CRU 接口、H5 详情页。

## 决策

### D1 · 单一主状态 `stage`，格结构 Ticket ⊇ Hub ⊇ Linear

新增 `tickets.stage` 与 `hub_issues.stage`（String(32)，无 CHECK，同 `hub_issues.status` 的做法）。取值集合是一个有明确包含关系的三层格：

```
TICKET_STAGES（大全集，18 个）
  received            已接收（入库/AI 分诊中，未毕业）
  complaint           投诉待人工（Complaint 停 ticket 层）
  split               已拆分（历史 Parent 工单）
  ─── 以下 15 个 = HUB_STAGES ───
  pending_classify    待确认分类（闸门①）
  pending_dispatch    待指派（分派无人 / Linear 查无此人）
  processing          处理中（Operation 处理 / 研发类已毕业未推）
  pending_answer_review 答复待审核（闸门②）
  supplementing       待客户补充资料
  pending_push        待确认转研发（闸门③）
  ─── 以下 4 个 = LINEAR_STAGES ───
  in_dev              研发中（Backlog/Unstarted/Started）
  dev_review          测试中（In Review）
  released            已发版
  canceled            已取消（Linear canceled）
  ───
  answered            已答复（Operation 观察期）
  resolved            已解决
  closed              已关闭
  returned            已退回（转单退回 / KSM 退回）
  exception           处理异常
```

`stage` 是**派生量**，由 `derive_ticket_stage(ticket, hub)` / `derive_hub_stage(hub)` 两个纯函数从旧 4 字段确定性算出（`app/services/state/stage.py`）。优先级与前端 `processStage.ts` 现有规则一致，后端成为唯一权威，前端改为消费 `stage`。

### D2 · 方案 A 先行（映射层），方案 B 分步收口

用户给了两个方向：A 维持旧字段读写、加映射对账；B 重写。**先 A 后 B**：

- **A（本 ADR 落地）**：SQLAlchemy `before_flush` 监听器（`app/services/state/listeners.py`）在任何 session 的 flush 前，对本次变脏的 Ticket/HubIssue 重算 `stage`，hub 变化自动传播到其挂载的所有 ticket。**27 处裸写零改动即被覆盖**。stage 变化写 `status_history(entity_type='ticket_stage')`，actor 从 `session.info['stage_actor']` 取，默认 `system:stage_sync`。
- **对账**：`scripts/state/reconcile_stage.py` 比对「存储 stage」与「派生 stage」，报告漂移，`--fix` 修正。任何监听器漏网都会在这里暴露。
- **B（后续）**：① 27 处 `hub.status =` 逐一改走 `apply_hub_status`；② `hub_issues.status` 剔除 Operation 值；③ `op_status`/`ticket.status` 降级为 `stage` 的派生视图，最终由 `apply_stage()` 单入口反向驱动旧字段。B 不在本 ADR 内承诺完成时间，但 A 的派生函数就是 B 的状态机定义，不会重写两遍。

### D3 · 责任人：工单一人、研发一人、模块绑死

- **工单环节唯一处理人** = `Ticket.handler_user_id`。
- **研发环节唯一责任人** = `HubIssue.owner_user_id`，来源 = `modules.dev_owner_user_id`（新增 FK，单人）。
- 责任人解析链固定为：手工指定（确认推送时选人）> `modules.dev_owner_user_id` > 无（停 `pending_push`/`pending_dispatch` 等人）。不再有 hub 责任人回落、不再轮询。
- **标记 deprecated（停写、只读兼容、后续迁移删列）**：`Ticket.assigned_user_id`、`HubIssue.assigned_user_id`、`HubIssue.op_handler_user_id`、`modules.dev_owners`、`modules.dev_owner_rotation_cursor`。`HubIssue.op_handler` 保留但语义收窄为「动作执行者标签」（`agent` / 用户名），不再当责任人用。
- **AI 优先**：链路各 checkpoint 的 actor 是 Agent 时统一写 `agent:<name>`（`status_history.changed_by`、`agent_decisions`），不落任何人员；只有人工确认/转交动作才出现 `user:<name>`。不新增 Agent 环节。

### D4 · 产品内提单门户（embedded）

- 新来源 `embedded`（产品内提单），复用 `tickets` 表与全部 AI 链（`run_post_ingest_agents`）。
- 租户模型：`tenants`（code / name / hmac_secret / is_active）+ `tenant_users`（tenant_id / external_uid / name / mobile / email / customer_identity_id）。租户用户通过 `IdentityResolver` 落 `customer_identities(source_code='embedded', source_user_id=external_uid, source_custom_id=tenant_code)`，与现有客户图谱打通。
- 鉴权两层：租户服务端用 HMAC-SHA256 对 `(tenant_code, external_uid, ts)` 签名换取**门户 JWT**（`aud=portal`，2h）；门户 JWT 只能访问 `/api/portal/*`，天然与内部 `/api/*` 的员工 JWT 隔离（`require_portal_user` 校验 aud）。
- 能力边界：提单人 **C/R/U**（创建、查看自己的单、更新标题正文与补充资料），**不提供 D**。
- H5：独立 Vite 入口 `frontend/portal.html` → `src/portal/`，HashRouter，移动优先，不进多标签框架。

## 后果

- **正面**：一个字段回答「这单到哪了」；前后端筛选/统计/对外接口口径统一；责任人可审计可绑死；产品内提单复用全部 AI 链零新 Agent。
- **代价**：监听器在每次 flush 多一次派生计算（仅对脏 Ticket/HubIssue，成本可忽略）；过渡期 `stage` 与旧字段并存需靠对账脚本兜底；deprecated 列删除需等一次完整发布周期。
- **风险**：监听器内查询关联 ticket 需 `no_autoflush`，避免递归 flush；迁移 0047 用 Python 回填存量 stage，行数上万时需分批。
