"""KSM 工单处理节点解析 —— 从 source_payload 的 handleSteps 提取前端可展示的节点列表。

KSM subscribeCallback 的 `data.handleSteps` 是工单流转各节点数组，字段（实测 SIT 真实数据）：
  nodeName(节点名,可空) / nodeStatus('0'待处理/'1'完成,可空) / handleDateTime(处理时间)
  / dealopinion(处理内容,全小写) / assignUser.realname(处理人真实姓名,优先) / handleInfo(补充)

真实数据特征（务必处理）：节点未按时间排序、含 nodeName 为空的"反馈提交"客户动作、同名节点重复。
本模块：按 handleDateTime 升序排序、空 nodeName 归一"反馈提交"、空处理人归一"客户/系统"、
不输出 assignUser.mobile/email 等 PII。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class KsmNode(BaseModel):
    """一个 KSM 处理节点（前端处理节点时间轴用）。"""

    node_name: str  # 节点名（空→"反馈提交"）
    handler_name: str  # 处理人真实姓名（空→"客户/系统"）
    handled_at: str | None  # 处理时间 "yyyy-MM-dd HH:mm:ss"（原样字符串）
    content: str | None  # 处理内容（dealopinion）
    done: bool  # 节点是否处理完成（nodeStatus == '1'）


def _node_sort_key(step: dict[str, Any]) -> str:
    """按 handleDateTime 升序排（字符串 yyyy-MM-dd HH:mm:ss 可直接字典序）。缺失排最后。"""
    dt = step.get("handleDateTime")
    return dt if isinstance(dt, str) and dt else "9999"


def parse_ksm_nodes(source_payload: dict[str, Any] | None) -> list[KsmNode]:
    """从 ticket.source_payload 提取 KSM 处理节点，按时间升序。非 KSM / 无 handleSteps → []。"""
    if not source_payload:
        return []
    callback = source_payload.get("_subscribe_callback")
    if not isinstance(callback, dict):
        return []
    steps = callback.get("handleSteps")
    if not isinstance(steps, list):
        return []

    nodes: list[KsmNode] = []
    for step in sorted(
        (s for s in steps if isinstance(s, dict)),
        key=_node_sort_key,
    ):
        assign_user = step.get("assignUser")
        handler = ""
        if isinstance(assign_user, dict):
            # 真实姓名优先，回落用户名；PII（mobile/email）不取。
            handler = str(assign_user.get("realname") or assign_user.get("name") or "").strip()

        node_name = str(step.get("nodeName") or "").strip() or "反馈提交"
        # dealopinion 处理内容，回落 handleInfo。
        content = str(step.get("dealopinion") or step.get("handleInfo") or "").strip() or None
        handled_at = step.get("handleDateTime")

        nodes.append(
            KsmNode(
                node_name=node_name,
                handler_name=handler or "客户/系统",
                handled_at=handled_at if isinstance(handled_at, str) and handled_at else None,
                content=content,
                done=str(step.get("nodeStatus") or "") == "1",
            )
        )
    return nodes
