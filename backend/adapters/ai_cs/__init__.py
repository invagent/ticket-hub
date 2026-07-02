"""AI 客服 adapter — public API."""

from .client import AiCsClient
from .exceptions import (
    AiCsAuthError,
    AiCsBusinessError,
    AiCsError,
    AiCsNetworkError,
)
from .types import (
    AiCsConfig,
    DraftSummary,
    ReplayResult,
    SkillDetail,
    SkillFile,
    SkillSummary,
    SkillVersion,
)

__all__ = [
    "AiCsAuthError",
    "AiCsBusinessError",
    "AiCsClient",
    "AiCsConfig",
    "AiCsError",
    "AiCsNetworkError",
    "DraftSummary",
    "ReplayResult",
    "SkillDetail",
    "SkillFile",
    "SkillSummary",
    "SkillVersion",
]
