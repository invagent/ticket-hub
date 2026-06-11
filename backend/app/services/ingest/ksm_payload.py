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

# Keep in sync with backend/config/seeds/assignment_scopes.example.yaml
PRODUCT_NAME_TO_CODE: dict[str, str] = {
    "金蝶发票云": "cloud-fapiao",
    "金蝶云星空": "cloud-erp-star",
    "金蝶云苍穹": "cloud-cangqiong",
    "金蝶EAS Cloud": "eas-cloud",
    "金蝶 EAS Cloud": "eas-cloud",  # tolerate the spaced variant
    "金蝶EAS": "eas-cloud",  # bare prefix
}


def _resolve_product_line_code(data: dict[str, Any]) -> str | None:
    version = (
        (data.get("version") or {}) if isinstance(data.get("version"), dict) else {}
    )
    product = (
        (data.get("product") or {}) if isinstance(data.get("product"), dict) else {}
    )
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
    customer = (
        data.get("customerInfo") or {}
        if isinstance(data.get("customerInfo"), dict)
        else {}
    )
    module = (data.get("module") or {}) if isinstance(data.get("module"), dict) else {}

    payload: dict[str, Any] = {
        # Identity / dedupe key
        "billId": data.get("billId") or data.get("id"),
        # Ticket metadata
        "title": data.get("title"),
        "content": data.get("problem"),
        "productLineCode": _resolve_product_line_code(data),
        "moduleName": module.get("name") or None,
        # Customer identity (KSMIngester._extract_identity reads these)
        "account": customer.get("customerNumber"),
        "accountName": data.get("feedbackUser"),
        "email": data.get("feedbackEmail"),
        "mobile": data.get("feedbackPhone"),
        "tel": data.get("feedbackTel"),
        "erpUid": customer.get("customerNumber"),
        # Pass through full original payload for source_payload audit trail.
        "_subscribe_callback": data,
    }
    return payload
