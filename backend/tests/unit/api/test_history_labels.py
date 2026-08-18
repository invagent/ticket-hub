"""工单时间轴文本人性化单测（app/api/history_labels.py）。"""

from __future__ import annotations

from app.api.history_labels import (
    collect_user_ids,
    humanize_actor,
    humanize_reason,
    humanize_status,
    humanize_type_tokens,
)


def test_humanize_type_tokens() -> None:
    assert humanize_type_tokens("改判为 Bug_fix") == "改判为 Bug修复"
    assert humanize_type_tokens("改判 Bug_fix→Operation: x") == "改判 Bug修复→运营: x"
    assert humanize_type_tokens("Demand 与 Internal_task") == "需求 与 内部任务"
    # 无类型词原样
    assert humanize_type_tokens("普通说明") == "普通说明"


def test_humanize_status() -> None:
    assert humanize_status("received") == "已接收"
    assert humanize_status("split") == "已拆分"
    assert humanize_status("released") == "已发版"
    assert humanize_status(None) is None
    # 未知原样
    assert humanize_status("weird") == "weird"


def test_humanize_actor_user_prefix() -> None:
    assert humanize_actor("user:张三", {}) == "张三"
    assert humanize_actor("op:user:李四", {}) == "李四"  # op: 双前缀


def test_humanize_actor_system_slugs() -> None:
    assert humanize_actor("system:reroute", {}) == "系统重新分派"
    assert humanize_actor("agent:linear_push", {}) == "系统推送 Linear"
    # 未列入表的 system/agent/cascade → 泛化"系统"
    assert humanize_actor("cascade:reply", {}) == "系统"
    assert humanize_actor("agent:unknown_x", {}) == "系统"
    assert humanize_actor(None, {}) == "—"


def test_humanize_reason_replaces_user_id_with_name() -> None:
    names = {42: "王五", 36: "赵六"}
    assert (
        humanize_reason("转交处理人 to user_id=42 by user_id=36", names)
        == "转交处理人 to 王五 by 赵六"
    )
    # 查不到的 user_id 留原样（不误导）
    assert humanize_reason("by user_id=99", names) == "by user_id=99"
    # 同时翻译英文类型
    assert humanize_reason("改判为 Bug_fix", names) == "改判为 Bug修复"


def test_collect_user_ids() -> None:
    ids = collect_user_ids(
        ["转交 to user_id=42 by user_id=36", None, "reroute by user_id=7", "无 id"]
    )
    assert ids == {42, 36, 7}
