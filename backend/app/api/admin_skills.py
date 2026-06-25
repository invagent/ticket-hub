"""Admin /api/admin/skills/* — DB 化提示词的查看/编辑/版本/回滚/预览/导入.

全部 require_admin。对标 sample 的 skill 编辑后端，套主项目治理（审计在 history 表）。

  GET  /api/admin/skills                  列出提示词
  GET  /api/admin/skills/{name}           取正文 + 当前版本
  GET  /api/admin/skills/{name}/history   历史版本
  PUT  /api/admin/skills/{name}           编辑（升版 + 留历史）
  POST /api/admin/skills/{name}/rollback  回滚到某版本
  POST /api/admin/skills/import-from-files 把 prompts/*.md 灌入表（幂等 seed）
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps.auth import AuthedUser, require_admin
from app.core.logging import get_logger
from app.db import get_session
from app.services.skills import prompt_store as ps

router = APIRouter()
logger = get_logger(__name__)


class SkillSummary(BaseModel):
    name: str
    type: str
    editable: bool
    version: int
    description: str | None
    updated_by: str | None
    updated_at: datetime | None


class SkillDetail(SkillSummary):
    content_md: str


class HistoryItem(BaseModel):
    version: int
    changed_by: str | None
    reason: str | None
    changed_at: datetime


class EditBody(BaseModel):
    content_md: str = Field(..., min_length=1)
    reason: str = ""


class EditResponse(BaseModel):
    name: str
    version: int


class RollbackBody(BaseModel):
    version: int


class ImportResponse(BaseModel):
    added: int


@router.get("", response_model=list[SkillSummary])
def list_skills(
    _admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[SkillSummary]:
    return [
        SkillSummary(
            name=p.name,
            type=p.type,
            editable=p.editable,
            version=p.version,
            description=p.description,
            updated_by=p.updated_by,
            updated_at=p.updated_at,
        )
        for p in ps.list_prompts(db)
    ]


@router.get("/{name}", response_model=SkillDetail)
def get_skill(
    name: str,
    _admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> SkillDetail:
    row = ps.get_prompt_row(db, name)
    if row is None:
        raise HTTPException(status_code=404, detail=f"skill {name!r} not found")
    return SkillDetail(
        name=row.name,
        type=row.type,
        editable=row.editable,
        version=row.version,
        description=row.description,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
        content_md=row.content_md,
    )


@router.get("/{name}/history", response_model=list[HistoryItem])
def get_history(
    name: str,
    _admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[HistoryItem]:
    return [
        HistoryItem(
            version=h.version, changed_by=h.changed_by, reason=h.reason, changed_at=h.changed_at
        )
        for h in ps.list_history(db, name)
    ]


@router.put("/{name}", response_model=EditResponse)
def edit_skill(
    name: str,
    body: EditBody,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> EditResponse:
    try:
        version = ps.edit_prompt(
            db, name, body.content_md, operator=f"user:{admin.name}", reason=body.reason
        )
    except ps.PromptEditError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return EditResponse(name=name, version=version)


@router.post("/{name}/rollback", response_model=EditResponse)
def rollback_skill(
    name: str,
    body: RollbackBody,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> EditResponse:
    try:
        version = ps.rollback_prompt(db, name, body.version, operator=f"user:{admin.name}")
    except ps.PromptEditError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return EditResponse(name=name, version=version)


@router.post("/import-from-files", response_model=ImportResponse)
def import_from_files(
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> ImportResponse:
    added = ps.import_prompts_from_files(db)
    logger.info("admin_skills_import", by=admin.user_id, added=added)
    return ImportResponse(added=added)
