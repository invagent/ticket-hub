"""Vision extract agent (D4 第③段) — OCR/describe ticket screenshots.

Runs over a ticket's image attachments BEFORE classify so the extracted error
text lands in ticket.body and downstream classify / dedup / escalation_classify
all benefit (dedup recall especially — the embedding text now contains the
error原文).

Per attachment: image → VisionClient → {ocr_text, ui_context, summary} →
attachments.extracted_text + a "[附件识别] …" block appended to ticket.body.
Non-image kinds and over-limit attachments are skipped. All failures are
swallowed (vision_status='failed') — never blocks the ingest chain.

Gated by VISION_ENABLED (default false, 灰度).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.llm_router.vision import VisionClient, VisionError
from app.core.logging import get_logger
from app.db import make_session
from app.models import Attachment, Ticket

logger = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "prompts"


def _load_prompt() -> str:
    from app.services.skills.prompt_store import load_prompt

    return load_prompt("vision_extract")


@dataclass(slots=True)
class VisionExtractReport:
    ticket_id: int
    extracted: int = 0
    skipped: int = 0
    failed: int = 0
    appended_to_body: bool = False


def extract_ticket_attachments(
    ticket_id: int,
    db: Session | None = None,
    *,
    client: VisionClient | None = None,
) -> VisionExtractReport | None:
    """BG task body. Returns None when disabled / ticket missing; otherwise a
    report. Never raises."""
    settings = get_settings()
    report = VisionExtractReport(ticket_id=ticket_id)
    if not settings.vision_enabled:
        return None

    own_session = db is None
    if own_session:
        db = make_session()
    assert db is not None
    try:
        ticket = db.get(Ticket, ticket_id)
        if ticket is None or ticket.deleted_at is not None:
            logger.warning("vision_ticket_not_found", ticket_id=ticket_id)
            return None

        pending = list(
            db.execute(
                select(Attachment)
                .where(
                    Attachment.ticket_id == ticket_id,
                    Attachment.kind == "image",
                    Attachment.vision_status == "pending",
                )
                .order_by(Attachment.id)
                .limit(settings.vision_max_images_per_ticket)
            )
            .scalars()
            .all()
        )
        if not pending:
            return report

        try:
            client = client or VisionClient.from_settings()
        except VisionError as e:
            logger.warning("vision_client_unavailable", ticket_id=ticket_id, error=str(e))
            return None

        prompt = _load_prompt()
        blocks: list[str] = []
        for att in pending:
            if not att.source_url and not att.storage_key:
                att.vision_status = "skipped"
                report.skipped += 1
                continue
            try:
                # MVP: only source_url passthrough (DashScope fetches it).
                # storage_key (MinIO presign/download) lands with KSM 附件接入.
                if not att.source_url:
                    att.vision_status = "skipped"
                    report.skipped += 1
                    continue
                result = client.extract(prompt=prompt, image_url=att.source_url)
            except VisionError as e:
                att.vision_status = "failed"
                report.failed += 1
                logger.warning("vision_extract_failed", attachment_id=att.id, error=str(e))
                continue
            att.vision_status = "extracted"
            att.extracted_text = "\n".join(
                x for x in (result.ocr_text, result.ui_context, result.summary) if x
            )
            att.vision_model = result.model
            att.vision_cost_usd = result.cost_usd
            report.extracted += 1
            block = result.as_body_block()
            if block:
                blocks.append(block)

        if blocks:
            existing = ticket.body or ""
            ticket.body = (existing + "\n\n" + "\n".join(blocks)).strip()
            report.appended_to_body = True

        db.commit()
        logger.info(
            "vision_extract_done",
            ticket_id=ticket_id,
            extracted=report.extracted,
            skipped=report.skipped,
            failed=report.failed,
        )
        return report
    except Exception:  # defensive: BG task must not propagate
        if own_session:
            db.rollback()
        logger.exception("vision_extract_unexpected_failure", ticket_id=ticket_id)
        return None
    finally:
        if own_session:
            db.close()
