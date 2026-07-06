"""hub 级语义去重（D4 优化 v2 §三需求4，移植 sample _hub_dedup）.

建 Linear 前对 hub embedding，召回同产品线**已推送 Linear** 的历史 hub，余弦 top_k +
LLM 确认 → 命中则当前 hub supersede 到已有 hub、不重复建 Linear（保证一 hub 一 Linear）。

降级（embedding/LLM 失败）一律返回 None=不去重，宁可多建一个 issue 也不漏推。
向量存 hub_issues.embedding（JSON），Python 余弦（同 ADR-0015）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.llm_router import LLMMessage, LLMRouter, LLMRouterError
from app.core.llm_router.embeddings import EmbeddingClient, EmbeddingError
from app.core.logging import get_logger
from app.models import HubIssue
from app.services.agents.dedup import cosine_similarity
from app.services.skills.prompt_store import load_prompt

logger = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "prompts"


def _hub_text(hub: HubIssue) -> str:
    return f"{hub.title or ''}\n{(hub.canonical_body or '')[:1500]}".strip()


def ensure_hub_embedding(
    db: Session, hub: HubIssue, *, client: EmbeddingClient | None = None
) -> list[float] | None:
    """给 hub 算并存 embedding（已有则直接返回）。失败返回 None。"""
    if hub.embedding:
        return hub.embedding
    try:
        client = client or EmbeddingClient.from_settings()
        vec = client.embed([_hub_text(hub)]).vectors[0]
    except (EmbeddingError, ValueError) as e:
        logger.warning("hub_embed_failed", hub_issue_id=hub.id, error=str(e))
        return None
    hub.embedding = vec
    db.flush()
    return vec


def find_duplicate_hub(
    db: Session,
    hub: HubIssue,
    *,
    embedding_client: EmbeddingClient | None = None,
    router: LLMRouter | None = None,
) -> int | None:
    """返回与 hub 重复的已有 hub_id，否则 None。永不抛（降级 None）。"""
    settings = get_settings()
    vec = ensure_hub_embedding(db, hub, client=embedding_client)
    if vec is None:
        return None

    rows = (
        db.execute(
            select(HubIssue).where(
                HubIssue.id != hub.id,
                HubIssue.deleted_at.is_(None),
                HubIssue.product_line_code == hub.product_line_code,
                HubIssue.linear_uuid.isnot(None),  # 只跟已推 Linear 的合并
                HubIssue.embedding.isnot(None),
            )
        )
        .scalars()
        .all()
    )
    scored = sorted(
        ((cosine_similarity(vec, r.embedding or []), r) for r in rows),
        key=lambda t: t[0],
        reverse=True,
    )
    top = [(s, r) for s, r in scored if s >= settings.hub_dedup_threshold][
        : settings.hub_dedup_top_k
    ]
    if not top:
        return None

    cand_list = [
        {
            "hub_id": r.id,
            "title": r.title,
            "problem_summary": (r.canonical_body or "")[:300],
            "similarity": round(s, 3),
        }
        for s, r in top
    ]
    try:
        prompt = load_prompt("hub_dedup")
        router = router or LLMRouter.from_settings()
        resp = router.complete(
            [
                LLMMessage(role="system", content=prompt),
                LLMMessage(
                    role="user",
                    content=(
                        f"当前 hub：title={hub.title!r} body={(hub.canonical_body or '')[:800]!r}\n"
                        f"候选：{json.dumps(cand_list, ensure_ascii=False)}"
                    ),
                ),
                LLMMessage(role="user", content="只输出 JSON。"),
            ],
            agent="hub_dedup",
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        parsed = _parse(resp.content, {r.id for _, r in top})
    except (LLMRouterError, _HubDedupError) as e:
        logger.warning("hub_dedup_llm_failed", hub_issue_id=hub.id, error=str(e))
        return None

    if parsed.get("is_dup") and parsed.get("dup_hub_id"):
        dup_id = int(parsed["dup_hub_id"])
        logger.info("hub_dedup_hit", hub_issue_id=hub.id, dup_hub_id=dup_id)
        return dup_id
    return None


class _HubDedupError(Exception):
    pass


def _parse(content: str, candidate_ids: set[int]) -> dict[str, Any]:
    try:
        data = json.loads(content.strip())
    except json.JSONDecodeError as e:
        raise _HubDedupError(f"non-JSON: {content[:120]!r}") from e
    if not isinstance(data, dict):
        raise _HubDedupError("not an object")
    if data.get("is_dup"):
        dup = data.get("dup_hub_id")
        if not isinstance(dup, int) or dup not in candidate_ids:
            raise _HubDedupError(f"dup_hub_id {dup!r} not in candidates")
    return data
