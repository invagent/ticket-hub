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
