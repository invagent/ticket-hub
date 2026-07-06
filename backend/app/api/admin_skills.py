"""Admin /api/admin/skills/* — DB 化提示词的查看/编辑/版本/回滚/预览/导入.

全部 require_admin。对标 sample 的 skill 编辑后端，套主项目治理（审计在 history 表）。

  GET  /api/admin/skills                  列出提示词
  GET  /api/admin/skills/{name}           取正文 + 三槽（current/draft/previous）
  GET  /api/admin/skills/{name}/history   历史版本
  PUT  /api/admin/skills/{name}           直接编辑 current（升版 + 留历史）
  PUT  /api/admin/skills/{name}/draft     保存 draft（不生效）
  DELETE /api/admin/skills/{name}/draft   丢弃 draft
  POST /api/admin/skills/{name}/draft/validate  draft 差异回放验证（ADR-0016）
  POST /api/admin/skills/{name}/draft/promote   draft → current（升版留历史）
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
    # 三槽（ADR-0016 P1）
    draft_md: str | None = None
    draft_updated_by: str | None = None
    draft_updated_at: datetime | None = None
    previous_version: int | None = None
    previous_md: str | None = None


class DraftBody(BaseModel):
    content_md: str = Field(..., min_length=1)


class DraftResponse(BaseModel):
    name: str
    has_draft: bool


class ValidateBody(BaseModel):
    sample: int = Field(default=8, ge=1, le=20)


class ValidationRowOut(BaseModel):
    ticket_id: int
    short_code: str
    title: str | None
    current_type: str | None
    current_confidence: float | None
    draft_type: str | None
    draft_confidence: float | None
    changed: bool
    error: str | None


class ValidationReportOut(BaseModel):
    supported: bool
    message: str
    sample_size: int
    changed_count: int
    error_count: int
    rows: list[ValidationRowOut]


class PromoteBody(BaseModel):
    reason: str = ""


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
    prev = ps.get_previous_content(db, name)
    return SkillDetail(
        name=row.name,
        type=row.type,
        editable=row.editable,
        version=row.version,
        description=row.description,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
        content_md=row.content_md,
        draft_md=row.draft_md,
        draft_updated_by=row.draft_updated_by,
        draft_updated_at=row.draft_updated_at,
        previous_version=prev[0] if prev else None,
        previous_md=prev[1] if prev else None,
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


@router.put("/{name}/draft", response_model=DraftResponse)
def save_draft_endpoint(
    name: str,
    body: DraftBody,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> DraftResponse:
    """保存 draft 槽（不生效、不影响 load_prompt）。"""
    try:
        ps.save_draft(db, name, body.content_md, operator=f"user:{admin.name}")
    except ps.PromptEditError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return DraftResponse(name=name, has_draft=True)


@router.delete("/{name}/draft", response_model=DraftResponse)
def discard_draft_endpoint(
    name: str,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> DraftResponse:
    try:
        ps.discard_draft(db, name, operator=f"user:{admin.name}")
    except ps.PromptEditError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return DraftResponse(name=name, has_draft=False)


@router.post("/{name}/draft/validate", response_model=ValidationReportOut)
def validate_draft_endpoint(
    name: str,
    body: ValidateBody,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> ValidationReportOut:
    """draft 差异回放：current vs draft 各跑最近 N 条真实工单，报差异。
    同步 LLM 调用 2×N 次——主管等结果，sample 上限 20。"""
    from dataclasses import asdict

    from app.services.skills.draft_validator import validate_draft

    report = validate_draft(db, name, sample=body.sample)
    logger.info(
        "admin_skill_draft_validated",
        name=name,
        by=admin.user_id,
        changed=report.changed_count,
    )
    return ValidationReportOut(
        supported=report.supported,
        message=report.message,
        sample_size=report.sample_size,
        changed_count=report.changed_count,
        error_count=report.error_count,
        rows=[ValidationRowOut(**asdict(r)) for r in report.rows],
    )


@router.post("/{name}/draft/promote", response_model=EditResponse)
def promote_draft_endpoint(
    name: str,
    body: PromoteBody,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> EditResponse:
    """draft → current（升版留历史，旧 current 成为 previous），清空 draft。"""
    try:
        version = ps.promote_draft(db, name, operator=f"user:{admin.name}", reason=body.reason)
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
