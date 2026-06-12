"""Admin /api/admin/users/* — users CRUD + supervisor + partner + Feishu sync.

Endpoints (all require role='admin'):

  GET    /api/admin/users                       list active users
  GET    /api/admin/users/{id}                  user detail (aggregated)
  PATCH  /api/admin/users/{id}                  partial update
  DELETE /api/admin/users/{id}                  soft-delete
  POST   /api/admin/users/{id}/supervisor       set supervisor + optional deputy
  DELETE /api/admin/users/{id}/supervisor       clear supervisor
  POST   /api/admin/users/{id}/partners         add a partner
  DELETE /api/admin/users/{id}/partners/{pid}   remove a partner

  POST   /api/admin/users/sync-from-feishu      bulk sync from Feishu contact API
  POST   /api/admin/users/sync-from-linear      match users to Linear members by email
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from adapters.feishu import FeishuClient, FeishuConfig, FeishuError
from adapters.linear import LinearError
from app.api.deps.auth import AuthedUser, require_admin, require_supervisor
from app.config import get_settings
from app.core.logging import get_logger
from app.db import get_session
from app.repositories.assignment_scope import AssignmentScopeAdminRepository
from app.repositories.user import UserRepository
from app.repositories.user_partner import UserPartnerRepository
from app.repositories.user_supervisor import UserSupervisorRepository
from app.services.linear.user_sync import sync_linear_users
from app.services.users.feishu_sync import FeishuUserSyncService

router = APIRouter()
logger = get_logger(__name__)


# ---- DTOs ------------------------------------------------------------


class UserOut(BaseModel):
    id: int
    feishu_uid: str
    employee_no: str | None
    name: str
    email: str | None
    mobile: str | None
    ksm_account: str | None
    zhichi_agent_id: str | None
    linear_user_id: str | None
    linear_team_id: str | None
    role: str
    is_active: bool

    model_config = {"from_attributes": True}


class SupervisorOut(BaseModel):
    user_id: int
    supervisor_id: int
    deputy_supervisor_id: int | None
    updated_at: datetime

    model_config = {"from_attributes": True}


class ScopeRowOut(BaseModel):
    id: int
    user_id: int
    product_line_code: str | None = None
    module: str | None = None
    feature: str | None = None

    model_config = {"from_attributes": True}


class PartnerRowOut(BaseModel):
    id: int
    name: str
    role: str

    model_config = {"from_attributes": True}


class UserDetailOut(BaseModel):
    """Aggregated profile for /admin/users/:id frontend page."""

    user: UserOut
    supervisor: SupervisorOut | None
    module_scopes: list[ScopeRowOut]
    feature_scopes: list[ScopeRowOut]
    partners: list[PartnerRowOut]


class UserPatch(BaseModel):
    role: str | None = Field(default=None, pattern="^(member|assignee|supervisor|admin)$")
    is_active: bool | None = None
    name: str | None = Field(default=None, min_length=1, max_length=128)
    email: str | None = Field(default=None, max_length=255)
    mobile: str | None = Field(default=None, max_length=32)
    employee_no: str | None = None
    ksm_account: str | None = None
    zhichi_agent_id: str | None = None
    linear_user_id: str | None = None


class SupervisorIn(BaseModel):
    supervisor_id: int = Field(..., gt=0)
    deputy_supervisor_id: int | None = Field(default=None, gt=0)


class PartnerIn(BaseModel):
    partner_id: int = Field(..., gt=0)


class SyncFromFeishuIn(BaseModel):
    """Either pull a whole department or a specific list of open_ids."""

    department_id: str | None = Field(
        default=None, description="Feishu department open_id; '0' = root"
    )
    open_ids: list[str] | None = Field(default=None, description="Targeted user list")


class SyncReportOut(BaseModel):
    new_count: int
    updated_count: int
    revived_count: int
    skipped_inactive: int
    errors: list[dict[str, Any]]
    new_user_ids: list[int]
    touched_user_ids: list[int]
    total_processed: int


class FeishuDeptOut(BaseModel):
    """One department node in the org-tree browser."""

    open_department_id: str
    department_id: str
    name: str
    parent_department_id: str
    member_count: int


class FeishuContactUserOut(BaseModel):
    """One user under a department in the org-tree browser, annotated with
    sync status so the UI can grey-out already-synced rows."""

    open_id: str
    name: str
    employee_no: str
    email: str
    mobile: str
    is_activated: bool
    already_synced: bool  # already exists in local users (any active state)
    local_user_id: int | None  # if synced, this is the local users.id


# ---- Feishu org-tree browse (read-only) -------------------------------


@router.get("/feishu/departments/tree", response_model=list[FeishuDeptOut])
def list_feishu_departments_tree(
    _admin: AuthedUser = Depends(require_admin),
) -> list[FeishuDeptOut]:
    """Return all departments under root as a flat list with parent_department_id.

    Uses fetch_child=True so Feishu returns the full hierarchy in one call.
    Frontend builds the tree from parent_department_id relationships.
    """
    settings = get_settings()
    if not (settings.feishu_app_id and settings.feishu_app_secret):
        raise HTTPException(status_code=503, detail="feishu credentials not configured")

    client = FeishuClient(FeishuConfig.from_settings(settings))
    try:
        try:
            depts = client.list_child_departments("0", fetch_child=True)
        except FeishuError as e:
            raise HTTPException(status_code=502, detail=f"feishu API error: {e}") from e
    finally:
        client.close()
    return [
        FeishuDeptOut(
            open_department_id=d.open_department_id,
            department_id=d.department_id,
            name=d.name,
            parent_department_id=d.parent_department_id,
            member_count=d.member_count,
        )
        for d in depts
    ]


@router.get("/feishu/departments", response_model=list[FeishuDeptOut])
def list_feishu_departments(
    parent_id: str = "0",
    _admin: AuthedUser = Depends(require_admin),
) -> list[FeishuDeptOut]:
    """List immediate child departments of `parent_id` (default: root='0')."""
    settings = get_settings()
    if not (settings.feishu_app_id and settings.feishu_app_secret):
        raise HTTPException(status_code=503, detail="feishu credentials not configured")

    client = FeishuClient(FeishuConfig.from_settings(settings))
    try:
        try:
            depts = client.list_child_departments(parent_id)
        except FeishuError as e:
            raise HTTPException(status_code=502, detail=f"feishu API error: {e}") from e
    finally:
        client.close()
    return [
        FeishuDeptOut(
            open_department_id=d.open_department_id,
            department_id=d.department_id,
            name=d.name,
            parent_department_id=d.parent_department_id,
            member_count=d.member_count,
        )
        for d in depts
    ]


@router.get(
    "/feishu/departments/{department_id}/users",
    response_model=list[FeishuContactUserOut],
)
def list_feishu_department_users(
    department_id: str,
    _admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[FeishuContactUserOut]:
    """List users directly under a Feishu department, annotated with sync status."""
    settings = get_settings()
    if not (settings.feishu_app_id and settings.feishu_app_secret):
        raise HTTPException(status_code=503, detail="feishu credentials not configured")

    client = FeishuClient(FeishuConfig.from_settings(settings))
    try:
        try:
            users = client.list_users_by_department(department_id)
        except FeishuError as e:
            raise HTTPException(status_code=502, detail=f"feishu API error: {e}") from e
    finally:
        client.close()

    user_repo = UserRepository(db)
    out: list[FeishuContactUserOut] = []
    for u in users:
        local = user_repo.get_by_feishu_uid(u.open_id, include_deleted=True)
        out.append(
            FeishuContactUserOut(
                open_id=u.open_id,
                name=u.name,
                employee_no=u.employee_no,
                email=u.email,
                mobile=u.mobile,
                is_activated=u.is_activated,
                already_synced=local is not None,
                local_user_id=local.id if local else None,
            )
        )
    return out


@router.post("/sync-from-feishu", response_model=SyncReportOut)
def sync_from_feishu(
    body: SyncFromFeishuIn,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> SyncReportOut:
    """Bulk-pull users from Feishu contact API and upsert into local users."""
    if (body.department_id is None) == (body.open_ids is None):
        raise HTTPException(
            status_code=400,
            detail="provide exactly one of department_id or open_ids",
        )
    if body.open_ids is not None and len(body.open_ids) == 0:
        raise HTTPException(status_code=400, detail="open_ids must be non-empty")

    settings = get_settings()
    if not (settings.feishu_app_id and settings.feishu_app_secret):
        raise HTTPException(status_code=503, detail="feishu credentials not configured")

    client = FeishuClient(FeishuConfig.from_settings(settings))
    try:
        svc = FeishuUserSyncService(db, client=client)
        try:
            if body.department_id is not None:
                report = svc.sync_from_department(body.department_id)
            else:
                assert body.open_ids is not None
                report = svc.sync_from_open_ids(body.open_ids)
        except FeishuError as e:
            db.rollback()
            raise HTTPException(status_code=502, detail=f"feishu API error: {e}") from e
    finally:
        client.close()

    db.commit()
    logger.info(
        "admin_user_feishu_sync_done",
        by=admin.user_id,
        new=report.new_count,
        updated=report.updated_count,
        revived=report.revived_count,
        errors=len(report.errors),
    )
    return SyncReportOut(
        new_count=report.new_count,
        updated_count=report.updated_count,
        revived_count=report.revived_count,
        skipped_inactive=report.skipped_inactive,
        errors=report.errors,
        new_user_ids=report.new_user_ids,
        touched_user_ids=report.touched_user_ids,
        total_processed=report.total_processed,
    )


class LinearSyncReportOut(BaseModel):
    matched_count: int
    cleared_count: int
    skipped_no_email: int
    unmatched_local: int
    unmatched_linear: list[str]
    errors: list[dict[str, Any]]
    touched_user_ids: list[int]


@router.post("/sync-from-linear", response_model=LinearSyncReportOut)
def sync_from_linear(
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> LinearSyncReportOut:
    """Match local users to Linear members by @email and populate
    linear_user_id / linear_team_id (for per-assignee Linear push routing)."""
    settings = get_settings()
    if not settings.linear_api_key:
        raise HTTPException(status_code=503, detail="LINEAR_API_KEY not configured")
    try:
        report = sync_linear_users(db)
    except LinearError as e:
        db.rollback()
        raise HTTPException(status_code=502, detail=f"linear API error: {e}") from e
    logger.info(
        "admin_user_linear_sync_done",
        by=admin.user_id,
        matched=report.matched_count,
        cleared=report.cleared_count,
        unmatched_local=report.unmatched_local,
        errors=len(report.errors),
    )
    return LinearSyncReportOut(
        matched_count=report.matched_count,
        cleared_count=report.cleared_count,
        skipped_no_email=report.skipped_no_email,
        unmatched_local=report.unmatched_local,
        unmatched_linear=report.unmatched_linear,
        errors=report.errors,
        touched_user_ids=report.touched_user_ids,
    )


# ---- list / detail ---------------------------------------------------


@router.get("", response_model=list[UserOut])
def list_users(
    include_inactive: bool = False,
    _user: AuthedUser = Depends(require_supervisor),
    db: Session = Depends(get_session),
) -> list[UserOut]:
    repo = UserRepository(db)
    rows = repo.list_all() if include_inactive else repo.list_active()
    return [UserOut.model_validate(r) for r in rows]


@router.get("/{user_id}", response_model=UserDetailOut)
def get_user_detail(
    user_id: int,
    _admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> UserDetailOut:
    repo = UserRepository(db)
    user = repo.get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    sup_row = UserSupervisorRepository(db).get(user_id)
    partners = UserPartnerRepository(db).list_partners(user_id)
    scope_repo = AssignmentScopeAdminRepository(db)
    module_scopes = scope_repo.list_modules(user_id=user_id)
    feature_scopes = scope_repo.list_features(user_id=user_id)

    return UserDetailOut(
        user=UserOut.model_validate(user),
        supervisor=SupervisorOut.model_validate(sup_row) if sup_row else None,
        module_scopes=[
            ScopeRowOut(
                id=r.id,
                user_id=r.user_id,
                product_line_code=r.product_line_code,
                module=r.module,
            )
            for r in module_scopes
        ],
        feature_scopes=[
            ScopeRowOut(id=r.id, user_id=r.user_id, feature=r.feature) for r in feature_scopes
        ],
        partners=[PartnerRowOut(id=p.id, name=p.name, role=p.role) for p in partners],
    )


# ---- update / delete -------------------------------------------------


@router.patch("/{user_id}", response_model=UserOut)
def patch_user(
    user_id: int,
    body: UserPatch,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> UserOut:
    # Self-demote guard: an admin cannot drop their own role to non-admin
    # (prevents the last admin accidentally locking themselves out).
    if user_id == admin.user_id and body.role and body.role != "admin":
        raise HTTPException(
            status_code=400,
            detail="cannot demote yourself; ask another admin to do it",
        )

    repo = UserRepository(db)
    patch_dict: dict[str, Any] = body.model_dump(exclude_unset=True)
    user = repo.update(user_id, patch=patch_dict)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    db.commit()
    db.refresh(user)
    logger.info(
        "admin_user_updated",
        target_user_id=user_id,
        by=admin.user_id,
        fields=list(patch_dict.keys()),
    )
    return UserOut.model_validate(user)


@router.delete("/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> None:
    if user_id == admin.user_id:
        raise HTTPException(status_code=400, detail="cannot soft-delete yourself")
    repo = UserRepository(db)
    deleted = repo.soft_delete(user_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail="user not found")
    db.commit()
    logger.info("admin_user_soft_deleted", target_user_id=user_id, by=admin.user_id)


@router.post("/{user_id}/revive", response_model=UserOut)
def revive_user(
    user_id: int,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> UserOut:
    repo = UserRepository(db)
    user = repo.revive(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    db.commit()
    db.refresh(user)
    logger.info("admin_user_revived", target_user_id=user_id, by=admin.user_id)
    return UserOut.model_validate(user)


# ---- supervisor ------------------------------------------------------


@router.post("/{user_id}/supervisor", response_model=SupervisorOut)
def set_supervisor(
    user_id: int,
    body: SupervisorIn,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> SupervisorOut:
    user_repo = UserRepository(db)
    if user_repo.get(user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")
    if user_repo.get(body.supervisor_id) is None:
        raise HTTPException(status_code=400, detail="supervisor_id refers to unknown user")
    if body.deputy_supervisor_id and user_repo.get(body.deputy_supervisor_id) is None:
        raise HTTPException(status_code=400, detail="deputy_supervisor_id refers to unknown user")
    try:
        row = UserSupervisorRepository(db).upsert(
            user_id=user_id,
            supervisor_id=body.supervisor_id,
            deputy_supervisor_id=body.deputy_supervisor_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    db.commit()
    db.refresh(row)
    logger.info(
        "admin_user_supervisor_set",
        target_user_id=user_id,
        supervisor_id=body.supervisor_id,
        deputy_supervisor_id=body.deputy_supervisor_id,
        by=admin.user_id,
    )
    return SupervisorOut.model_validate(row)


@router.delete("/{user_id}/supervisor", status_code=204)
def clear_supervisor(
    user_id: int,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> None:
    if not UserSupervisorRepository(db).clear(user_id):
        raise HTTPException(status_code=404, detail="no supervisor relationship to clear")
    db.commit()
    logger.info("admin_user_supervisor_cleared", target_user_id=user_id, by=admin.user_id)


# ---- partners --------------------------------------------------------


@router.post("/{user_id}/partners", response_model=list[PartnerRowOut], status_code=201)
def add_partner(
    user_id: int,
    body: PartnerIn,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[PartnerRowOut]:
    user_repo = UserRepository(db)
    if user_repo.get(user_id) is None:
        raise HTTPException(status_code=404, detail="user not found")
    if user_repo.get(body.partner_id) is None:
        raise HTTPException(status_code=400, detail="partner_id refers to unknown user")
    repo = UserPartnerRepository(db)
    try:
        added = repo.add_pair(user_id=user_id, partner_id=body.partner_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail="partner pair already exists") from e
    if not added:
        # Idempotent — return current list with 200 instead of 201
        # Actually keep 201 for consistency; the row exists so caller can ignore
        pass
    db.commit()
    partners = repo.list_partners(user_id)
    logger.info(
        "admin_user_partner_added",
        target_user_id=user_id,
        partner_id=body.partner_id,
        by=admin.user_id,
        new_pair=added,
    )
    return [PartnerRowOut(id=p.id, name=p.name, role=p.role) for p in partners]


@router.delete("/{user_id}/partners/{partner_id}", status_code=204)
def remove_partner(
    user_id: int,
    partner_id: int,
    admin: AuthedUser = Depends(require_admin),
    db: Session = Depends(get_session),
) -> None:
    repo = UserPartnerRepository(db)
    if not repo.remove_pair(user_id=user_id, partner_id=partner_id):
        raise HTTPException(status_code=404, detail="partner pair not found")
    db.commit()
    logger.info(
        "admin_user_partner_removed",
        target_user_id=user_id,
        partner_id=partner_id,
        by=admin.user_id,
    )
