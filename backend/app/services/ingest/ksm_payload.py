"""Convert a KSM `subscribeCallback` response `data` block into the dict
shape that `KSMIngester.ingest()` understands (D2-F).

Field mapping (KSM doc § 三)：
    billId                                           → billId
    title                                            → title
    problem                                          → content
    version.mainproductname (preferred, more specific)
        OR product.name                              → productLineCode (after lookup)
    module.name                                      → moduleName
    customerInfo.customerNumber                      → account, erpUid
    feedbackUser                                     → accountName
    feedbackEmail                                    → email
    feedbackPhone                                    → mobile
    feedbackTel                                      → tel

Mapping table for product lines (Chinese name in KSM → our seeded code):
    金蝶发票云*       → cloud-fapiao   (prefix match, handles "金蝶发票云（旗舰版）..."等变体)
    金蝶云星空*       → cloud-erp-star
    金蝶云苍穹*       → cloud-cangqiong
    金蝶EAS*         → eas-cloud

Resolution order:
    1. Exact match in `PRODUCT_NAME_TO_CODE`
    2. Prefix match (KSM's product names commonly trail with version/edition
       parens; we strip those rather than maintain a Cartesian-product table)
    3. Unmapped → return None (NOT raw Chinese string) so the Ticket FK to
       product_lines.code stays valid; Router will fall to default_pool.

Admin extends `PRODUCT_NAME_TO_CODE` (and re-deploys) when a new product
appears. Future: D3 may move this to a DB table.
"""

from __future__ import annotations

from typing import Any

# ⚠️ DEPRECATED（2026-08）：这张硬编码表把 KSM 产品名映射到旧产品线码
# （cloud-fapiao 等，均已 is_active=False）。module_classify_enabled 开启后，
# 其输出会被 safe_product_line_code 抹成 NULL、由 module_resolve 归类链按现有
# active 目录重判——即此表输出被丢弃、不再生效。仅在归类开关【关闭】时作为
# 旧的入库落码路径保留（向后兼容）。归类稳定长开后可连同 _resolve_product_line_code
# 一并删除。新产品线由目录管理维护，不要再往这张表加。
# Keep in sync with backend/config/seeds/assignment_scopes.example.yaml
PRODUCT_NAME_TO_CODE: dict[str, str] = {
    "金蝶发票云": "cloud-fapiao",
    "金蝶云星空": "cloud-erp-star",
    "金蝶云苍穹": "cloud-cangqiong",
    "金蝶EAS Cloud": "eas-cloud",
    "金蝶 EAS Cloud": "eas-cloud",  # tolerate the spaced variant
    "金蝶EAS": "eas-cloud",  # bare prefix
}


def parse_attachments(data: dict[str, Any]) -> list[dict[str, str | None]]:
    """从 KSM subscribeCallback data 块提取附件 [{url, name}] 列表。

    KSM `attachment` 是对象数组，每项形如 {name, type, url, desc}。**真实文件名在
    `name`**（url 是下载动作端点 accessory!download.action?id=xxx，路径尾段不是文件名）。
    容错：缺失/非数组/项无 url → 空列表，绝不抛（字段名按 KSM 实际返回，已用真实数据确证）。
    """
    raw = data.get("attachment")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str | None]] = []
    for item in raw:
        if isinstance(item, dict):
            url = item.get("url")
            if isinstance(url, str) and url.strip():
                name = item.get("name")
                out.append(
                    {
                        "url": url.strip(),
                        "name": name.strip() if isinstance(name, str) and name.strip() else None,
                    }
                )
    return out


def _resolve_product_line_code(data: dict[str, Any]) -> str | None:
    version = (data.get("version") or {}) if isinstance(data.get("version"), dict) else {}
    product = (data.get("product") or {}) if isinstance(data.get("product"), dict) else {}
    candidate = version.get("mainproductname") or product.get("name") or ""
    candidate = candidate.strip()
    if not candidate:
        return None

    # 1. Exact match wins.
    if candidate in PRODUCT_NAME_TO_CODE:
        return PRODUCT_NAME_TO_CODE[candidate]

    # 2. Prefix match — sort longest-first so "金蝶EAS Cloud" beats "金蝶EAS".
    for name in sorted(PRODUCT_NAME_TO_CODE, key=len, reverse=True):
        if candidate.startswith(name):
            return PRODUCT_NAME_TO_CODE[name]

    # 3. Unknown product. Return None so we don't poison the FK constraint.
    return None


def from_subscribe_callback(data: dict[str, Any]) -> dict[str, Any]:
    """Map the `data` block returned by KSM `subscribeCallback` into the
    payload dict consumed by `KSMIngester.ingest()`.

    The returned dict additionally preserves the raw `data` under
    `_subscribe_callback` so the ticket's `source_payload` retains
    everything (handleSteps, attachments, etc.) for later replay or audit.
    """
    customer = data.get("customerInfo") or {} if isinstance(data.get("customerInfo"), dict) else {}
    module = (data.get("module") or {}) if isinstance(data.get("module"), dict) else {}

    payload: dict[str, Any] = {
        # Identity / dedupe key
        "billId": data.get("billId") or data.get("id"),
        "billNumber": data.get("billNumber"),  # 来源工单编号（展示/搜索用）
        # Ticket metadata
        "title": data.get("title"),
        "content": data.get("problem"),
        "productLineCode": _resolve_product_line_code(data),
        "moduleName": module.get("name") or None,
        # Customer identity (KSMIngester._extract_identity reads these)
        "account": customer.get("customerNumber"),
        "accountName": data.get("feedbackUser"),
        # 手机/邮箱优先取客户公司联系人（customerInfo.mobile/email），反馈人顶层
        # feedbackPhone/feedbackEmail 作兜底——部分工单 feedbackPhone 为空，但
        # customerInfo.mobile 有值（如 TKT-006256）。
        "email": customer.get("email") or data.get("feedbackEmail"),
        "mobile": customer.get("mobile") or data.get("feedbackPhone"),
        "tel": customer.get("phone") or data.get("feedbackTel"),
        "erpUid": customer.get("customerNumber"),
        # 提单公司：KSM customerInfo.customerName（客户公司名，≠ feedbackUser 反馈人）。
        # 税号/租户 KSM 不传，留空。
        "reporterCompany": customer.get("customerName") or None,
        "attachments": parse_attachments(data),
        # Pass through full original payload for source_payload audit trail.
        "_subscribe_callback": data,
    }
    return payload
