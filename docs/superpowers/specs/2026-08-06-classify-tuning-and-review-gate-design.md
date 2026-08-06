# 分类优化（基础报错降级）+ 研发类推 Linear 前人工确认闸门

**日期**：2026-08-06
**范围**：triage 分类 prompt + hub_issue 毕业/推 Linear 链 + 主管工作台
**类型**：prompt 优化 + 新增人工确认闸门 + 人工重分类入口

## 背景

工单 5915（`获取发票云平台 app_token 异常 / 移动云初始化失败`）被 triage 判为 **Bug_fix 置信度 0.95**（理由"系统初始化失败，明确报错"），自动毕业成 Bug_fix hub（HUB-000482）。这是误判：

- `app_token 异常`、`初始化失败`、`XX 无法调用 YY` 这类**基础平台鉴权/连接报错**，大概率是**客户侧配置/凭证填错**，属于 Operation（配置咨询），而非平台 bug。
- 领域先验：基础平台能力若真为 bug，影响面极大、早会爆发；单客户报错压倒性倾向配置问题。
- 当前 triage prompt 规则 4「报错区分性质」把 `初始化失败`/`plugin not found` 归到了「程序级异常 → Bug_fix」侧，缺「基础平台鉴权/连接/部署类报错优先 Operation」这条判据。

误判的 Bug_fix 会自动毕业并（生产 `linear_push_enabled=true` 时）**自动推 Linear，污染研发队列**。SIT 现状 `hub_issue_auto_enabled=true / confidence≥0.80` 自动毕业、`linear_push_enabled=false`（故 5915 未真推 Linear）；生产 push 为 true。

## 目标

1. **triage prompt 优化**：基础平台报错（token/鉴权/初始化/调用失败/参数配置 + `plugin not found`）优先判 Operation；收紧 Bug_fix 至「明确程序级异常」。
2. **研发类推 Linear 前人工确认闸门**：agent 自动毕业的 Bug_fix/Demand 不自动推 Linear，进主管审核队列，确认后才推。
3. **人工重分类**：主管在队列可改判误分类的工单；改判成 Operation 的自动回炉走答复链。

## 非目标

- 不处理「已推 Linear 后才发现要改判」（闸门拦在推之前，无 Linear 脏数据；已推的极端情况本期不覆盖）。
- 不改 Operation 自动答复链、准确率闸门（改判来的 Operation 自然复用它们）。
- 主管**手动**毕业（create-hub-issue）视为已确认，不进闸门、直推。

## 详细设计

### ① triage prompt 优化（`prompts/triage.md`，走 draft→回放→promote）

改**规则 4**（报错性质区分），当前：
> 明确业务原因（权限不足…）→ Operation；程序级异常（堆栈、500、plugin not found、"服务器异常"）、数据错乱 → Bug_fix。

改为（增补基础平台报错降级 + 收紧程序级异常定义）：
> **报错性质三分**：
> (a) 明确业务原因（权限不足、资质限制、"不支持在 X 模式下开具"）→ Operation；
> (b) **基础平台鉴权/连接/部署类报错**——`app_token/token 异常`、`密钥/appid/secret 错误`、`鉴权失败`、`初始化失败`、`XX 无法调用 YY`、`连接/对接失败`、`参数配置错误`、`证书过期`、`plugin not found`（多为包未部署）——**优先 Operation**（大概率客户侧配置/凭证/部署问题，由 AI 客服/主管先核查配置）；
> (c) **仅明确的程序级异常**——空指针（NullPointerException）、数组越界、堆栈报错、500、"服务器异常" 且无配置嫌疑——才判 Bug_fix。
> **例外升级回 Bug_fix**：客户明确说明「配置已核对无误仍报错」，或伴随 (c) 类程序异常证据。

新增 few-shot：
- `app_token 异常 / 移动云初始化失败` → Operation（要点：基础平台鉴权/初始化报错优先配置问题）
- `plugin not found` → Operation（要点：可能是补丁/包未部署，先核查部署，非代码 bug；除非伴随明确堆栈）
- 保留并对照现有 `plugin not found → Bug_fix` 的旧 few-shot（第 78-82 行需**改**，否则与新规则矛盾）

验证：用历史真实工单（含 5915、及过往判 Bug_fix 的样本）跑 `draft_validator` current vs draft 回放，确认 5915 翻成 Operation 且无大面积回归，再 promote。

### ② 研发类推 Linear 前人工确认闸门

**配置**（`app/config.py`）：`require_review_before_linear: bool = True`（默认开）。

**hub 状态**：`hub.status` 无 CheckConstraint（自由字符串），直接引入取值 `pending_review`（待分类确认）。**无需迁移**。

**插入点**（`services/hub_issues/creator.py:217-219`）：现状
```python
if result.created and result.type in ("Bug_fix", "Demand"):
    push_hub_issue_to_linear(result.hub_issue_id)
```
改为：
```python
if result.created and result.type in ("Bug_fix", "Demand"):
    if settings.require_review_before_linear:
        # agent 自动毕业的研发类 → 进 pending_review 待主管确认，不自动推 Linear
        _mark_pending_review(db, result.hub_issue_id, reason="研发类待主管确认分类后推 Linear")
    else:
        push_hub_issue_to_linear(result.hub_issue_id)
```
`_mark_pending_review`：把 hub.status 置 `pending_review` + 写 status_history（changed_by="agent:hub_issue_auto"）。只作用于**自动路径**（`create_hub_issue_for_ticket_auto`）；主管手动 `create-hub-issue` 路径不经过这里，天然直推（视为已确认）。

> **已核实**：主管手动 `create-hub-issue`（`supervisor.py` 的 create-hub-issue 端点）有**自己独立的** `push_hub_issue_to_linear` 调用（其内部 `if result.created and result.type in ("Bug_fix","Demand")` 块），与 `creator.py:217` 的自动路径分开。本方案**只给自动路径加闸门**，这个手动端点**故意保持不加闸门、直推**（主管手动毕业即已确认）。实现时勿误改此端点。

### ③ 主管审核队列 + 三动作

**队列端点**（`app/api/supervisor.py`）：`GET /api/supervisor/pending-classification`（require_supervisor）——列 `type∈(Bug_fix,Demand) AND status='pending_review' AND 未删除`，带 short_code/title/body/predicted_type/confidence/最近 classify 决策的 reason。

**三个动作端点**（require_supervisor）：

1. **确认放行** `POST /api/supervisor/confirm-classification` `{hub_issue_id}`：
   hub.status `pending_review→created` + status_history（"主管确认分类"）→ 调 `push_hub_issue_to_linear(hub_issue_id)`。

2. **改判分类** `POST /api/supervisor/reclassify` `{hub_issue_id, new_type, reason}`：
   - `new_type=Operation` → hub.type=Operation + status=created + **op_status=processing + op_handler=agent**（下轮 `drain_operation_auto_reply` 自动扫到跑答复链，复用现有，自动经准确率闸门）；清 Bug_fix/Demand 专属残留（如无）。
   - `new_type∈(Demand,Bug_fix)`（互相改判）→ 改 type，仍留 `pending_review` 待确认（改完还得确认才推 Linear）。
   - `new_type=Internal_task` → 改 type + status=created（内部任务不推 Linear、不走答复）。
   - `new_type=Complaint` → 转投诉人工流程（复用现有 close-complaint 语义或置人工待处理）。
   - 写 `classify_type` 修正审计（changed_by=`user:{name}`，human_confirmed=true）+ status_history。
   - **改判不推 Linear**（除非后续走确认放行）。

3. **误报关闭** `POST /api/supervisor/dismiss-classification` `{hub_issue_id, reason}`：
   hub.status→`closed`（或软删）+ status_history（"主管判误报关闭"）。不推 Linear、不走答复。

### ④ 前端

- **工作台**：新增「待确认分类」队列卡片（区别于既有 pending-linear/reviewing/split 等；另取一色）。每卡显示 short_code、标题、正文摘要、AI 判的类型+置信度+理由，三个按钮：确认推送 / 改判（下拉选 new_type）/ 误报关闭。
- **OpStatusBadge / hub 列表**：`pending_review` 状态展示（如"待确认分类"徽章）。
- `make gen-types` 同步新端点类型。

## 影响面清单

| 文件 | 改动 |
|---|---|
| `prompts/triage.md` | 规则 4 三分 + 收紧 Bug_fix + 改/加 few-shot（draft→回放→promote，不改代码） |
| `app/config.py` | +`require_review_before_linear: bool = True` |
| `app/services/hub_issues/creator.py` | 自动推 Linear 处加门 + `_mark_pending_review` |
| `app/api/supervisor.py` | +4 端点：pending-classification / confirm / reclassify / dismiss |
| `frontend/src/pages/.../WorkbenchPage.tsx` | +待确认分类队列 + 三动作 |
| `frontend/src/components/OpStatusBadge.tsx` 或 hub 状态展示 | +pending_review 展示 |
| `frontend/src/api/types.ts` | gen-types |

**无需数据库迁移**（hub.status 无约束；op_status=processing 是既有合法值）。

## 测试

- **prompt 回放**：5915 及 app_token/初始化/plugin-not-found 类样本 → Operation；空指针/500/数据错乱类 → 仍 Bug_fix；历史 Bug_fix 样本无大面积翻转（回归门槛）。
- **闸门单测**：
  - `require_review_before_linear=true` + agent 自动毕业 Bug_fix → status=pending_review、**未调 push_hub_issue_to_linear**。
  - `=false` → 直推（同现状）。
  - 主管手动 create-hub-issue 研发类 → 不受闸门影响、直推。
- **动作单测**：
  - confirm → status created + 调 push。
  - reclassify→Operation → type=Operation + op_status=processing + op_handler=agent（下轮 drain 可扫到）+ 修正审计。
  - reclassify→Demand → type 改、仍 pending_review。
  - dismiss → status closed，不推不答。
- **队列端点**：列 pending_review 研发类 + 鉴权 403。
- **回归**：Operation 自动答复链、准确率闸门、split、hub_dedup 不受影响。

## 迁移与上线

1. 无 DB 迁移。`git pull` + 重建后端 + 前端 rebuild。
2. prompt 优化：draft→回放验证→promote（可独立于代码先行/后行）。
3. `require_review_before_linear` 默认 true，部署即生效——研发类自动毕业转待确认。生产 `linear_push_enabled=true` 环境上线后，误判不再自动污染 Linear。
4. 回退：`require_review_before_linear=false` 恢复自动直推。

## 待确认的实现细节（非阻塞）

- `pending_review` 徽章中文文案（实现时定）。
- reclassify 改判成 Complaint 的确切落点（复用 close-complaint 还是置人工待处理）——实现时对齐现有投诉流程。
- 队列是否需要展示"距毕业时长"等运营指标（可后加）。
