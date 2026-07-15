# ticket-hub 项目完善路线图（缺失功能总计划）

> 制定日期：2026-07-14
> 背景：V1 由前同事完成核心骨架（ADR-0016 流水线重构 P0-P5），本计划是在 V1 基础上完善——把 ADR-0016 §3 流程图完整落地 + 打通真实业务链 + 补齐前端缺口。
> 依据：三轮全面盘点（已实现未验证 / 后端未落地 / 前端缺口）+ ADR-0016 流程图逐条对照。

## 现状基线（2026-07-14）

**SIT 已通**：入站（KSM 轻量 ping + 智齿信封 + AI 客服 escalation）、飞书 SSO、反思诊断 UI、classify LLM（GLM 兜底）、智齿信封解析。

**SIT 凭证现状**：
- ✅ 已配：`KSM_APP_ID/APP_SECRET`、`ZHICHI_APPID/APP_KEY`、`GLM_API_KEY`、`AI_CS_*`、`FEISHU_*`、`FEISHU_WIKI_*`
- ❌ 空缺：`KSM_HANDLER_NAME/NUMBER`（出站身份）、`DASHSCOPE_API_KEY`、`LINEAR_API_KEY/TEAM_ID`
- 灰度开关：全部 false + dry_run（除 `KNOWLEDGE_FEEDBACK_ENABLED=true`）

**SIT 数据实况**：KSM 工单 0、智齿 2（测试）、escalation 6（测试）、hub_issues 1、sync_outbox 0、attachments 0、owner-split 0。→ 六大链路（KSM/Linear/writeback/cascade/owner-split/vision）真实数据 0 记录。

**UAT 现状**：实质死机——worker 报 `no pg_hba.conf entry`，容器停在 07-01 老版本，落后 main 4 个迁移（0017-0020）。

---

## 阶段划分与优先级

按「先让业务端到端跑通，再补精细化功能」排序。P0-P1 是打通链路，P2-P3 是功能增强，P4 是辅助。

---

## 阶段 P0：环境修复（阻塞项，1-2 天）

### P0-1 UAT 环境救活
- **问题**：worker 刷屏 `no pg_hba.conf entry for host "106.55.57.40"`，写路径断连
- **动作**：
  1. UAT 宿主机 pg_hba.conf 放行容器网段（参照 `docs/memory/project_deployment.md`，之前放行过被删/失效）
  2. `pg_reload_conf()` 热重载
  3. UAT `git pull` + `docker compose up -d --build` 升级到 main
  4. `alembic upgrade head`（0016 → 0020，补 devcollab/skill三槽/owner-split/knowledge_op 表）
- **验证**：worker 日志无 pg_hba 报错；`alembic current` = 0020
- **前置**：需要 UAT 宿主机 root 权限
- **工作量**：小（运维操作）

### P0-2 celery zhichi 任务注册（已完成）
- ✅ 已修 `celery_app.py` include + SIT 重建验证生效（commit e34765e）

---

## 阶段 P1：核心业务链真打（3-6 天）

### P1-1 KSM 出站回写真打
- **前置**：SIT 配 `KSM_HANDLER_NAME` + `KSM_HANDLER_NUMBER`（KSM 侧处理人身份）
- **代码**：`services/ksm/writeback.py` 已完整（6 kind 映射 + lock→refresh→handle 时序）
- **动作**：
  1. `.env` 补 HANDLER 身份 → 重启
  2. 翻 `KSM_WRITEBACK_ENABLED=true`，保持 `DRY_RUN=true` → 观察组装 payload（log）
  3. 主管手动 `POST /api/supervisor/drain-ksm-writeback` 看成败
  4. 稳了翻 `DRY_RUN=false` → 真打 KSM 关单
- **验证**：一条真实 KSM 工单 → 主管回复 → 客户在 KSM 侧看到答复+关单；sync_outbox 行 pending→sent
- **工作量**：小（配置 + 灰度验证）

### P1-2 智齿出站回写真打
- **前置**：`ZHICHI_APPID/APP_KEY` 已配 ✅
- **代码**：Task 1-8 已完成 + celery 修复已生效
- **动作**：翻 `ZHICHI_WRITEBACK_ENABLED=true` 保持 dry_run → drain 观察 → 翻 dry_run=false
- **验证**：智齿工单主管回复 → save_ticket_reply → 客户在智齿看到答复
- **依赖**：需要一条智齿 Operation 工单毕业 hub_issue（当前 auto 关，走手动毕业）
- **工作量**：小

### P1-3 Linear 推送真打
- **前置**：SIT 配 `LINEAR_API_KEY` + `LINEAR_TEAM_ID`
- **代码**：`services/hub_issues/linear_push.py` 已完整（含 hub_dedup + team 路由）
- **动作**：
  1. 配 key → 翻 `LINEAR_PUSH_ENABLED=true`
  2. `POST /api/admin/users/sync-from-linear` 同步用户 team 映射
  3. 手动毕业一条 Bug_fix hub_issue → 看 linear_uuid 是否落上
- **验证**：hub_issue.linear_uuid 非空；Linear 侧有对应 issue；users.linear_team_id 有映射
- **工作量**：小-中

### P1-4 cascade → sync_outbox 端到端
- **代码**：`cascade/reply_sync.py` / `status_cascade.py` / `supply_sync.py` 已完整
- **依赖**：P1-1/P1-2 跑通后自然验证（主管回复触发 cascade 入队）
- **验证**：hub_issue 回复 → 每个有源工单入 outbox → drain 消费；hub_issue_reply_history 有版本
- **工作量**：无新代码，随 P1-1/P1-2 验证

---

## 阶段 P2：架构图未落地的自动分流（1-2 周）

对照 ADR-0016 §3 流程图，补齐图承诺但代码未做的分支。

### P2-1 hub_dedup 覆盖 Operation/Internal_task
- **问题**：图说「所有类型毕业时 hub_dedup」，代码里 `find_duplicate_hub` 只在 `linear_push.py` 内调用（Bug_fix/Demand）；Operation/Internal_task 毕业不查重 → 重复问题生成多个 hub_issue，occurrence_count 永远 1
- **改法**：把 `find_duplicate_hub` 调用从 linear_push 上移到 `creator.ensure_hub_issue_for_ticket` 内，所有类型毕业时统一查重；命中则挂靠复用 + occurrence_count+1
- **文件**：`services/hub_issues/creator.py` + `hub_dedup.py`
- **风险**：改动核心毕业路径，需充分单测 + 灰度（hub_dedup_enabled 已默认 true）
- **工作量**：中

### P2-2 Operation 未命中 + 非 escalation → 直接答复客户
- **问题**：图说 KSM/智齿的新 Operation 未命中现有 hub 时「直接答复客户」，代码里 `_route_by_type` 对 Operation 只毕业不答复
- **改法**：Operation 毕业后，若非 escalation 来源且 hub_dedup 未命中 → 触发自动答复流程（走 cascade → outbox）。需定义「答复内容从哪来」——可能需要一个 Operation 自动答复 skill 或复用 AI 客服
- **待澄清**：自动答复的内容来源（LLM 生成？知识库检索？固定模板？）——这块 ADR 没细化，需与产品确认
- **文件**：`webhooks.py` `_route_by_type` + 新答复逻辑
- **工作量**：中-大（依赖答复内容来源方案）

### P2-3 Demand 单/多责任人自动分流
- **问题**：图说 Demand 未命中要判「单/多责任人」自动走「推 1 Linear issue」或「owner-split N 子 issue」；代码里所有 Demand 都单 issue，owner-split 只主管手动
- **改法**：毕业 Demand 时根据责任人数量自动判定；多责任人时提示主管走 owner-split（v1 保持手动，因 ADR 明确 LLM 预拆留 v2）
- **注意**：ADR §6 明确「owner-split v1 触发=主管手动，LLM 预拆留 v2」——所以这里**不做全自动**，只做「检测到多责任人 → 提示/引导主管拆分」
- **文件**：`webhooks.py` + 前端 hub_issue 详情页提示
- **工作量**：中

### P2-4 Bug_fix 命中迭代中 → 跟踪 + 回写客户
- **问题**：图说命中已迭代的 Bug_fix 要「跟踪排期/发版 + 回写客户"问题在跟踪中"」；代码 supersede 链只挂靠去重，无主动回写
- **改法**：hub_dedup 命中已有 Bug_fix hub 时，给新关联的源工单 cascade 一条「您的问题已合并到 XXX，正在跟踪修复」的告知
- **文件**：`hub_dedup.py` 命中分支 + cascade
- **工作量**：中

### P2-5 Internal_task 飞书任务集成
- **问题**：图最简单一支「内部处理」，但 `feishu_task_id`/`feishu_task_status` 几乎无实现
- **改法**：Internal_task 毕业 → 建飞书任务 → 状态回同步
- **待澄清**：是否真需要飞书任务集成？还是 Internal_task 只在 hub 内部流转即可
- **工作量**：大（需接飞书任务 API）

---

## 阶段 P3：前端功能缺口（1-2 周，可与 P2 并行）

后端有端点但前端未接，或手册承诺但前端缺失。

### P3-1 出站回写手动触发 UI（drain 按钮）
- **缺**：`drain-ksm-writeback` + `drain-zhichi-writeback` 端点前端零调用
- **落点**：工作台 `WorkbenchPage` 加「回写状态」卡片（显示 KSM/智齿 outbox pending/sent/failed 计数 + 立即 drain 按钮）
- **工作量**：小

### P3-2 未分配 → 手动指派处理人
- **缺**：手册说「手动指定处理人」，前端只有「重新触发 AI 分配」
- **落点**：工单列表未分配视图加「指派给 X」下拉 + 调用指派端点
- **工作量**：中

### P3-3 hub_issue 详情页补 4 个协同动作入口
- **缺**：催办/发版通知/记录回访/登记自修复 bug 只在列表页，详情页无入口
- **落点**：`HubIssueDetailPage` 加动作区（Bug_fix/Demand 可见）
- **工作量**：小

### P3-4 非投诉工单手动毕业入口
- **缺**：低置信度普通工单无「手动毕业」按钮（只有投诉队列有 create-hub-issue）
- **落点**：工单详情页/列表批量加「毕业为 hub_issue」（可选 type）
- **工作量**：中

### P3-5 Operation 回复编辑器加前端权限 gate
- **缺**：任何登录用户都能看到「修改回复」按钮，靠后端 403 拦
- **落点**：`HubIssueDetailPage:192` OperationReplySection 加 `isSupervisor()` 判断
- **工作量**：小

### P3-6 revert-split / relink UI
- **缺**：撤销错拆、手工重挂 hub_issue 端点无前端入口
- **落点**：工单详情 timeline / hub_issue 详情页
- **工作量**：中

### P3-7 节假日日历 UI（SLA 工作日感知依赖）
- **缺**：`admin/holidays` 端点无前端；holidays 表 SIT 0 行 → SLA_WORKDAY_AWARE 开也 fallback
- **落点**：管理后台新 tab
- **工作量**：中

### P3-8 主管/搭档关系管理 UI
- **缺**：`user_supervisors`/`user_partners` 端点有，人员分工页无字段
- **落点**：`PeopleScopesPage` 加卡片
- **工作量**：中

### P3-9 assignee "我的工单" 视图
- **缺**：处理人角色无 assigned_user_id=me 默认筛选
- **落点**：工单列表默认筛选 + 导航
- **工作量**：小

### P3-10 "客户仍报错" → 升级新工单按钮
- **缺**：记录回访标 stillbad 后只显示红字，无「升级/重推研发」动作
- **落点**：hub_issue 详情/列表回访区
- **工作量**：中

---

## 阶段 P4：辅助能力 + 技术债（可选，按需）

### P4-1 KSM 附件下载 → MinIO → vision 闭环
- **缺**：`download_attachment` client 就绪，storage_key 无赋值代码，无 boto3/minio client
- **前置**：需要 MinIO/S3 可用 + `DASHSCOPE_API_KEY`（vision）
- **工作量**：中

### P4-2 sync_outbox zammad/ai_cs 消费端
- **缺**：cascade 给所有有源工单入队，zammad/ai_cs 永久 pending 累积孤儿
- **改法**：白名单（只对 ksm/zhichi 入队，短期）或补 sender（完备）
- **工作量**：小（白名单）/ 中（sender）

### P4-3 agent_decisions revert 补齐
- **缺**：dedup_link 缺 revert；classify_type 无 revert；merge_identity/relink 不写 agent_decisions
- **工作量**：中

### P4-4 知识反哺 knowledge/retrieval 病因自动核验
- **现状**：只有 skill 病因走 replay+publish 闭环；knowledge/retrieval 人工勾（依赖飞书 KB API）
- **工作量**：中（依赖飞书 KB 发布回调，可能受限）

### P4-5 文档一致性
- CLAUDE.md 阶段进度、DEPLOY-UAT.md 过时段落更新
- ticket_embeddings 表 + dedup_execute.py 存量清理（ADR-0016 P2e 退役但未删净）
- **工作量**：小

---

## 依赖关系图

```
P0-1 UAT救活 ─────────────────────┐
P0-2 celery修复 ✅                  │
                                    ▼
P1-1 KSM出站 ← 需 HANDLER 身份    生产可用
P1-2 智齿出站 ← 凭证已配 ✅          │
P1-3 Linear ← 需 LINEAR_KEY         │
P1-4 cascade ← 随 P1-1/P1-2 验证    │
                                    ▼
P2 架构自动分流（依赖 P1 出站跑通才能看效果）
   P2-1 hub_dedup 全类型（可独立）
   P2-2 Operation 直接答复（依赖 P1 出站 + 答复来源方案）
   P2-3 Demand 单/多责任人
   P2-4 Bug_fix 命中回写
   P2-5 Internal_task 飞书任务

P3 前端（可与 P2 并行，前后端解耦）
P4 辅助（按需）
```

## 待用户澄清的决策点

1. **P1 真打需要的凭证**：KSM_HANDLER_NAME/NUMBER、DASHSCOPE_API_KEY、LINEAR_API_KEY/TEAM_ID 你手上有吗？
2. **P2-2 Operation 自动答复内容来源**：LLM 生成 / 知识库检索 / 固定模板？（ADR 未细化）
3. **P2-5 Internal_task 是否真需要飞书任务集成**？
4. **同事 V1 是否还在开发**？避免改动冲突。
5. **优先级是否认可**：P0→P1→P2/P3 并行→P4

## 建议的第一步

**P0-1（救活 UAT）+ P1-2（智齿出站真打，凭证已齐最省事）** 作为起点——一个解决环境阻塞，一个用最小成本证明「入站→处理→出站」主链能端到端跑通，建立信心后再推 KSM 和架构增强。
