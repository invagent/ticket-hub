"""Zhichi adapter DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class ZhichiConfig:
    appid: str
    app_key: str
    base_url: str = "https://www.soboten.com"

    @classmethod
    def from_settings(cls, s: Any) -> ZhichiConfig:
        return cls(appid=s.zhichi_appid, app_key=s.zhichi_app_key)


@dataclass(slots=True, frozen=True)
class Agent:
    """智齿坐席。"""

    agentid: str
    agent_name: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Agent:
        return cls(agentid=str(d.get("agentid", "")), agent_name=str(d.get("agent_name", "")))


@dataclass(slots=True)
class ReplyTicketRequest:
    ticket_id: str
    ticket_title: str
    ticket_content: str
    ticket_status: str
    ticket_level: str
    reply_agentid: str
    reply_agent_name: str
    reply_content: str = ""
    reply_type: str = "0"  # 0=文本; 1=富文本
    reply_file_str: str = ""
