"""pii_lite 底线遮罩测试。"""

from __future__ import annotations

import pytest

from app.core.pii.pii_lite import has_pii, mask_pii


@pytest.mark.parametrize(
    "raw,masked",
    [
        ("联系电话 13812345678", "联系电话 138****5678"),
        ("我的号码是13987654321，谢谢", "我的号码是139****4321，谢谢"),
        # 18 位身份证（末位数字）
        ("身份证110101199003071234", "身份证110101********1234"),
        # 18 位身份证末位 X
        ("证件号11010119900307123X", "证件号110101********123X"),
    ],
)
def test_mask_basic(raw: str, masked: str) -> None:
    assert mask_pii(raw) == masked


def test_mask_multiple_in_one_text() -> None:
    raw = "张三 13812345678 / 李四 13987654321"
    assert mask_pii(raw) == "张三 138****5678 / 李四 139****4321"


def test_none_and_empty_safe() -> None:
    assert mask_pii(None) == ""
    assert mask_pii("") == ""


def test_does_not_touch_non_pii() -> None:
    # 公司名/单号/邮箱/座机 故意不动
    raw = "金蝶发票云 工单 FPY-20260101000123 邮箱 a@b.com 座机 020-12345678"
    assert mask_pii(raw) == raw


def test_phone_not_eaten_by_longer_digit_run() -> None:
    # 嵌在更长数字串里的不应被当手机号打码
    raw = "订单号 113812345678999"
    assert mask_pii(raw) == raw


def test_15_digit_order_not_masked() -> None:
    # 15 位订单号不应被误当旧身份证（已故意不支持 15 位证）
    assert mask_pii("订单 113812345678999") == "订单 113812345678999"


def test_idcard_boundary_not_over_match() -> None:
    # 不是合法 18 位的纯数字不动
    assert mask_pii("数字 12345678") == "数字 12345678"


def test_has_pii() -> None:
    assert has_pii("打给 13812345678")
    assert has_pii("身份证110101199003071234")
    assert not has_pii("没有敏感信息的普通文本")
    assert not has_pii(None)
    assert not has_pii("")
