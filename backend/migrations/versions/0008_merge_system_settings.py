"""Merge system_settings branch into main migration chain.

Revision ID: 0008_merge_system_settings
Revises: 0002_system_settings, 0007_d3_a_agent_decisions
Create Date: 2026-05-13 00:00:00.000000
"""

from collections.abc import Sequence

revision: str = "0008_merge_system_settings"
down_revision: tuple[str, str] = ("0002_system_settings", "0007_d3_a_agent_decisions")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
