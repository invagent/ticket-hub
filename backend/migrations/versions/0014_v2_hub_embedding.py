"""D4 优化 v2: hub_issues.embedding — hub 级语义去重向量.

Revision ID: 0014_v2_hub_embedding
Revises: 0013_v2_skill_prompts
Create Date: 2026-06-26

建 Linear 前对 hub embedding 召回同产品线已有 hub，避免重复 issue（一 hub 一 Linear）。
向量存 JSON 列 + Python 余弦（同 ADR-0015）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_v2_hub_embedding"
down_revision: str | Sequence[str] | None = "0013_v2_skill_prompts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("hub_issues", sa.Column("embedding", sa.JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("hub_issues", "embedding")
