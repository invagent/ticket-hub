"""Vision client — D4 第③段 截图识别.

Calls a multimodal model (DashScope qwen-vl-* via the OpenAI-compatible
/chat/completions endpoint) with an image + extraction prompt, returns
structured {ocr_text, ui_context, summary}.

Image input:
    * source_url publicly fetchable → pass as image_url, the model fetches it
    * otherwise a data URL (base64) — caller supplies bytes

Deliberately NOT part of LLMProvider/LLMRouter: multimodal request shape and
the single consumer (vision_extract agent) don't fit the chat abstraction.
Same supplier boundary as the text models (国内管理大模型) so no new PII
exposure — see d4-stage3 design.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings
from app.core.llm_router.providers.dashscope import DASHSCOPE_BASE_URL
from app.core.logging import get_logger

logger = get_logger(__name__)

# USD per 1k tokens — 2026-06 snapshot (qwen-vl), admin updates on change.
_PRICING: dict[str, tuple[float, float]] = {
    "qwen-vl-max": (0.0009, 0.0009),
    "qwen-vl-plus": (0.00021, 0.00063),
}


class VisionError(Exception):
    """Vision call failed or returned unparseable output."""


@dataclass(slots=True, frozen=True)
class VisionResult:
    ocr_text: str  # 图中可见的文字（报错原文等），无则空串
    ui_context: str  # 所在界面/操作路径
    summary: str  # 一句话描述
    model: str
    cost_usd: float
    raw: dict[str, Any]

    def as_body_block(self) -> str:
        """Render for appending into ticket.body (downstream classify/dedup)."""
        parts = []
        if self.summary:
            parts.append(self.summary)
        if self.ocr_text:
            parts.append(f"报错/文字：{self.ocr_text}")
        if self.ui_context:
            parts.append(f"界面：{self.ui_context}")
        return "[附件识别] " + " ".join(parts) if parts else ""


def _calc_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = _PRICING.get(model)
    if pricing is None:
        return 0.0
    in_per_k, out_per_k = pricing
    return round((input_tokens / 1000.0) * in_per_k + (output_tokens / 1000.0) * out_per_k, 6)


@dataclass(slots=True)
class VisionClient:
    api_key: str
    model: str
    base_url: str = DASHSCOPE_BASE_URL
    timeout_seconds: float = 60.0

    @classmethod
    def from_settings(cls) -> VisionClient:
        settings = get_settings()
        key = settings.vision_api_key or settings.dashscope_api_key
        if not key:
            raise VisionError("no vision API key (set VISION_API_KEY or DASHSCOPE_API_KEY)")
        return cls(api_key=key, model=settings.vision_model)

    def extract(
        self,
        *,
        prompt: str,
        image_url: str | None = None,
        image_bytes: bytes | None = None,
        mime: str = "image/png",
    ) -> VisionResult:
        """One image → structured extraction. Provide image_url OR image_bytes."""
        if not image_url and not image_bytes:
            raise VisionError("extract() needs image_url or image_bytes")
        if image_bytes is not None:
            b64 = base64.b64encode(image_bytes).decode("ascii")
            url = f"data:{mime};base64,{b64}"
        else:
            assert image_url is not None
            url = image_url

        content = [
            {"type": "image_url", "image_url": {"url": url}},
            {"type": "text", "text": prompt},
        ]
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": content}],
                    "temperature": 0.0,
                },
                timeout=self.timeout_seconds,
            )
        except httpx.TransportError as e:
            raise VisionError(f"network error: {e}") from e
        if not resp.is_success:
            raise VisionError(f"HTTP {resp.status_code}: {resp.text[:200]}")

        body = resp.json()
        try:
            text = body["choices"][0]["message"]["content"]
            usage = body.get("usage") or {}
        except (KeyError, IndexError, TypeError) as e:
            raise VisionError(f"unexpected response: {str(body)[:200]}") from e
        # qwen-vl may return content as a list of parts in some modes.
        if isinstance(text, list):
            text = "".join(p.get("text", "") for p in text if isinstance(p, dict))

        parsed = _parse(text)
        cost = _calc_cost(
            self.model,
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
        )
        logger.info("vision_extract_ok", model=self.model, cost_usd=cost)
        return VisionResult(
            ocr_text=parsed["ocr_text"],
            ui_context=parsed["ui_context"],
            summary=parsed["summary"],
            model=self.model,
            cost_usd=cost,
            raw=body,
        )


def _parse(content: str) -> dict[str, str]:
    """Extract the JSON envelope; tolerate ```json fences and surrounding prose."""
    s = content.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1]
        if s.startswith("json"):
            s = s[4:]
        s = s.strip()
    # find first {...} block if extra prose leaked in
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end != -1 and end > start:
        s = s[start : end + 1]
    try:
        data = json.loads(s)
    except json.JSONDecodeError as e:
        raise VisionError(f"non-JSON vision output: {content[:120]!r}") from e
    if not isinstance(data, dict):
        raise VisionError(f"expected JSON object, got {type(data).__name__}")
    return {
        "ocr_text": str(data.get("ocr_text") or "").strip(),
        "ui_context": str(data.get("ui_context") or "").strip(),
        "summary": str(data.get("summary") or "").strip(),
    }
