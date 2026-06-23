# sample/ticket 分支差异分析报告

- 日期：2026-06-23
- 分析对象：`sample/ticket`（第三方 fork）vs 鸡鸭牛项目（主仓库 `invagent/ticket-hub`）
- 方法：两者共享 git 历史，采用 commit lineage + 工作树全量 diff 求**精确**差异（非人工盲读）

---

## 〇、摘要（TL;DR）

**`sample/ticket` 不是一个独立的新项目，而是主项目在 `4794213`（"D4 第③段设计文档"提交）这一刻被 fork 出去的快照。** 它落后主项目整整一个「D4 第③段实现」。

```
                    4794213 (共同祖先：第③段设计完成、实现未开始)
                   /        \
   sample/ticket  ●          ●●●●●●  主项目（鸡鸭牛）
   +1 提交(脱敏)              +6 提交(Vision/escalation/飞书wiki/ADR)
   +2 未提交(auth本地修复)     = 32 文件 / +2462 行
   +uv.lock(改用 uv)
```

| | 主项目（鸡鸭牛） | sample/ticket |
|---|---|---|
| git 远端 | `invagent/ticket-hub` | `xiaojianjian2233/ticket`（第三方）|
| 作者 | shawn | 小坚坚 `junill@…`（Claude Sonnet 4.6 协作）|
| HEAD | `b1b3dc4` | `5cde149` |
| 相对祖先 `4794213` | **+6 提交** | **+1 提交** |
| git 跟踪文件 | 312 | 295（少 17）|
| 后端 .py | 206 | 196 |
| 迁移 | 13（到 0012）| 12（缺 0012）|

**一句话**：代码能力上 sample 落后主项目一个完整阶段；主项目唯一该向 sample 学的，是那个**脱敏提交**（详见 §5 安全发现）。

---

## 一、差异点全量清单（新增 + 改动）

### 1.1 sample 相对主项目「独有 / 领先」的部分（仅 4 处）

| # | 类型 | 内容 | 文件 | 是否已测 |
|---|---|---|---|---|
| 1 | 改动(提交) | **脱敏 CLAUDE.md**：生产 IP / 员工邮箱 / Linear team ID → 指向 `CLAUDE.local.md`（提交 `5cde149`）| `CLAUDE.md` | n/a |
| 2 | 改动(未提交) | **SSO 本地跳转修复**：回调 `localhost:8080`（后端口）自动改写为 `:5173`（Vite dev），本地联调免手动改 | `app/api/auth.py` `_frontend_base_from_redirect` | ✅ 有新测试 |
| 3 | 新增(未提交) | 上述修复的单测 | `tests/unit/test_auth.py` `test_frontend_base_from_local_redirect_uses_vite_port` | — |
| 4 | 新增(未跟踪) | **改用 `uv` 管依赖**：生成 `backend/uv.lock` | `backend/uv.lock` | — |

> 注：sample 的 §1.1 全部内容总和 ≈ 1 个安全提交 + 1 个本地开发便利修复 + 1 个依赖工具切换。**没有任何新业务功能。**

### 1.2 主项目相对 sample「独有 / 领先」的部分（32 文件 / +2462 行）

分叉后主项目独立完成的整个 **D4 第③段**：

| 模块 | 关键文件（sample 完全没有）| 行数 |
|---|---|---|
| **Vision 多模态** | `app/core/llm_router/vision.py`、`services/agents/vision_extract.py`、`migrations/0012_d4_attachments.py`、`prompts/vision_extract_v1.md` | +416 |
| **AI 客服 escalation 链** | `services/agents/escalation_classify.py`、`services/ingest/escalation_ingester.py`、`prompts/escalation_classify_v1.md`、`webhooks.py` 的 `/webhook/cs-escalation` | +590 |
| **飞书 wiki 集成** | `adapters/feishu/client.py`(+108: get/list/walk/create_node + get_doc_raw_content)、`adapters/feishu/types.py`(WikiNode)、`scripts/feishu_wiki_dump.py` | +250 |
| **架构决策记录** | `docs/adr/0013/0014/0015-*.md` + README | +137 |
| **知识飞轮设计** | `docs/spec/d4-stage3-knowledge-flywheel-skill.md` | +111 |
| **模型/配置** | `models.py`(+47: Attachment)、`config.py`(+16: vision/escalation 开关) | +63 |
| **前端类型同步** | `frontend/src/api/openapi.json`(+43)、`types.ts`(+52) | +95 |
| **测试** | 6 个新测试文件（vision/escalation/feishu_client）| +734 |
| **文档** | `CLAUDE.md`、`plan`、`d4-stage3-design.md` 更新 | — |

---

## 二、新分支（sample）设计的优缺点

由于 sample 的「设计」实质只有 §1.1 那 4 处改动，逐项评估：

### 优点

1. **脱敏提交 — 安全意识到位（最有价值）**
   - 把生产 IP、员工真实邮箱、Linear team UUID 从受版本控制的 `CLAUDE.md` 移除，指向不提交的 `CLAUDE.local.md`。
   - 这是主项目的真实疏漏，sample 做对了，**主项目应吸收**。

2. **SSO 本地跳转修复 — 开发体验改进，且有测试**
   - 解决了本地联调时飞书 SSO 回调落到后端口（8080）而非前端 Vite（5173）的痛点。
   - 实现干净（`urlsplit`/`urlunsplit`，仅对 localhost:8080 生效，不影响生产），并配了单测，工程质量好。

3. **`uv` 依赖工具 — 现代化方向**
   - `uv` 比 pip 快一个数量级，lock 文件可复现。方向正确。

### 缺点 / 风险

1. **整体落后一个阶段**：缺 Vision、escalation、飞书 wiki、ADR——这是最大问题，sample 的代码价值远低于主项目。
2. **关键改动停留在未提交状态**：auth.py 修复 + test 只在工作树，未 commit，易丢失、不可追溯。
3. **`uv` 切换不彻底**：只生成了 `uv.lock`，但 `Makefile` 仍是 pip/.venv 流程（`make install` 用 venv）。工具链处于半切换的不一致状态——要么提交配套的 `pyproject`/Makefile 改造，要么别引入 uv.lock。
4. **脱敏只治标**：敏感值已在 git 历史里（且已 fork 到第三方仓库），仅改当前文件无法回收已泄露的值（见 §5）。

---

## 三、合并方案：应删除 / 应补充

> 前提认知：sample ⊂ 主项目（功能上），所以「合并」**不是对等 merge**，而是**主项目选择性吸收 sample 的 3 个有用改动**，sample 整体以主项目为准。

### 3.1 若以主项目为基线（推荐）

**主项目应「补充」（从 sample 吸收）：**

| 补充项 | 操作 | 优先级 |
|---|---|---|
| 脱敏 CLAUDE.md | 照 `5cde149` 把 IP/邮箱/team ID → `CLAUDE.local.md` | 🔴 高（安全）|
| auth.py SSO 本地跳转修复 + 测试 | cherry-pick sample 工作树改动（先让对方 commit）| 🟡 中（开发体验）|
| `uv` 工具链 | 若团队决定迁 uv，连 `pyproject`/Makefile 一起改，别只留 lock | 🟢 低（可选）|

**主项目应「删除」：无。** 主项目没有任何 sample 没有的"多余/错误"内容——它领先的全是有效实现。

### 3.2 若以 sample 为基线（不推荐）

sample 需要「补充」= 把主项目 `4794213..b1b3dc4` 的 6 个提交全量拉过来（Vision + escalation + 飞书 wiki + ADR），等于放弃自己的落后状态。「删除」= 无（它的 1 个脱敏提交可保留）。

### 3.3 推荐合并动作（最小冲突）

```
# 在主项目执行：
1. 把 sample 的脱敏改法应用到主项目 CLAUDE.md（手动改 3 处，1 个 commit）
2. 请 sample 作者把 auth.py + test_auth.py 改动 commit，再 cherry-pick 过来
3. uv：单独决策，要切就切干净（pyproject + Makefile + CI）
# sample 侧：rebase/merge 主项目 main，获得第③段全部实现
```
**冲突面极小**：两边唯一同时改过的文件是 `CLAUDE.md`（sample 脱敏 vs 主项目加第③段章节）——按行不重叠，手动合并 1 分钟。

---

## 四、三大能力完成度（AI客服 / KSM集成 / 人员分流）

| 能力 | 主项目（鸡鸭牛）| sample | 是否完成 | 缺口 |
|---|---|---|---|---|
| **AI 客服处理** | escalation 链：`/webhook/cs-escalation` + 黄金三元组二次分类（实测 Bug_fix 0.94）+ 判回 Operation 走 reply_sync | ❌ 完全没有 | 🟡 **主项目主体完成、未全闭环** | ③-3 `adapters/ai_cs` 未建（待真实 API 路径）；自动毕业开关默认关；知识反哺飞轮（第④点）未开发 |
| **KSM 集成** | KSM ingest（轻量 ping + subscribeCallback 回查）+ 客户身份解析 + 自动 upsert 产品线/模块；Vision 管道已就绪 | 部分（KSM ingest 有，Vision 无）| 🟡 **基础完成、附件未通** | **KSM 截图附件 → Vision 未打通**：`storage_key`(MinIO) 下载/presign 路径标注"待 KSM 附件接入"；Vision 目前只支持 `source_url` 直传 |
| **人员分流** | 路由引擎（module→feature→default_pool）+ Linear 按处理人 team 路由（21 个映射）+ 查无此人置 pending 待人工 + reroute | ✅ 全部都有（分叉前已完成）| 🟢 **两边都完成** | 组账号→个人化路由是运营动作（非代码）；多 team 成员取默认 team 的取舍已实现 |

**要点**：
- **人员分流** 是三者中唯一**两边都已完整**的（属分叉前 D4 ①② 的成果：`linear_push` / `user_sync` / `cascade` / 迁移 0010/0011 sample 都有）。
- **AI 客服** 主项目做了核心（接收+分类+流转），但「接 AI 客服真实 API」和「知识反哺飞轮」两块未落地——前者等对方 API，后者明确放在下一阶段。
- **KSM 集成** 的最后一公里是**截图附件下载**——这是 Vision 在 KSM 工单上真正生效的前提，目前只对带公开 `source_url` 的图生效（如 ai_cs escalation 的截图），KSM 自有附件需要鉴权下载到 MinIO 再喂 Vision。

---

## 五、其他重要事项

### 5.1 🔴 安全发现（本报告最重要的一条）

**主项目的 `CLAUDE.md`（已提交 git、已推送 `invagent/ticket-hub`）至今仍含明文敏感信息**，而第三方 fork 反而删了：

| 敏感项 | 主项目现状（行号）| sample 脱敏为 |
|---|---|---|
| 生产服务器 IP | 明文 IP（L143，本报告已脱敏，原值见 `CLAUDE.local.md`）| 「见 CLAUDE.local.md」|
| 员工真实邮箱 | 明文员工邮箱（L382）| 「某内部用户」|
| Linear team UUID | 明文 team UUID（L327）| 「见 CLAUDE.local.md」|

> 注：本报告已于 2026-06-23 完成脱敏整改——上述明文值已从主项目 `CLAUDE.md`
> 及测试 fixture 移除，统一归集到 gitignored 的 `CLAUDE.local.md`，故此处不再列原值。

**两层风险：**
1. 这些值**已随 fork 进入第三方（xiaojianjian2233）的 GitHub 仓库**——已发生的泄露，脱敏当前文件无法回收。
2. 主项目仍在 `CLAUDE.md` 持续累积内容，敏感信息一直在扩散面上。

**建议（按紧急度）：**
1. 立即：照 sample 思路脱敏主项目 `CLAUDE.md`（止血，防继续扩散）。
2. 评估：已泄露值是否需轮换——生产 IP 的访问控制收紧（防扫描）、该 Linear key 的影响面、确认 fork 仓库是否 public。
3. 流程：把"敏感值不进 git"做成约定（CLAUDE.local.md 已有机制，需贯彻）。

> ⚠️ 同时提醒：本次会话中生产服务器密码、DashScope/Linear 的 API key 等都以明文在对话里出现过——这些应视为已知给协作方，必要时轮换。

### 5.2 依赖工具链分歧（uv vs pip）

sample 引入 `uv.lock` 但 Makefile 未配套，处于半切换状态。若主项目也考虑迁 uv，应一次性改造 `pyproject` + `Makefile` + CI，避免两套并存。

### 5.3 fork 的协作模式观察

sample 由「小坚坚」用 Claude Sonnet 4.6（1M 上下文）协作产出，提交规范、有脱敏意识、本地修复带测试——是一个**质量不错但进度落后**的并行实验分支。若是团队成员，建议其 rebase 主项目以免持续分叉；其脱敏与 SSO 修复值得反向合并回主干。

---

## 附录：精确 git 数据

```
共同祖先:        4794213  docs(d4): 第③段详细设计 — AI 客服闭环 + Vision 多模态（取消自建 RAG）

主项目 4794213..b1b3dc4 (6 提交):
  3bd76d4  feat(d4-stage3): Vision 多模态管道
  9c34358  feat(d4-stage3): AI 客服 escalation 链
  8b83108  docs(d4-stage3): 记录 escalation 链 + Vision 生产落地
  74f99fe  docs: 补 ADR 0013/0014/0015 + 修正技术债
  7867b28  feat(d4-stage3): 飞书 wiki 只读地基
  b1b3dc4  feat(d4-stage3): 飞书 wiki create_wiki_node 写方法

sample 4794213..5cde149 (1 提交):
  5cde149  security: 脱敏 CLAUDE.md 中的生产服务器 IP、员工邮箱和 Linear team ID
  + 未提交工作树: app/api/auth.py, tests/unit/test_auth.py
  + 未跟踪: backend/uv.lock

统计: 主项目 +2462 行 / 32 文件 ; sample +3 行(净) / 1 文件(提交)
```
