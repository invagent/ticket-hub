# feishu_ai 工单入库接口对接文档

## 一、概述

`feishu_ai` 是 ticket-hub 面向飞书 AI / 外部系统的工单入库来源。外部系统把一条待处理工单 POST 到本接口，工单即进入 ticket-hub 的**标准 triage 分诊流程**（自动分类、路由分配、混合单自动拆分、达门槛自动毕业 hub_issue），与 KSM / 智齿 来源的处理链路完全一致。

请求参数形状与 AI 客服 escalation 接口（`/webhook/cs-escalation`）一致，便于复用同一套载荷组装逻辑。差别仅在入库后：本接口走标准 triage，而非 escalation 二次分类。

```
外部系统 → POST /webhook/feishu_ai → 建 Raw 工单(source=feishu_ai)
  → 路由分配 → triage 分诊(classify + 混合判定)
  → 非混合按类型分流；混合单进拆分/主管队列
```

---

## 二、接口定义

- **接口类型：** POST
- **Content-Type：** application/json
- **接口地址：** `POST {BASE_URL}/webhook/feishu_ai?access_token={TOKEN}`
- **鉴权：** query 参数 `access_token`，值为部署方配置的 `WEBHOOK_ACCESS_TOKEN`（常量时间比对）

### 2.1 请求参数

请求 Body 为 JSON 对象：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | string | **是** | 工单唯一标识，作去重键。同一 `session_id` 重复推送只入库一次（返回 `deduped=true`）。别名：`sessionId` / `id` |
| `original_question` | string | **是** | 工单问题正文。截前 120 字符作标题，全文作 body 参与分类。别名：`question` |
| `ai_answer` | string | 否 | AI 已给出的回答（仅存档，不参与分类）。别名：`answer` |
| `dissatisfaction` | string | 否 | 不满反馈 / 补充说明（仅存档，不参与分类）。别名：`feedback` |
| `product_line_code` | string | 否 | 产品线编码。系统会自动创建未知产品线。别名：`product` |
| `module` | string | 否 | 产品模块。系统会自动创建未知模块 |
| `customer` | object | 否 | 客户信息，见下表 |
| `attachments` | array | 否 | 附件（截图为主），见下表 |
| `conversation` | array | 否 | 对话记录 `[{role, text, ts?}]`（仅存档） |
| `cited_knowledge` | array | 否 | 引用知识 `[{type?, id?, title?, snippet?, score?, url?}]`（仅存档） |
| `skills_used` | array | 否 | 使用到的技能名字符串数组（仅存档） |

**`customer` 对象字段**（均可选，用于客户身份解析）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `erp_uid` | string | ERP 客户编号 |
| `mobile` | string | 手机号 |
| `email` | string | 邮箱 |
| `name` | string | 联系人姓名 |
| `source_user_id` | string | 外部系统内的用户 ID |

**`attachments` 数组项字段**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `url` | string | 是 | 附件 URL（别名 `source_url`）。无 url 的项被跳过 |
| `filename` | string | 否 | 文件名 |
| `mime` | string | 否 | MIME 类型 |

### 2.2 请求示例

```bash
curl -X POST 'https://your-host/ticket-hub/webhook/feishu_ai?access_token=YOUR_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "fa-20260805-001",
    "original_question": "发票冲红后可用额度没有恢复，客户催得急",
    "product_line_code": "cloud-fapiao",
    "module": "数电开票",
    "customer": {
      "erp_uid": "E10086",
      "mobile": "13800000000",
      "name": "张三"
    },
    "attachments": [
      {"url": "https://cdn.example.com/screenshot/err.png", "filename": "err.png", "mime": "image/png"}
    ]
  }'
```

---

## 三、响应

### 3.1 成功（HTTP 200）

```json
{
  "ticket_id": 12345,
  "short_code": "TKT-012345",
  "deduped": false,
  "routing_decision": "assigned",
  "assigned_user_ids": [7],
  "trace_id": "..."
}
```

| 字段 | 说明 |
|------|------|
| `ticket_id` | 入库工单的内部 ID |
| `short_code` | 工单短码（`TKT-NNNNNN`） |
| `deduped` | `true` 表示同 `session_id` 已存在，本次为幂等命中，未新建 |
| `routing_decision` | 路由结果：`assigned`（已分派）/ `multi_match`（多团队认领待定）/ `default_pool`（落兜底池）/ `dedup`（去重命中） |
| `assigned_user_ids` | 分派到的处理人 ID 列表 |
| `trace_id` | 本次请求链路 ID，便于排查 |

### 3.2 失败

| HTTP | 场景 | Body |
|------|------|------|
| 400 | 缺 `session_id` 或 `original_question` | `{"detail": "ingest failed: missing original_question"}` |
| 401 | `access_token` 缺失或不匹配 | `{"detail": "invalid webhook access_token"}` |
| 503 | 服务端未配置 `WEBHOOK_ACCESS_TOKEN` | `{"detail": "webhook auth not configured"}` |

---

## 四、行为说明

- **同步入库**：接口在返回前完成工单入库（建单 + 路由），随后异步触发 triage 分诊链（分类可能耗时数秒到数分钟，不阻塞响应）。
- **幂等**：同 `session_id` 重复推送返回 `deduped=true`，不重复建单、不重跑分类。
- **附件**：仅登记附件行（`vision_status=pending`），实际下载与 OCR 由后台流水线异步处理。
- **黄金三元组**：`ai_answer` / `dissatisfaction` 等字段会存进工单 `source_payload` 供审计回查，但**不参与自动分类**（分类只依据 `original_question`）。若需要「AI 答复失败 → 二次分类压低 Operation 概率」的语义，应改用 `/webhook/cs-escalation` 接口。

---

## 五、与其他来源接口的关系

| 接口 | 来源 | 入库后链路 | 说明 |
|------|------|------------|------|
| `/webhook/ksm` | ksm | 标准 triage | KSM 推轻量 ping，异步回调拉全量 |
| `/webhook/zhichi` | zhichi | 标准 triage | 智齿工单 |
| `/webhook/feishu_ai` | feishu_ai | **标准 triage** | 本接口，ai_cs 载荷契约 + 标准分诊 |
| `/webhook/cs-escalation` | ai_cs | escalation 二次分类 | AI 客服转人工，黄金三元组分类 |
