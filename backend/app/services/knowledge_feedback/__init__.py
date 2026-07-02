"""Phase 1 知识反哺闭环 — 主管从 escalation 工单反思 AI 客服 skill。

Flow: escalation 工单（AI 客服答错被客户投诉）→ 主管看黄金三元组 → 改 skill draft
→ replay 用 draft 就同一问题重答 → 对比旧/新答复 → 满意则 publish。

对接 adapters/ai_cs/（skill-management.json wire format）。默认关，
`knowledge_feedback_enabled` + `ai_cs_app_id/app_key` 配好才可用。
"""

from .service import (
    EscalationContext,
    KnowledgeFeedbackDisabledError,
    build_client,
    load_escalation_context,
    record_publish_audit,
)

__all__ = [
    "EscalationContext",
    "KnowledgeFeedbackDisabledError",
    "build_client",
    "load_escalation_context",
    "record_publish_audit",
]
