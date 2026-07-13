# 智齿（Sobot）双向打通 · 设计文档

- 日期：2026-07-13
- 状态：设计已批准，待写实施计划
- 范围：入站字段映射修复 + 出站回写新建（一起做）
- 相关：`docs/manual/operation-manual.md`、`app/services/ksm/writeback.py`（KSM 蓝本）、`工单参数.txt`（真实入站格式）、智齿原生接口 https://developer.zhichi.com/pages/950d89/

## 1. 背景与问题

智齿对接目前是「半程」：入站有 `ZhichiIngester` + webhook 端点，出站完全缺失。两个真实缺口：

1. **入站字段映射对不上真实格式**：现有 `zhichi_ingester.py` 读的是假想的扁平字段（`productLineCode`、`customer.mobile`），但智齿真实推送的是三层信封 `{source, raw, fields}`——产品线/模块/客户联系方式全在 `fields` 中文块或 `raw.extend_fields_list` 里。真实工单进来只有 ticketid/标题/正文能解析，其余全丢 → 路由全落兜底池、客户身份识别不到人。
2. **出站回写完全没有**：无 `adapters/zhichi/`、无 writeback sender。cascade 生产者（reply_sync/status_cascade/supply_sync/owner_split）已经在给智齿工单入队 `sync_outbox`（`target_source_code='zhichi'`），但没有消费端 → 这些行永久停在 `pending`。主管的回复回不到智齿客户。

## 2. 决策要点（已锁定）

| 项 | 决策 |
|---|---|
| 范围 | 入站映射修复 + 出站回写，一起做 |
| 入站真实格式 | 三层信封 `{source, raw, fields}`（`工单参数.txt` 智齿段为权威样例） |
| 入站解析优先级 | 优先 `fields` 中文块，`raw` 兜底；`source_payload` 存整个信封 |
| 出站 base url | `https://www.soboten.com`（用户环境国内域名，非原生文档的 sobot.com） |
| 坐席身份 | 工单 `deal_agent_name` → get_data_dict 查 agentid；为空用默认坐席「莉莉」；连莉莉都查不到 → failed 转人工报错（不静默跳过） |
| 结构化拼接 | 先不做（YAGNI）——hub 回复已是完整答复 |
| 灰度 | 镜像 KSM：`zhichi_writeback_enabled`(默认 false) + `zhichi_writeback_dry_run`(默认 true) |
| cascade 生产者 | 不改（已按 `target_source_code=t.source_code` 生成智齿行） |

## 3. 模块 A：入站字段映射修复

**文件**：`backend/app/services/ingest/zhichi_ingester.py`

改 `ZhichiIngester.ingest()`，在读字段前增加信封解包层。

### 字段映射（fields 主源 / raw 兜底）

| ticket 字段 | 主源（fields 中文键） | 兜底（raw） |
|---|---|---|
| source_ticket_id | 工单来源ID | `ticketid` |
| title | 主题 | `ticket_title` |
| body | 问题描述 | `ticket_content` |
| product_line_code | 产品线（如"金蝶发票云"）| extend_fields_list「产品分类」field_text |
| module | 产品模块 | extend_fields_list「产品分类」field_text |
| feature | （fields 无对应，留空）| extend_fields_list（无则 NULL）|
| 客户姓名 | 联系人 / 反馈人 | extend_fields_list「联系人」|
| 客户手机 | 联系人手机 / 反馈人手机 | extend_fields_list「联系手机」|
| 客户邮箱 | 反馈人邮箱 | `user_emails` |
| 公司名 | 客户名称 | `enterprise_name` |
| erp_uid | 对接ERP | extend_fields_list「对接ERP」|

### extend_fields_list 解析规则

`raw.extend_fields_list` 是数组，每项 `{field_name, field_type, field_text, field_value}`：
- `field_type == "6"`（下拉列表）→ 取 `field_text`
- 其余 → 取 `field_value`
- 按 `field_name` 匹配需要的字段（产品分类/联系人/联系手机/对接ERP 等）

### 关键约束

- **`source_payload` 存整个原始信封**（含 `raw.deal_agent_name`、`raw.ticket_level`、`raw.file_str`）——出站回写要用
- **向后兼容**：payload 无 `raw`/`fields` 键时走现有扁平逻辑，不破坏现有单测
- **附件**：`raw.file_str`（分号分隔的 URL）落 `attachments` 表，供 vision（若 vision_enabled）
- **幂等/客户识别/路由**：沿用现有逻辑，仅字段来源变化

## 4. 模块 B：出站 adapter `backend/adapters/zhichi/`

镜像 `adapters/ksm/` 结构，四个文件。

### `types.py`
- `ZhichiConfig`：`app_id` / `app_key` / `base_url`(默认 `https://www.soboten.com`) / `timeout_seconds`(60) / `fallback_agent_name`(默认 "莉莉")。`from_settings()` 读 config。
- `ReplyTicketRequest` DTO：ticketid / ticket_title / ticket_content / reply_content / reply_agentid / reply_agent_name / ticket_status / ticket_level / reply_type(默认 "0") / reply_file_str(可选)

### `exceptions.py`
- `ZhichiError`（基类）/ `ZhichiBusinessError`（ret_code != "000000"）

### `client.py` `ZhichiClient`
- **鉴权**：`GET /api/get_token?appid=&create_time=&sign=`，`sign = md5(appid + create_time + app_key)`（create_time 秒级时间戳字符串）。返回 `item.token` / `item.expires_in`。token 模块级/实例级缓存，提前 5min 过期。
- **`_request`**：header 带 `token`；成功判断 `ret_code == "000000"`；HTTP 401 或 ret_code 为 token 失效码时强制刷新 token 重试一次。
- **`get_data_dict()`**：`GET /api/ws/5/ticket/get_data_dict` → `item.agent_list[]`（agentid + agent_name）。**缓存 30min**（业务变化频繁但非实时）。
- **`resolve_agent(name)`**：按 agent_name 在 agent_list 里查 agentid，查不到抛 `ZhichiError`。
- **`save_ticket_reply(ReplyTicketRequest)`**：`POST /api/ws/5/ticket/save_ticket_reply`。
- **`upload_file()`**（可选，后置）：`POST /api/ws/5/ticket/upload_file`（multipart）→ file_url。

### `__init__.py`
导出 `ZhichiClient` / `ZhichiConfig` / `ReplyTicketRequest` / `ZhichiError` / `ZhichiBusinessError`。

## 5. 模块 C：出站 writeback `backend/app/services/zhichi/writeback.py`

镜像 `ksm/writeback.py`，但更简单（无 KSM 的 lock→refresh→handle 时序，一个 save_ticket_reply 搞定）。

### kind → ticket_status 映射

| sync_outbox kind | ticket_status | 动作 | payload 取值 |
|---|---|---|---|
| `reply` | 3 已解决 | 回复+关单 | payload.reply_content |
| `status` to_status=released | 3 | 关单 | hub.reply_content 或默认语 |
| `status` to_status=in_progress | — | **skip**（智齿无接管概念）| — |
| `supply` | 2 等待回复 | 补料不关单 | payload.supply_note |
| `release_note` | 3 | 发版关单 | payload.note |
| `progress_note` | 2 | 进度不关单 | payload.note |

映射外的 kind/to_status → mark_skipped。

### 坐席解析

1. 从 `ticket.source_payload` 取 `raw.deal_agent_name`
2. 为空 → 用 `settings.zhichi_fallback_agent_name`（"莉莉"）
3. `client.resolve_agent(name)` 查 agentid
4. 查不到（连莉莉都没有）→ `record_failure`（attempts++，超限标 failed 转人工，报错不静默）

### 回复组装

从 `source_payload` 取 `raw.ticket_title`（回落 ticket.title）、`raw.ticket_content`（回落 ticket.body）、`raw.ticket_level`（回落 "1" 中）。ticketid = ticket.source_ticket_id。reply_content 直接用 payload 内容（**不做结构化拼接**）。

### 结构（镜像 KSM）

- `ZhichiWritebackSender`：`drain()` 扫 `target_source_code='zhichi' & status='pending'`，per-row commit
- `_resolve_action` / `_execute` / `_reply` / bookkeeping（mark_skipped / record_failure / sent）
- `drain_zhichi_outbox(db, *, client=None, settings=None)`：入口，enabled 门控 + 构造 client
- DrainReport（scanned/sent/skipped/failed/deferred/errors）

### 灰度阀（同 KSM）

- `zhichi_writeback_enabled`（默认 False）→ drain 直接返回空报告
- `zhichi_writeback_dry_run`（默认 True）→ 组装 payload + log + 标 skipped，不真发
- 失败 attempts++，超 `zhichi_writeback_max_attempts`(5) 标 failed

## 6. 模块 D：配置 + 调度 + 端点

### `config.py` 新增
```
zhichi_base_url: str = "https://www.soboten.com"
zhichi_writeback_enabled: bool = False
zhichi_writeback_dry_run: bool = True
zhichi_writeback_batch: int = 20
zhichi_writeback_max_attempts: int = 5
zhichi_fallback_agent_name: str = "莉莉"
```
（`zhichi_appid` / `zhichi_app_key` 已存在，占位转启用）

### `celery_app.py`
beat 任务 `drain_zhichi_writeback_every_2min`（镜像 `drain_ksm_writeback_every_2min`）→ `app/services/zhichi/writeback_task.py`

### `supervisor.py`
`POST /api/supervisor/drain-zhichi-writeback`（require_supervisor，同步执行看成败，镜像 KSM 的手动 drain 端点）

## 7. 测试

- **入站单测**：`工单参数.txt` 真实信封样例 → 断言 title/body/product_line_code/module/客户联系方式全部正确解析；extend_fields_list field_type=6 取 field_text 用例；旧扁平格式向后兼容用例。
- **出站单测**：mock ZhichiClient →
  - kind→ticket_status 映射全覆盖（reply/status released/status in_progress skip/supply/release_note/progress_note）
  - 坐席兜底：deal_agent_name 有值用它 / 为空用莉莉 / 连莉莉查不到 → failed
  - dry_run 只组装标 skipped
  - 失败重试 attempts++、超限 failed
- **adapter 单测**：get_token 签名、ret_code 判定、token 失效刷新重试、get_data_dict 缓存。
- `conftest.py` 沿用清空 GLM/DASHSCOPE key 防真实调用；智齿 appid/app_key 同样在测试环境留空。

## 8. 灰度上线顺序（部署）

镜像 KSM 剧本：
1. 部署代码（`zhichi_writeback_enabled=false`，drain 空转，什么都不碰）
2. `deploy/.env` 配 `ZHICHI_APPID`/`ZHICHI_APP_KEY` + `zhichi_writeback_enabled=true` + `zhichi_writeback_dry_run=true` → 观察组装的 payload（log）
3. 翻 `zhichi_writeback_dry_run=false` → 真打智齿
4. 主管 `POST /api/supervisor/drain-zhichi-writeback` 手动触发看成败 → 稳了靠 beat 自动
5. SIT 先行验证（入站造真实信封工单 + 出站回复回到智齿），UAT 再放量

## 9. 非目标（本次不做）

- 结构化拼接（productCategory/productModule/rootCause）——需要时后加
- 智齿附件上传回复（upload_file）——先纯文本回复，附件后置
- KSM 出站真打验证——独立任务，不在本次范围
- 入站 webhook 的智齿专有签名校验——沿用共享 token

## 10. 影响面

- 改动文件：`zhichi_ingester.py`（入站）、新增 `adapters/zhichi/*`、新增 `app/services/zhichi/*`、`config.py`、`celery_app.py`、`supervisor.py`
- 无数据库迁移（sync_outbox 的 zhichi 行已在生成，kind 枚举已含全部所需值）
- 前端无改动（drain 是后台 + 主管手动端点，工作台已有 pending 展示）
