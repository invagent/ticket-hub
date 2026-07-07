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
    # Parent issue UUID — owner-split 子 issue 挂 hub 主 issue（ADR-0016 P4）
    parent_id: str | None = None


@dataclass(slots=True, frozen=True)
class CreatedIssue:
    """Minimal fields returned after issue creation."""

    id: str  # Linear UUID
    identifier: str  # e.g. "ENG-123"
    url: str
    title: str


@dataclass(slots=True, frozen=True)
class IssueState:
    """Issue workflow state snapshot (status back-sync).

    state_type is Linear's workflow category — one of: triage, backlog,
    unstarted, started, completed, canceled. state_name is the display
    name of the column (e.g. "In Progress", "Done").
    """

    id: str  # Linear issue UUID
    identifier: str  # e.g. "CNPRD-809"
    state_name: str
    state_type: str


@dataclass(slots=True, frozen=True)
class LinearTeam:
    id: str  # Linear team UUID
    key: str  # e.g. "CNPRD"
    name: str


@dataclass(slots=True, frozen=True)
class LinearUser:
    """Workspace member + their team memberships (for the user sync)."""

    id: str  # Linear user UUID
    name: str
    email: str
    active: bool
    teams: list[LinearTeam] = field(default_factory=list)
