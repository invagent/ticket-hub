"""Linear adapter DTOs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class LinearConfig:
    api_key: str
    team_id: str
    base_url: str = "https://api.linear.app/graphql"
    timeout_seconds: float = 30.0

    @classmethod
    def from_settings(cls, settings: Any) -> LinearConfig:
        return cls(
            api_key=getattr(settings, "linear_api_key", ""),
            team_id=getattr(settings, "linear_team_id", ""),
        )


@dataclass(slots=True, frozen=True)
class CreateIssueRequest:
    """Input for creating a Linear issue."""

    title: str
    team_id: str
    description: str = ""
    # Label IDs to attach (e.g. "Bug", "Feature")
    label_ids: list[str] = field(default_factory=list)
    # Assignee Linear user ID (users.linear_user_id)
    assignee_id: str | None = None
    # Priority: 0=No priority, 1=Urgent, 2=High, 3=Medium, 4=Low
    priority: int = 0


@dataclass(slots=True, frozen=True)
class CreatedIssue:
    """Minimal fields returned after issue creation."""

    id: str  # Linear UUID
    identifier: str  # e.g. "ENG-123"
    url: str
    title: str
