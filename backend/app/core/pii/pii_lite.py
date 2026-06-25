"""PII 轻量底线遮罩（D4 优化 v2 §四.1）—— 不可逆打码，发客户/出库前用。

定位区别于同目录的 sanitizer/restorer（那套是「占位+加密可还原」，给外部 LLM 用）：
这里是**一次性遮罩**，守底线即可（手机、身份证），不还原、不加密。
全链用中国区国内模型，同供应商边界，无需复杂脱敏。

规则（业务底线）：
    手机号   1[3-9]xxxxxxxxx → 前3后4，中间 ****        138****5678
    身份证   18 位(末位可X)  → 前6后4，中间打码          110101********1234
其余（公司名/单号/邮箱/座机/15位旧证）不动 —— 故意保守：15 位纯数字与订单号
无法用正则区分，宁可漏（旧证少见）也不误伤业务文本。
"""

from __future__ import annotations

import re

# 手机：避免吃到更长数字串（前后非数字边界）
_PHONE = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")
# 身份证：仅 18 位现行标准（末位可 X）；前6后4 保留，中间打码
_IDCARD_18 = re.compile(r"(?<![0-9Xx])(\d{6})\d{8}(\d{3}[0-9Xx])(?![0-9Xx])")


def mask_pii(text: str | None) -> str:
    """对手机号、18 位身份证做不可逆打码。None/空串安全返回空串。"""
    if not text:
        return ""
    out = _PHONE.sub(lambda m: f"{m.group(1)}****{m.group(2)}", text)
    out = _IDCARD_18.sub(lambda m: f"{m.group(1)}********{m.group(2)}", out)
    return out


def has_pii(text: str | None) -> bool:
    """是否含可识别的手机/18 位身份证（用于审计/告警，不改文本）。"""
    if not text:
        return False
    return bool(_PHONE.search(text) or _IDCARD_18.search(text))
