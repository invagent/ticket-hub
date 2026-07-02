"""AI 客服 adapter DTOs.

Wire format per skill-management.json (2026-07-03): the self-built AI 客服
system exposes /open-api/* with a {errcode, description, data} envelope,
token auth (get_token + MD5 sign), and a draft→published→superseded skill
lifecycle. Version strings are `{skill_name}:V{N}`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class AiCsConfig:
    app_id: str
    app_key: str
    base_url: str = "http://localhost:9090"
    timeout_seconds: float = 60.0  # replay regenerates an answer (LLM) — allow slack
    # Skills the AI 客服 side allows managing (mirror of its MANAGED_SKILLS env).
    managed_skills: tuple[str, ...] = ("customer-service", "customer-service-feishu")

    @classmethod
    def from_settings(cls, settings: Any) -> AiCsConfig:
        raw = getattr(settings, "ai_cs_managed_skills", "") or ""
        skills = tuple(s.strip() for s in raw.split(",") if s.strip())
        return cls(
            app_id=getattr(settings, "ai_cs_app_id", ""),
            app_key=getattr(settings, "ai_cs_app_key", ""),
            base_url=getattr(settings, "ai_cs_base_url", "") or "http://localhost:9090",
            managed_skills=skills or ("customer-service", "customer-service-feishu"),
        )


@dataclass(slots=True, frozen=True)
class SkillFile:
    """One file inside a skill version. `content` is only populated by
    get_skill (detail) / draft reads — list endpoints omit it."""

    filename: str
    filepath: str
    content: str | None = None


@dataclass(slots=True, frozen=True)
class SkillVersion:
    """A version row from a skill's history."""

    version: str  # {skill_name}:V{N}
    status: str  # published | superseded | draft
    operator: str
    reason: str
    created_at: str


@dataclass(slots=True, frozen=True)
class SkillSummary:
    """One entry from GET /open-api/skills (list)."""

    skill_name: str
    published_version: str
    operator: str
    updated_at: str
    files: list[SkillFile] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class SkillDetail:
    """GET /open-api/skills/{name} — published files + full version history."""

    skill_name: str
    published_version: str
    published_operator: str
    published_reason: str
    published_files: list[SkillFile] = field(default_factory=list)
    history: list[SkillVersion] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class DraftSummary:
    """One entry from GET /open-api/skills/{name}/drafts."""

    version: str
    operator: str
    reason: str
    created_at: str
    files: list[SkillFile] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class ReplayResult:
    """POST /open-api/replay — the regenerated answer for comparison."""

    answer: str
    cited_knowledge: list[dict[str, Any]] = field(default_factory=list)
    skills_used: list[str] = field(default_factory=list)
    trace_id: str = ""
