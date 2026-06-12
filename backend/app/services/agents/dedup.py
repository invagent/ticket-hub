"""Dedup agent (D3-E) — cross-source duplicate-ticket detection.

Pipeline (BackgroundTask after ingest, mirrors conflict_detect):
    1. Embed f"{title}\\n{body}" via EmbeddingClient, upsert ticket_embeddings
    2. Recall: cosine similarity against the most recent N embedded tickets
       (Python brute force over a bounded pool — deliberate non-pgvector
       choice at current volume, see ticket_embeddings model docstring)
    3. No candidate ≥ threshold → write decision_type='dedup_new'
       (recall-only, zero LLM cost)
    4. Otherwise LLM judges the top-k candidates → 'dedup_link' (with
       duplicate_of_ticket_id) or 'dedup_new'

D3-E scope: ADVISORY ONLY — agent_decisions audit rows, no ticket mutation.
Supervisor tooling to act on dedup_link proposals comes later (same 灰度
playbook as split: audit first, then manual execute, then auto).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.llm_router import LLMMessage, LLMRouter, LLMRouterError
from app.core.llm_router.embeddings import EmbeddingClient, EmbeddingError
from app.core.logging import get_logger
from app.db import make_session
from app.models import AgentDecision, Ticket, TicketEmbedding

logger = get_logger(__name__)

_VALID_DECISIONS = frozenset({"duplicate", "new"})

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "prompts"


def _prompt_version() -> str:
    return get_settings().dedup_prompt_version


def _load_system_prompt() -> str:
    return (_PROMPTS_DIR / f"dedup_{_prompt_version()}.md").read_text(encoding="utf-8")


class DedupError(Exception):
    """LLM call succeeded but output couldn't be parsed/validated."""


@dataclass(slots=True, frozen=True)
class Candidate:
    ticket_id: int
    short_code: str
    title: str
    similarity: float


@dataclass(slots=True, frozen=True)
class DedupResult:
    decision: str  # "duplicate" | "new"
    duplicate_of_ticket_id: int | None
    confidence: float
    reason: str
    candidates: tuple[Candidate, ...]
    method: str  # "recall_only" | "llm"
    cost_usd: float
    model: str


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Plain cosine; returns 0.0 on dim mismatch or zero vector (treat as
    no-signal rather than erroring — mismatches happen when the embedding
    model changes between deployments)."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _embedding_text(title: str | None, body: str | None) -> str:
    # Same trim policy as classify/conflict_detect.
    return f"{title or ''}\n{(body or '')[:1500]}".strip()


def upsert_ticket_embedding(
    db: Session,
    ticket: Ticket,
    *,
    client: EmbeddingClient | None = None,
) -> TicketEmbedding:
    """Embed the ticket text and upsert the ticket_embeddings row. Flushes,
    caller commits."""
    client = client or EmbeddingClient.from_settings()
    result = client.embed([_embedding_text(ticket.title, ticket.body)])
    vector = result.vectors[0]
    row = db.get(TicketEmbedding, ticket.id)
    if row is None:
        row = TicketEmbedding(
            ticket_id=ticket.id, model=result.model, dim=len(vector), vector=vector
        )
        db.add(row)
    else:
        row.model = result.model
        row.dim = len(vector)
        row.vector = vector
    db.flush()
    return row


def recall_candidates(
    db: Session,
    ticket: Ticket,
    vector: list[float],
    *,
    threshold: float,
    top_k: int,
    pool: int,
) -> list[Candidate]:
    """Cosine scan over the most recent `pool` embedded tickets (excluding
    self, deleted, and Child tickets — children are internal artifacts of a
    split, never dedup targets)."""
    rows = (
        db.query(TicketEmbedding, Ticket)
        .join(Ticket, Ticket.id == TicketEmbedding.ticket_id)
        .filter(
            TicketEmbedding.ticket_id != ticket.id,
            Ticket.deleted_at.is_(None),
            Ticket.type.in_(("Raw", "Parent")),
        )
        .order_by(TicketEmbedding.ticket_id.desc())
        .limit(pool)
        .all()
    )
    scored = [
        Candidate(
            ticket_id=t.id,
            short_code=t.short_code,
            title=t.title or "",
            similarity=round(cosine_similarity(vector, emb.vector), 4),
        )
        for emb, t in rows
    ]
    hits = sorted(
        (c for c in scored if c.similarity >= threshold),
        key=lambda c: c.similarity,
        reverse=True,
    )
    return hits[:top_k]


def judge_duplicate_payload(
    *,
    title: str | None,
    body: str | None,
    candidates: list[Candidate],
    candidate_bodies: dict[int, str],
    router: LLMRouter | None = None,
) -> tuple[dict[str, Any], float, str]:
    """LLM judgment over recalled candidates. Returns (parsed, cost, model).

    Pure-ish (no DB writes); caller persists.
    """
    router = router or LLMRouter.from_settings()
    lines = [f"新工单：\ntitle={title!r}\nbody={(body or '')[:1500]!r}\n", "候选："]
    for c in candidates:
        snippet = (candidate_bodies.get(c.ticket_id) or "")[:500]
        lines.append(
            f"[ticket_id={c.ticket_id}] similarity={c.similarity} "
            f"title={c.title!r} body={snippet!r}"
        )
    resp = router.complete(
        [
            LLMMessage(role="system", content=_load_system_prompt()),
            LLMMessage(role="user", content="\n".join(lines)),
        ],
        agent=f"dedup_{_prompt_version()}",
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    parsed = _parse_response(resp.content, candidate_ids={c.ticket_id for c in candidates})
    return parsed, resp.cost_usd, resp.model


def _parse_response(content: str, *, candidate_ids: set[int]) -> dict[str, Any]:
    try:
        data = json.loads(content.strip())
    except json.JSONDecodeError as e:
        raise DedupError(f"non-JSON LLM output: {content[:120]!r}") from e
    if not isinstance(data, dict):
        raise DedupError(f"expected JSON object, got {type(data).__name__}")

    decision = data.get("decision")
    if decision not in _VALID_DECISIONS:
        raise DedupError(
            f"invalid decision {decision!r}; must be one of {sorted(_VALID_DECISIONS)}"
        )
    try:
        c = float(data["confidence"])
    except (KeyError, TypeError, ValueError) as e:
        raise DedupError(f"missing/invalid confidence: {data!r}") from e
    if not 0.0 <= c <= 1.0:
        raise DedupError(f"confidence out of range: {c}")

    dup_id = data.get("duplicate_of_ticket_id")
    if decision == "duplicate":
        if not isinstance(dup_id, int):
            raise DedupError(f"decision=duplicate needs integer duplicate_of_ticket_id: {data!r}")
        if dup_id not in candidate_ids:
            raise DedupError(
                f"duplicate_of_ticket_id {dup_id} not among candidates {sorted(candidate_ids)}"
            )
    else:
        data["duplicate_of_ticket_id"] = None
    return data


def detect_ticket_duplicate(ticket_id: int, db: Session | None = None) -> DedupResult | None:
    """BackgroundTask body. Returns None on any failure (logged); never
    raises. Writes ONLY an agent_decisions audit row — no ticket mutation.
    """
    own_session = db is None
    if own_session:
        db = make_session()
    assert db is not None

    settings = get_settings()
    try:
        t = db.get(Ticket, ticket_id)
        if t is None or t.deleted_at is not None:
            logger.warning("dedup_ticket_not_found", ticket_id=ticket_id)
            return None
        if t.type != "Raw":
            logger.info("dedup_skip_non_raw", ticket_id=ticket_id, type=t.type)
            return None

        try:
            emb_row = upsert_ticket_embedding(db, t)
        except (EmbeddingError, ValueError) as e:
            logger.warning("dedup_embedding_failed", ticket_id=ticket_id, error=str(e))
            db.rollback()
            return None

        candidates = recall_candidates(
            db,
            t,
            emb_row.vector,
            threshold=settings.dedup_recall_threshold,
            top_k=settings.dedup_recall_top_k,
            pool=settings.dedup_candidate_pool,
        )

        if not candidates:
            result = DedupResult(
                decision="new",
                duplicate_of_ticket_id=None,
                confidence=1.0,
                reason="no recall candidate above similarity threshold",
                candidates=(),
                method="recall_only",
                cost_usd=0.0,
                model="",
            )
        else:
            bodies = {
                row.id: (row.body or "")
                for row in db.query(Ticket)
                .filter(Ticket.id.in_([c.ticket_id for c in candidates]))
                .all()
            }
            try:
                parsed, cost, model = judge_duplicate_payload(
                    title=t.title,
                    body=t.body,
                    candidates=candidates,
                    candidate_bodies=bodies,
                )
            except (DedupError, LLMRouterError) as e:
                logger.warning("dedup_judge_failed", ticket_id=ticket_id, error=str(e))
                return None
            result = DedupResult(
                decision=str(parsed["decision"]),
                duplicate_of_ticket_id=parsed.get("duplicate_of_ticket_id"),
                confidence=float(parsed["confidence"]),
                reason=str(parsed.get("reason") or ""),
                candidates=tuple(candidates),
                method="llm",
                cost_usd=cost,
                model=model,
            )

        decision_type = "dedup_link" if result.decision == "duplicate" else "dedup_new"
        db.add(
            AgentDecision(
                decision_type=decision_type,
                subject_type="ticket",
                subject_id=t.id,
                proposal={
                    "decision": result.decision,
                    "duplicate_of_ticket_id": result.duplicate_of_ticket_id,
                    "confidence": result.confidence,
                    "reason": result.reason,
                    "method": result.method,
                    "candidates": [
                        {
                            "ticket_id": c.ticket_id,
                            "short_code": c.short_code,
                            "similarity": c.similarity,
                        }
                        for c in result.candidates
                    ],
                    "embedding_model": emb_row.model,
                    "model": result.model,
                    "cost_usd": result.cost_usd,
                    "prompt_version": _prompt_version(),
                },
            )
        )
        db.commit()
        logger.info(
            "dedup_committed",
            ticket_id=ticket_id,
            short_code=t.short_code,
            decision=result.decision,
            duplicate_of_ticket_id=result.duplicate_of_ticket_id,
            method=result.method,
            candidate_count=len(result.candidates),
            cost_usd=result.cost_usd,
        )
        return result
    except Exception:  # defensive: BG task must not propagate
        if own_session:
            db.rollback()
        logger.exception("dedup_unexpected_failure", ticket_id=ticket_id)
        return None
    finally:
        if own_session:
            db.close()
