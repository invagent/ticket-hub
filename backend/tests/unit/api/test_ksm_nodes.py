"""KSM 处理节点解析单测（app/api/ksm_nodes.py）。"""

from __future__ import annotations

from app.api.ksm_nodes import parse_ksm_nodes


def _payload(steps: list[dict]) -> dict:
    return {"_subscribe_callback": {"handleSteps": steps}}


def test_parse_sorts_by_time_ascending() -> None:
    """节点按 handleDateTime 升序（真实数据乱序）。"""
    payload = _payload(
        [
            {"nodeName": "受理", "handleDateTime": "2026-08-06 15:00:00", "nodeStatus": "1"},
            {"nodeName": "提交", "handleDateTime": "2026-08-04 10:00:00", "nodeStatus": "1"},
            {"nodeName": "协同处理", "handleDateTime": "2026-08-10 09:00:00", "nodeStatus": "1"},
        ]
    )
    nodes = parse_ksm_nodes(payload)
    assert [n.node_name for n in nodes] == ["提交", "受理", "协同处理"]


def test_empty_node_name_and_handler_normalized() -> None:
    """nodeName 空→'反馈提交'；处理人空→'客户/系统'。"""
    payload = _payload(
        [{"nodeName": None, "handleDateTime": "2026-08-04 10:00:00", "dealopinion": "反馈提交"}]
    )
    nodes = parse_ksm_nodes(payload)
    assert nodes[0].node_name == "反馈提交"
    assert nodes[0].handler_name == "客户/系统"
    assert nodes[0].content == "反馈提交"


def test_handler_prefers_realname() -> None:
    """处理人优先 realname，回落 name；不输出 mobile/email（PII）。"""
    payload = _payload(
        [
            {
                "nodeName": "受理",
                "handleDateTime": "2026-08-06 15:00:00",
                "nodeStatus": "1",
                "dealopinion": "已受理",
                "assignUser": {
                    "realname": "颜明霞",
                    "name": "wbmingxia_yan",
                    "mobile": "13800000000",
                    "email": "x@y.com",
                },
            }
        ]
    )
    nodes = parse_ksm_nodes(payload)
    assert nodes[0].handler_name == "颜明霞"
    assert nodes[0].content == "已受理"
    assert nodes[0].done is True
    # PII 不在输出模型里
    dumped = nodes[0].model_dump()
    assert "mobile" not in dumped and "email" not in dumped


def test_handler_falls_back_to_name_when_no_realname() -> None:
    payload = _payload(
        [
            {
                "nodeName": "受理",
                "handleDateTime": "2026-08-06 15:00:00",
                "assignUser": {"name": "wbqingqing_zeng"},
            }
        ]
    )
    assert parse_ksm_nodes(payload)[0].handler_name == "wbqingqing_zeng"


def test_node_status_done_flag() -> None:
    payload = _payload(
        [
            {"nodeName": "A", "handleDateTime": "2026-08-01 00:00:00", "nodeStatus": "1"},
            {"nodeName": "B", "handleDateTime": "2026-08-02 00:00:00", "nodeStatus": "0"},
            {"nodeName": "C", "handleDateTime": "2026-08-03 00:00:00", "nodeStatus": None},
        ]
    )
    nodes = parse_ksm_nodes(payload)
    assert [n.done for n in nodes] == [True, False, False]


def test_non_ksm_and_missing_data_returns_empty() -> None:
    assert parse_ksm_nodes(None) == []
    assert parse_ksm_nodes({}) == []
    assert parse_ksm_nodes({"_subscribe_callback": {}}) == []
    assert parse_ksm_nodes({"_subscribe_callback": {"handleSteps": "notalist"}}) == []
    # 智齿等其他源 payload 无 _subscribe_callback
    assert parse_ksm_nodes({"raw": {}, "fields": {}}) == []
