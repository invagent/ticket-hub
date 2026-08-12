"""Tests for settings loading."""

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings


def test_settings_uses_test_overrides() -> None:
    s = get_settings()
    assert s.environment == "test"
    assert s.feishu_app_id == "test-app"
    assert s.webhook_access_token == "test-token"


def test_settings_is_cached() -> None:
    a = get_settings()
    b = get_settings()
    assert a is b


def test_zhichi_writeback_defaults() -> None:
    s = get_settings()
    assert s.zhichi_base_url == "https://www.soboten.com"
    assert s.zhichi_writeback_enabled is False
    assert s.zhichi_writeback_dry_run is True
    assert s.zhichi_writeback_batch == 20
    assert s.zhichi_writeback_max_attempts == 5
    assert s.zhichi_fallback_agent_name == "莉莉"


def test_operation_auto_reply_defaults() -> None:
    s = get_settings()
    assert s.operation_auto_reply_enabled is False
    assert s.operation_auto_reply_min_length == 10


def test_operation_answer_accuracy_mode_default_off() -> None:
    s = get_settings()
    assert s.operation_answer_accuracy_mode == "off"


def test_operation_answer_accuracy_mode_accepts_valid_value() -> None:
    s = Settings(operation_answer_accuracy_mode="enforce")
    assert s.operation_answer_accuracy_mode == "enforce"


def test_operation_answer_accuracy_mode_rejects_typo() -> None:
    with pytest.raises(ValidationError):
        Settings(operation_answer_accuracy_mode="enforc")


def test_gate_classify_falls_back_to_require_review() -> None:
    # 未显式设 gate_classify_enabled 时，回落 require_review_before_linear
    s = Settings(require_review_before_linear=False)
    assert s.gate_classify_enabled is False
    s2 = Settings(require_review_before_linear=True)
    assert s2.gate_classify_enabled is True


def test_gate_classify_explicit_overrides_fallback() -> None:
    s = Settings(require_review_before_linear=True, gate_classify_enabled=False)
    assert s.gate_classify_enabled is False


def test_gate_linear_push_default_on() -> None:
    assert Settings().gate_linear_push_enabled is True
