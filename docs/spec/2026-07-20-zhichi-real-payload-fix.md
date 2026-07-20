# 智齿真实 payload 字段映射修复 + 标题优化

> 触发：TKT-000015（2026-07-20 10:40 真实智齿工单）标题仍是「客户留言-手机号」，
> 且 product_line / module / reporter 全部为 None。定位后发现根因远大于标题。

## 1. 根因

线上智齿 webhook 推送的是**智齿原生扁平格式**——顶层直接是 `ticket_*` 字段
+ `extend_fields_list` 数组，**没有** `raw` / `fields` 外壳。

但 `zhichi_ingester._flatten_envelope` 只认两种格式：

1. `{source, raw, fields}` 三层信封（`工单参数.txt` 里的样例，实际线上从未出现）
2. 检测不到 `raw`/`fields` → 原样返回，交给下游 legacy 分支
   `payload.get("productLineCode")` / `payload.get("customer")` 等**三层信封字段名**

真实扁平 payload 里这些键都不存在（真实字段名是 `ticket_title`、
`extend_fields_list[产品分类]`、`user_tels`、`user_emails`），于是：

| 字段 | 应取值 | 实际入库（TKT-000015） |
|------|--------|------|
| title | 问题内容（因兜底标题） | `客户留言-18279172007` ❌ |
| product_line | 星瀚-收票 | None ❌ |
| module | 星瀚-收票 | None ❌ |
| reporter.name | 李志坚 | None ❌ |
| reporter.mobile | 18279172007 | None ❌ |
| reporter.email | liuzhian9658@163.com | None ❌ |

只有 `ticketid` / `ticket_title` / `ticket_content` 碰巧同名而取到了值。

### 真实扁平 payload 字段清单（取自 TKT-000015）

```
ticketid          = "e240351f6a7e4e518df9d61d1fa5af11"   # source_ticket_id
ticket_code       = "20260720000001"                     # 展示用单号
ticket_title      = "客户留言-18279172007"                # 智齿兜底标题
ticket_content    = "<p>发票云找不到...如何处理</p>"       # 真正内容（带 HTML）
user_emails       = "liuzhian9658@163.com"               # 反馈邮箱
user_tels         = "18279172007"                        # 反馈手机
user_nick         = "18279172007"
update_name       = "18279172007"
enterprise_name   = ""                                   # 常为空
deal_agent_name   = ""                                   # 坐席（出站回写用）
ticket_status     = 0
ticket_level      = 0
extend_fields_list = [
  {field_name:"产品分类", field_type:"6", field_text:"星瀚-收票", field_value:"cf41..."},
  {field_name:"对接ERP",  field_type:"6", field_text:"星瀚",     field_value:"7512..."},
  {field_name:"公司税号", field_type:"1", field_value:"914403..."},
  {field_name:"联系人",   field_type:"1", field_value:"李志坚"},
  {field_name:"联系手机", field_type:"1", field_value:"18279172007"},
  {field_name:"公司/项目名称", field_type:"1", field_value:"金蝶软件（中国）有限公司"},
]
```

**注意**：`extend_fields_list` 里 field_type=6（下拉）取 `field_text`，其余取
`field_value` —— 现有 `_parse_extend_fields` 已正确实现，可直接复用。

## 2. 修复范围

改一个文件 + 加测试：`backend/app/services/ingest/zhichi_ingester.py`。

### 2.1 `_flatten_envelope` 识别扁平原生格式（新增第三分支）

当前判断：`if not isinstance(raw_obj, dict) and not isinstance(fields_obj, dict): return payload`

改为：无 `raw`/`fields` 但**有 `ticketid` 且有 `extend_fields_list` 或 `ticket_title`**
→ 判定为智齿原生扁平格式，走新的扁平映射（把顶层当 raw 解析）：

```python
ext = _parse_extend_fields(payload)   # 直接解析顶层 extend_fields_list
title = _derive_title(payload.get("ticket_title"), payload.get("ticket_content"))
return {
    "ticketid": payload.get("ticketid"),
    "title": title,
    "content": payload.get("ticket_content"),   # body 保留完整（含 HTML），只标题去 HTML
    "productLineCode": ext.get("产品分类"),
    "moduleName": ext.get("产品分类"),           # 智齿无独立模块，产品分类兼作模块（沿用信封逻辑）
    "customer": {
        "name": ext.get("联系人"),
        "mobile": ext.get("联系手机") or payload.get("user_tels"),
        "email": payload.get("user_emails"),
        "erp_uid": ext.get("对接ERP"),
    },
    "customerid": payload.get("userid"),
    "company": payload.get("enterprise_name") or ext.get("公司/项目名称"),
    "_envelope": payload,   # 原样存档，出站回写读 deal_agent_name / ticket_level
}
```

保留现有三层信封分支和 legacy 分支不动（测试都在，向后兼容）。

### 2.2 标题派生 helper `_derive_title`（你定的规则）

```python
import re

_FALLBACK_TITLE_RE = re.compile(r"^客户留言[-—]")   # 智齿兜底标题：客户留言-手机号
_TAG_RE = re.compile(r"<[^>]+>")

def _strip_html(s: str) -> str:
    text = _TAG_RE.sub("", s)
    # 常见实体
    for a, b in (("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&")):
        text = text.replace(a, b)
    return " ".join(text.split())   # 折叠空白

def _derive_title(raw_title: str | None, raw_content: str | None) -> str | None:
    """标题规则：
    - raw_title 是正常人工标题 → 原样保留（超 150 截断）
    - raw_title 命中「客户留言-…」兜底格式 或 为空 → 用去 HTML 的内容
    - 内容也空 → None
    - 最终一律截前 150 字符
    """
    t = (raw_title or "").strip()
    is_fallback = not t or bool(_FALLBACK_TITLE_RE.match(t))
    if not is_fallback:
        return t[:150]
    content = _strip_html(raw_content or "").strip()
    if content:
        return content[:150]
    return t or None   # 内容也没有，退回原兜底标题（至少有个手机号）比 None 好
```

TKT-000015 结果：`ticket_title` 命中「客户留言-」→ 用内容 →
`"发票云找不到出货的注册邮件无法进行配置。邮箱联系人反馈未收到邮件和短信并且无垃圾邮件拦截，如何处理"`（<150，不截断）。

### 2.3 建 Ticket 时的 title 取值

现有 `title=payload.get("title") or payload.get("ticket_title")` 保持不变——
因为扁平分支已在 `_flatten_envelope` 里算好 `title`（派生后的），
三层信封分支的 `title` 也走 `_derive_title`（下方 2.4 一并改），legacy 分支
`ticket_title` 兜底不变。

### 2.4 三层信封分支也接标题规则（顺带修）

信封分支现在 `"title": pick(fields.get("主题"), raw.get("ticket_title"))`，
主题也可能是兜底标题。改为：
```python
"title": _derive_title(
    pick(fields.get("主题"), raw.get("ticket_title")),
    pick(fields.get("问题描述"), raw.get("ticket_content")),
),
```

## 3. 分类问题（TKT-000015 判 Bug_fix）——不在本次修复内

重跑确认：入库时已是 v2 prompt，判 **Bug_fix 0.85**，理由「系统无法找到注册
邮件导致配置问题」。这**不是** prompt 缺陷，是内容本身歧义：

> 「发票云找不到出货的注册邮件无法进行配置。邮箱联系人反馈未收到邮件和短信…如何处理」

「找不到/未收到邮件短信」是系统故障信号 → Bug_fix 合理；结尾「如何处理」偏
Operation。0.85 的置信度反映了边界性。属于「低置信边界样本」，建议人工修正
或后续 skill draft 回放迭代，**不在本次代码修复范围**。

如果确认这类「通知未送达」应归 Operation，另起 prompt 迭代（走三槽），
与本 payload 修复解耦。

## 4. 测试

在 `test_zhichi_ingester.py` 新增：

1. `test_ingest_native_flat_maps_fields`：用 TKT-000015 脱敏 payload（扁平原生），
   断言 product_line=`星瀚-收票`、module=`星瀚-收票`、reporter.name=`李志坚`、
   mobile/email 到位、source_payload 存原样。
2. `test_native_flat_fallback_title_uses_content`：`ticket_title="客户留言-138…"`
   + 有内容 → title = 去 HTML 的内容。
3. `test_native_flat_real_title_kept`：`ticket_title="发票红冲失败"`（正常标题）
   → 原样保留，不被内容覆盖。
4. `test_title_truncated_to_150`：内容 >150 字 → 截断到 150。
5. `test_strip_html_in_title`：内容带 `<p>`/`&nbsp;` → 标题无标签无实体。
6. 现有 6 个测试（信封/legacy/extend type6/幂等/identity）全部保持绿。

## 5. 部署

- 代码改动 → rsync backend 到 SIT + 重启 backend/worker/worker-beat
  （或 git pull，SIT 走 git 部署）。**纯代码，不涉及 prompt/DB**。
- 历史工单（TKT-000012/014/015 等已入库的错标题/空字段）**不回填**——
  修复只对部署后新入的智齿工单生效。如需回填另写一次性脚本（可选）。
- 无 DB 迁移。

## 6. 风险 / 权衡

- **仅动智齿入站**，不碰 KSM/Zammad/出站回写。
- `_strip_html` 是轻量正则去标签，非完整 HTML 解析——智齿内容是简单 `<p>` 段，
  够用；若将来有复杂 HTML 再引 `bleach`/`html2text`。
- 「客户留言-」前缀判定用正则 `^客户留言[-—]`，覆盖 `-`（半角）和 `—`（破折号）；
  若智齿还有其他兜底前缀（如「在线留言」），后续加正则分支即可。
- 保留三种格式分支（信封/原生扁平/legacy），向后兼容零回归。
