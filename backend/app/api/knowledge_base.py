"""Knowledge Base management API endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps.auth import AuthedUser, require_user
from app.db import get_session
from app.models import Ticket
from app.repositories.knowledge_base import KnowledgeBaseRepository

router = APIRouter()


class KnowledgeItemOut(BaseModel):
    id: str
    title: str
    content: str
    type: str
    product_line_code: str
    product_line_name: str
    module_code: str
    module_name: str
    status: str
    created_by: str
    created_by_user_id: int | None = None
    ticket_id: int | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    total_calls: int = 0
    recent_calls: int = 0
    attachments: list[dict[str, Any]] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeItemListResponse(BaseModel):
    items: list[KnowledgeItemOut]
    total: int
    page: int
    page_size: int


class CreateKnowledgeItemBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1)
    type: str = Field(..., pattern="^(FAQ|操作手册|交付配置)$")
    product_line_code: str = Field(..., min_length=1, max_length=64)
    product_line_name: str = Field(..., min_length=1, max_length=128)
    module_code: str = Field(..., min_length=1, max_length=64)
    module_name: str = Field(..., min_length=1, max_length=128)
    status: str = Field("pending_review", pattern="^(pending_review|active|rejected|offline)$")
    created_by: str | None = Field(None, max_length=64)
    ticket_id: int | None = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class UpdateKnowledgeItemBody(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    content: str | None = Field(None, min_length=1)
    type: str | None = Field(None, pattern="^(FAQ|操作手册|交付配置)$")
    product_line_code: str | None = Field(None, max_length=64)
    product_line_name: str | None = Field(None, max_length=128)
    module_code: str | None = Field(None, max_length=64)
    module_name: str | None = Field(None, max_length=128)
    status: str | None = Field(None, pattern="^(pending_review|active|rejected|offline)$")
    attachments: list[dict[str, Any]] | None = None


class BatchReviewBody(BaseModel):
    ids: list[str] = Field(..., min_length=1)
    action: str = Field(..., pattern="^(approve|reject)$")


class BatchStatusBody(BaseModel):
    ids: list[str] = Field(..., min_length=1)


@router.get("", response_model=KnowledgeItemListResponse)
def list_knowledge_items(
    type_: str | None = Query(None, alias="type"),
    status: str | None = Query(None),
    statuses: list[str] | None = Query(None),
    product_line_code: str | None = Query(None),
    module_code: str | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> KnowledgeItemListResponse:
    """获取知识库列表（多维服务端筛选与分页）。"""
    repo = KnowledgeBaseRepository(db)
    items, total = repo.list_paginated(
        type_=type_,
        status=status,
        statuses=statuses,
        product_line_code=product_line_code,
        module_code=module_code,
        search=search,
        page=page,
        page_size=page_size,
    )
    return KnowledgeItemListResponse(
        items=[KnowledgeItemOut.model_validate(it) for it in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=KnowledgeItemOut, status_code=201)
def create_knowledge_item(
    body: CreateKnowledgeItemBody,
    user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> KnowledgeItemOut:
    """创建知识库条目：默认状态为 pending_review (待审核)，编号按规则生成。"""
    repo = KnowledgeBaseRepository(db)

    # 确定创建人：若从工单详情页发起且指定了工单处理人姓名，优先使用；
    # 若传入了 ticket_id 则尝试反查工单处理人；否则回落当前操作人
    effective_creator = (body.created_by or "").strip()
    creator_user_id: int | None = user.user_id

    if body.ticket_id is not None:
        t = db.get(Ticket, body.ticket_id)
        if t is not None and not effective_creator:
            from app.models import User

            handler_id = t.handler_user_id or t.assigned_user_id
            if handler_id:
                hu = db.get(User, handler_id)
                if hu and hu.name:
                    effective_creator = hu.name
                    creator_user_id = hu.id

    if not effective_creator:
        effective_creator = user.name or "系统用户"

    item = repo.create(
        title=body.title,
        content=body.content,
        type_=body.type,
        product_line_code=body.product_line_code,
        product_line_name=body.product_line_name,
        module_code=body.module_code,
        module_name=body.module_name,
        status=body.status or "pending_review",
        created_by=effective_creator,
        created_by_user_id=creator_user_id,
        ticket_id=body.ticket_id,
        attachments=body.attachments,
    )
    db.commit()
    db.refresh(item)
    return KnowledgeItemOut.model_validate(item)


@router.get("/{item_id}", response_model=KnowledgeItemOut)
def get_knowledge_item(
    item_id: str,
    _user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> KnowledgeItemOut:
    """获取单个知识库条目详情。"""
    item = KnowledgeBaseRepository(db).get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="知识库条目不存在")
    return KnowledgeItemOut.model_validate(item)


@router.put("/{item_id}", response_model=KnowledgeItemOut)
def update_knowledge_item(
    item_id: str,
    body: UpdateKnowledgeItemBody,
    _user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> KnowledgeItemOut:
    """更新知识库条目。"""
    repo = KnowledgeBaseRepository(db)
    item = repo.update(
        item_id,
        title=body.title,
        content=body.content,
        type_=body.type,
        product_line_code=body.product_line_code,
        product_line_name=body.product_line_name,
        module_code=body.module_code,
        module_name=body.module_name,
        status=body.status,
        attachments=body.attachments,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="知识库条目不存在")
    db.commit()
    db.refresh(item)
    return KnowledgeItemOut.model_validate(item)


@router.delete("/{item_id}", status_code=204)
def delete_knowledge_item(
    item_id: str,
    _user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> Response:
    """软删除知识库条目。"""
    repo = KnowledgeBaseRepository(db)
    success = repo.delete(item_id)
    if not success:
        raise HTTPException(status_code=404, detail="知识库条目不存在")
    db.commit()
    return Response(status_code=204)


@router.post("/batch-review", response_model=dict[str, Any])
def batch_review_knowledge(
    body: BatchReviewBody,
    user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """批量审核通过或驳回。"""
    repo = KnowledgeBaseRepository(db)
    count = repo.batch_review(
        body.ids,
        action=body.action,
        reviewer_name=user.name or "当前主管",
        reviewer_user_id=user.user_id,
    )
    db.commit()
    return {"reviewed_count": count, "action": body.action}


@router.post("/batch-offline", response_model=dict[str, Any])
def batch_offline_knowledge(
    body: BatchStatusBody,
    _user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """批量下架知识条目。"""
    repo = KnowledgeBaseRepository(db)
    count = repo.batch_status(body.ids, status="offline")
    db.commit()
    return {"offline_count": count}


@router.post("/batch-online", response_model=dict[str, Any])
def batch_online_knowledge(
    body: BatchStatusBody,
    _user: AuthedUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """批量上架知识条目。"""
    repo = KnowledgeBaseRepository(db)
    count = repo.batch_status(body.ids, status="active")
    db.commit()
    return {"online_count": count}
