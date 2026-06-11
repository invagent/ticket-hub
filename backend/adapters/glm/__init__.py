"""智谱 BigModel (GLM) HTTP client adapter.

Endpoint:    https://open.bigmodel.cn/api/paas/v4/chat/completions
Auth:        Authorization: Bearer <API_KEY>
Body shape:  OpenAI-compatible (messages / model / temperature / etc.)
Pricing:     glm-4.5-flash (cheapest), glm-4-air, glm-4-plus
"""

from .client import GLMClient
from .exceptions import (
    GLMAuthError,
    GLMBusinessError,
    GLMError,
    GLMNetworkError,
)
from .types import (
    ChatChoice,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    GLMConfig,
    Usage,
)

__all__ = [
    "ChatChoice",
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "GLMAuthError",
    "GLMBusinessError",
    "GLMClient",
    "GLMConfig",
    "GLMError",
    "GLMNetworkError",
    "Usage",
]
