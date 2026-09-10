"""Knowledge Base repository and ID generator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import KnowledgeBaseItem

BEIJING = timezone(timedelta(hours=8))


def generate_knowledge_id(db: Session, now: datetime | None = None) -> str:
    """生成知识编号：结构 FPYFAQ + YYYYMMDD + 4位流水号（如 FPYFAQ202609100001）。"""
    dt = now or datetime.now(BEIJING)
    prefix = f"FPYFAQ{dt.strftime('%Y%m%d')}"

    stmt = select(func.max(KnowledgeBaseItem.id)).where(KnowledgeBaseItem.id.like(f"{prefix}%"))
    max_id = db.execute(stmt).scalar()
    if max_id and len(max_id) >= len(prefix) + 4:
        try:
            seq = int(max_id[len(prefix) :]) + 1
        except ValueError:
            seq = 1
    else:
        seq = 1

    return f"{prefix}{seq:04d}"


class KnowledgeBaseRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, item_id: str) -> KnowledgeBaseItem | None:
        item = self._db.get(KnowledgeBaseItem, item_id)
        if item is None or item.deleted_at is not None:
            return None
        return item

    def list_paginated(
        self,
        *,
        type_: str | None = None,
        status: str | None = None,
        statuses: list[str] | None = None,
        product_line_code: str | None = None,
        module_code: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[KnowledgeBaseItem], int]:
        base = select(KnowledgeBaseItem).where(KnowledgeBaseItem.deleted_at.is_(None))
        count_base = select(func.count(KnowledgeBaseItem.id)).where(
            KnowledgeBaseItem.deleted_at.is_(None)
        )

        if type_:
            base = base.where(KnowledgeBaseItem.type == type_)
            count_base = count_base.where(KnowledgeBaseItem.type == type_)
        if statuses:
            base = base.where(KnowledgeBaseItem.status.in_(statuses))
            count_base = count_base.where(KnowledgeBaseItem.status.in_(statuses))
        elif status:
            base = base.where(KnowledgeBaseItem.status == status)
            count_base = count_base.where(KnowledgeBaseItem.status == status)
        if product_line_code:
            base = base.where(KnowledgeBaseItem.product_line_code == product_line_code)
            count_base = count_base.where(KnowledgeBaseItem.product_line_code == product_line_code)
        if module_code:
            base = base.where(KnowledgeBaseItem.module_code == module_code)
            count_base = count_base.where(KnowledgeBaseItem.module_code == module_code)
        if search:
            like_pattern = f"%{search.strip()}%"
            cond = or_(
                KnowledgeBaseItem.id.ilike(like_pattern),
                KnowledgeBaseItem.title.ilike(like_pattern),
                KnowledgeBaseItem.content.ilike(like_pattern),
            )
            base = base.where(cond)
            count_base = count_base.where(cond)

        total = self._db.execute(count_base).scalar() or 0
        stmt = (
            base.order_by(KnowledgeBaseItem.created_at.desc(), KnowledgeBaseItem.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = list(self._db.execute(stmt).scalars().all())
        return items, total

    def create(
        self,
        *,
        title: str,
        content: str,
        type_: str,
        product_line_code: str,
        product_line_name: str,
        module_code: str,
        module_name: str,
        created_by: str,
        status: str = "pending_review",
        created_by_user_id: int | None = None,
        ticket_id: int | None = None,
        attachments: list[dict[str, Any]] | None = None,
        item_id: str | None = None,
    ) -> KnowledgeBaseItem:
        final_id = item_id or generate_knowledge_id(self._db)
        item = KnowledgeBaseItem(
            id=final_id,
            title=title.strip(),
            content=content.strip(),
            type=type_,
            product_line_code=product_line_code,
            product_line_name=product_line_name,
            module_code=module_code,
            module_name=module_name,
            status=status,
            created_by=created_by,
            created_by_user_id=created_by_user_id,
            ticket_id=ticket_id,
            attachments=attachments or [],
        )
        self._db.add(item)
        self._db.flush()
        return item

    def update(
        self,
        item_id: str,
        *,
        title: str | None = None,
        content: str | None = None,
        type_: str | None = None,
        product_line_code: str | None = None,
        product_line_name: str | None = None,
        module_code: str | None = None,
        module_name: str | None = None,
        status: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> KnowledgeBaseItem | None:
        item = self.get(item_id)
        if item is None:
            return None
        if title is not None:
            item.title = title.strip()
        if content is not None:
            item.content = content.strip()
        if type_ is not None:
            item.type = type_
        if product_line_code is not None:
            item.product_line_code = product_line_code
        if product_line_name is not None:
            item.product_line_name = product_line_name
        if module_code is not None:
            item.module_code = module_code
        if module_name is not None:
            item.module_name = module_name
        if status is not None:
            item.status = status
        if attachments is not None:
            item.attachments = attachments
        self._db.flush()
        return item

    def batch_review(
        self,
        ids: list[str],
        *,
        action: str,  # 'approve' | 'reject'
        reviewer_name: str,
        reviewer_user_id: int | None = None,
    ) -> int:
        now = datetime.now(UTC)
        target_status = "active" if action == "approve" else "rejected"
        items = (
            self._db.query(KnowledgeBaseItem)
            .filter(
                KnowledgeBaseItem.id.in_(ids),
                KnowledgeBaseItem.deleted_at.is_(None),
            )
            .all()
        )
        count = 0
        for item in items:
            item.status = target_status
            item.reviewed_by = reviewer_name
            item.reviewed_by_user_id = reviewer_user_id
            item.reviewed_at = now
            count += 1
        self._db.flush()
        return count

    def batch_status(self, ids: list[str], *, status: str) -> int:
        items = (
            self._db.query(KnowledgeBaseItem)
            .filter(
                KnowledgeBaseItem.id.in_(ids),
                KnowledgeBaseItem.deleted_at.is_(None),
            )
            .all()
        )
        count = 0
        for item in items:
            item.status = status
            count += 1
        self._db.flush()
        return count

    def delete(self, item_id: str) -> bool:
        item = self.get(item_id)
        if item is None:
            return False
        item.deleted_at = datetime.now(UTC)
        self._db.flush()
        return True
