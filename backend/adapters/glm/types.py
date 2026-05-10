"""GLM adapter DTOs.

Request/response shapes mirror OpenAI Chat Completions (智谱 v4 API
intentionally compatible). We don't 100% mirror — only the fields we
actually use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(slots=True, frozen=True)
class GLMConfig:
    api_key: str
    base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    default_model: str = "glm-4.5-flash"
    timeout_seconds: float = 60.0

    @classmethod
    def from_settings(cls, settings: Any) -> GLMConfig:
        return cls(api_key=getattr(settings, "glm_api_key", ""))


@dataclass(slots=True)
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(slots=True)
class ChatRequest:
    messages: list[ChatMessage]
    model: str | None = None              # falls back to GLMConfig.default_model
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    response_format: dict[str, Any] | None = None  # {"type": "json_object"}

    def to_payload(self, default_model: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model or default_model,
            "messages": [m.to_dict() for m in self.messages],
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.top_p is not None:
            body["top_p"] = self.top_p
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens
        if self.response_format is not None:
            body["response_format"] = self.response_format
        return body


@dataclass(slots=True, frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(slots=True, frozen=True)
class ChatChoice:
    index: int
    role: str
    content: str
    finish_reason: str | None


@dataclass(slots=True, frozen=True)
class ChatResponse:
    id: str
    model: str
    choices: list[ChatChoice]
    usage: Usage
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def first(self) -> ChatChoice:
        return self.choices[0]

    @property
    def text(self) -> str:
        return self.first.content
