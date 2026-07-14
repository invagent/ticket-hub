"""Tests for settings loading."""

from app.config import get_settings


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
