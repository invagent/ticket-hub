"""统一工单主状态 stage（ADR-0017）：派生函数 + flush 监听器。"""

from app.services.state.stage import (
    HUB_STAGES,
    LINEAR_STAGES,
    STAGE_TONE,
    STAGE_ZH,
    TERMINAL_STAGES,
    TICKET_STAGES,
    WAITING_HUMAN_STAGES,
    derive_hub_stage,
    derive_ticket_stage,
    stage_label,
)

__all__ = [
    "HUB_STAGES",
    "LINEAR_STAGES",
    "STAGE_TONE",
    "STAGE_ZH",
    "TERMINAL_STAGES",
    "TICKET_STAGES",
    "WAITING_HUMAN_STAGES",
    "derive_hub_stage",
    "derive_ticket_stage",
    "stage_label",
]
