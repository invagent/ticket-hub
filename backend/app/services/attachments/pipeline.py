"""异步附件流水线：download → MinIO → vision OCR。

只处理 vision_status=='queued' 的行（KSM 附件；escalation 的 'pending' 归 ingest 链 vision_extract）。
enabled 关 → 整体不扫。dry_run 开 → 只标 skipped 不下载。
单条 try/except 隔离；下载/上传/vision 失败 attempts++，超 max_attempts 标 failed。
MinIO 未配置 → 整批标 failed 转人工，绝不静默成功。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from adapters.ksm.client import KSMClient
from adapters.ksm.types import KSMConfig
from app.config import Settings, get_settings
from app.core.llm_router.vision import VisionClient
from app.core.logging import get_logger
from app.core.storage.minio_store import (
    MinioNotConfiguredError,
    MinioStore,
    attachment_object_key,
)
from app.models import Attachment, Ticket

logger = get_logger(__name__)

# 复用 vision_extract 的识别提示语（保持 OCR 提取口径一致）。
_VISION_PROMPT = "识别图片中的报错文本与界面上下文，简述问题。"


@dataclass(slots=True)
class AttachmentDrainReport:
    scanned: int = 0
    extracted: int = 0
    skipped: int = 0
    failed: int = 0


def drain_pending_attachments(
    db: Session,
    *,
    ksm_client: KSMClient | None = None,
    store: MinioStore | None = None,
    vision_client: VisionClient | None = None,
    limit: int = 20,
) -> AttachmentDrainReport:
    settings = get_settings()
    report = AttachmentDrainReport()
    if not settings.attachment_pipeline_enabled:
        return report

    rows = (
        db.execute(
            select(Attachment)
            .where(
                and_(
                    Attachment.vision_status == "queued",
                    Attachment.storage_key.is_(None),
                    Attachment.kind == "image",
                    Attachment.source_url.is_not(None),
                )
            )
            .limit(limit)
        )
        .scalars()
        .all()
    )
    report.scanned = len(rows)
    if not rows:
        return report

    # dry_run 短路：不建 client、不下载，且**不改行状态**——留 queued。
    # 观察窗口（enabled+dry_run）期间到达的附件，翻掉 dry_run 后仍会被扫到真正处理，
    # 不会永久卡在 skipped（否则需手工 UPDATE 回 queued 才能补 OCR）。report 里计入 skipped 只作观察。
    if settings.attachment_pipeline_dry_run:
        report.skipped = len(rows)
        return report

    # 惰性建 store（未配置 MinIO → 全批标 failed 转人工，不静默成功）。
    try:
        store = store or MinioStore(settings)
    except MinioNotConfiguredError as e:
        for att in rows:
            att.vision_status = "failed"
            att.last_error = f"minio_not_configured: {e}"
            report.failed += 1
        db.flush()
        logger.error("attachment_pipeline_minio_unconfigured", count=len(rows))
        return report

    # Task 2 review note：ensure_bucket 提前调一次热身，避免 drain 循环里
    # put_bytes 每行都重复往返（put_bytes 内部仍保留自己的调用，不删）。
    store.ensure_bucket()

    ksm_client = ksm_client or KSMClient(KSMConfig.from_settings(settings))
    vision_client = vision_client or VisionClient.from_settings()

    for att in rows:
        status = process_one(
            db,
            att,
            store=store,
            ksm_client=ksm_client,
            vision_client=vision_client,
            settings=settings,
        )
        if status == "extracted":
            report.extracted += 1
        elif status == "skipped":
            report.skipped += 1
        elif status == "failed":
            report.failed += 1
        # queued（重试留待下轮）不计入终态
    db.flush()
    return report


def process_one(
    db: Session,
    att: Attachment,
    *,
    store: MinioStore,
    ksm_client: KSMClient,
    vision_client: VisionClient,
    settings: Settings,
) -> str:
    try:
        assert att.source_url is not None  # drain 查询已过滤 source_url IS NOT NULL
        img = ksm_client.download_attachment(att.source_url)
        if len(img) > settings.attachment_max_bytes:
            att.size_bytes = len(img)
            att.vision_status = "skipped"
            att.last_error = f"oversize:{len(img)}"
            return "skipped"

        key = attachment_object_key(att.ticket_id, att.id, att.filename)
        content_type = att.mime or "image/png"
        # 先只留局部变量，不写 att.storage_key：drain 扫描条件是 storage_key IS NULL，
        # 若下面 vision 失败就 return，让这行继续可被扫到重试（否则会卡成 stuck row）。
        # key 是确定性的，重试重新上传会覆盖同一个 MinIO 对象，不产生重复。
        public_url = store.put_bytes(key, img, content_type)
        size_bytes = len(img)

        result = vision_client.extract(prompt=_VISION_PROMPT, image_bytes=img, mime=content_type)
        text = getattr(result, "ocr_text", None) or getattr(result, "text", None) or ""

        # OCR 也成功了，这里才把 storage_key 落地，标记为终态。
        att.storage_key = public_url
        att.size_bytes = size_bytes
        att.extracted_text = text
        att.vision_model = getattr(result, "model", None) or settings.vision_model
        att.vision_cost_usd = getattr(result, "cost_usd", None)
        att.vision_status = "extracted"

        # 追加 OCR 文本到 ticket.body（与 vision_extract 格式一致，"[附件识别]" 段）。
        if text:
            ticket = db.get(Ticket, att.ticket_id)
            if ticket:
                existing = ticket.body or ""
                ticket.body = (existing + "\n\n" + f"[附件识别] {text}").strip()
        return "extracted"

    except Exception as e:  # 逐条隔离，绝不让单条异常打断整批
        att.download_attempts = (att.download_attempts or 0) + 1
        att.last_error = str(e)[:512]
        if att.download_attempts >= settings.attachment_max_attempts:
            att.vision_status = "failed"
            logger.error("attachment_process_failed", att_id=att.id, error=str(e))
            return "failed"
        logger.warning(
            "attachment_process_retry",
            att_id=att.id,
            attempts=att.download_attempts,
            error=str(e),
        )
        return "queued"
