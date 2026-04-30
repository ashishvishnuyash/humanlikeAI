"""
Super Admin Router — Platform-Wide Control
==========================================
  Super admin has unrestricted access across ALL companies, employers, and employees.

  Identity & Auth
  ───────────────
  GET  /api/admin/me                          → Super admin's own profile
  POST /api/admin/change-password             → Change super admin password

  Employers (cross-company)
  ─────────────────────────
  GET  /api/admin/employers                   → List all employers on the platform
  GET  /api/admin/employers/{uid}             → Get single employer profile
  PATCH /api/admin/employers/{uid}            → Update any employer profile
  POST /api/admin/employers/{uid}/deactivate  → Disable employer
  POST /api/admin/employers/{uid}/reactivate  → Re-enable employer
  DELETE /api/admin/employers/{uid}           → Hard-delete employer + company

  Employees (cross-company)
  ─────────────────────────
  GET  /api/admin/employees                   → List all employees (optionally filter by company)
  GET  /api/admin/employees/{uid}             → Get single employee
  PATCH /api/admin/employees/{uid}            → Update any employee
  DELETE /api/admin/employees/{uid}           → Hard-delete employee

  Companies
  ─────────
  GET  /api/admin/companies                   → List all companies
  GET  /api/admin/companies/{company_id}      → Get company document
  PATCH /api/admin/companies/{company_id}     → Update company

  Platform Stats
  ──────────────
  GET  /api/admin/stats                       → Platform-wide KPIs

  Password Reset (any user)
  ─────────────────────────
  POST /api/admin/users/{uid}/reset-password  → Force-set a new password for any user
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from auth.password import hash_password, verify_password
from db.models import Company, User
from db.session import get_session
from routers.auth import get_super_admin_user, RegisterRequest, RegisterResponse
from utils.audit import log_audit

router = APIRouter(prefix="/admin", tags=["Super Admin"])


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _ts_to_iso(ts) -> Optional[str]:
    if ts is None:
        return None
    if hasattr(ts, "timestamp"):
        return datetime.fromtimestamp(ts.timestamp(), tz=timezone.utc).isoformat()
    if isinstance(ts, datetime):
        return ts.isoformat()
    return str(ts)


def _user_to_profile(user: User) -> dict:
    """Serialise a User ORM instance into the _safe_profile response shape."""
    p = user.profile or {}
    company_id_str = str(user.company_id) if user.company_id else None
    return {
        "uid":            user.id,
        "email":          user.email,
        "firstName":      p.get("first_name", ""),
        "lastName":       p.get("last_name", ""),
        "displayName":    p.get("display_name", ""),
        "role":           user.role,
        "companyId":      company_id_str,
        "companyName":    p.get("company_name"),
        "department":     user.department,
        "position":       p.get("position"),
        "phone":          p.get("phone"),
        "jobTitle":       p.get("job_title"),
        "hierarchyLevel": p.get("hierarchy_level", 0),
        "isActive":       user.is_active,
        "createdAt":      _ts_to_iso(user.created_at),
        "updatedAt":      _ts_to_iso(user.updated_at),
        "createdBy":      p.get("created_by"),
    }


# ─── Schemas ─────────────────────────────────────────────────────────────────

class AdminMeResponse(BaseModel):
    uid: str
    email: str
    role: str
    displayName: str
    isActive: bool
    createdAt: Optional[str]


class PlatformStatsResponse(BaseModel):
    totalEmployers:  int
    totalEmployees:  int
    totalCompanies:  int
    totalUsers:      int
    activeUsers:     int
    inactiveUsers:   int
    roleBreakdown:   Dict[str, int]
    recentJoins:     int          # all users added in last 30 days
    computedAt:      str


class UpdateUserRequest(BaseModel):
    firstName:      Optional[str] = None
    lastName:       Optional[str] = None
    phone:          Optional[str] = None
    department:     Optional[str] = None
    position:       Optional[str] = None
    jobTitle:       Optional[str] = None
    hierarchyLevel: Optional[int] = None
    isActive:       Optional[bool] = None
    role:           Optional[str] = None


class UpdateCompanyRequest(BaseModel):
    name:        Optional[str] = None
    industry:    Optional[str] = None
    size:        Optional[str] = None
    website:     Optional[str] = None
    address:     Optional[str] = None
    phone:       Optional[str] = None
    description: Optional[str] = None
    logoUrl:     Optional[str] = None


class ResetPasswordRequest(BaseModel):
    new_password: str   # min 8 chars


class AdminChangePasswordRequest(BaseModel):
    current_password: str
    new_password:     str


class MutationResponse(BaseModel):
    success: bool
    message: str
    updatedFields: Optional[List[str]] = None


# ─── GET /admin/me ────────────────────────────────────────────────────────────

@router.get("/me", response_model=AdminMeResponse, summary="Super Admin Profile")
async def admin_me(admin: dict = Depends(get_super_admin_user)):
    return AdminMeResponse(
        uid=admin.get("id", ""),
        email=admin.get("email", ""),
        role=admin.get("role", "super_admin"),
        displayName=admin.get("display_name", "Diltak Admin"),
        isActive=admin.get("is_active", True),
        createdAt=_ts_to_iso(admin.get("created_at")),
    )


# ─── Platform Stats ───────────────────────────────────────────────────────────

@router.get(
    "/stats",
    response_model=PlatformStatsResponse,
    summary="Platform-Wide Stats",
)
async def platform_stats(
    _: dict = Depends(get_super_admin_user),
    db: Session = Depends(get_session),
):
    try:
        all_users: List[User] = db.query(User).all()
    except Exception as e:
        raise HTTPException(500, f"Query failed: {e}")

    now = datetime.now(timezone.utc)
    thirty_ago = now - timedelta(days=30)

    total_employers = total_employees = active = inactive = recent = 0
    roles: Dict[str, int] = {}

    for user in all_users:
        role = user.role or "unknown"
        roles[role] = roles.get(role, 0) + 1

        if role == "employer":
            total_employers += 1
        elif role != "super_admin":
            total_employees += 1

        if user.is_active:
            active += 1
        else:
            inactive += 1

        created = user.created_at
        if created is not None:
            # Ensure timezone-aware for comparison
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created >= thirty_ago:
                recent += 1

    try:
        total_companies = db.query(Company).count()
    except Exception:
        total_companies = total_employers  # fallback

    return PlatformStatsResponse(
        totalEmployers=total_employers,
        totalEmployees=total_employees,
        totalCompanies=total_companies,
        totalUsers=len(all_users),
        activeUsers=active,
        inactiveUsers=inactive,
        roleBreakdown=roles,
        recentJoins=recent,
        computedAt=now.isoformat(),
    )


# ─── Employers: Create ───────────────────────────────────────────────────────

@router.post(
    "/employers",
    status_code=201,
    response_model=RegisterResponse,
    summary="Create Employer (Admin Only)",
    description=(
        "Super admin creates a new employer account + company. "
        "The employer can then log in and manage their own team."
    ),
)
async def admin_create_employer(
    req: RegisterRequest,
    _: dict = Depends(get_super_admin_user),
    db: Session = Depends(get_session),
):
    from routers.auth import register
    return register(req, db)


# ─── Employers: List ─────────────────────────────────────────────────────────

@router.get(
    "/employers",
    summary="List All Employers",
    description="Returns all employer accounts across the entire platform.",
)
async def list_employers(
    include_inactive: bool = Query(False),
    search: Optional[str] = Query(None, description="Search by name, email, or company"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    _: dict = Depends(get_super_admin_user),
    db: Session = Depends(get_session),
):
    try:
        query = db.query(User).filter(User.role == "employer")
        if not include_inactive:
            query = query.filter(User.is_active.is_(True))
        employers: List[User] = query.all()
    except Exception as e:
        raise HTTPException(500, f"Query failed: {e}")

    result = []
    for user in employers:
        profile = _user_to_profile(user)
        if search:
            term = search.lower().strip()
            searchable = " ".join([
                f"{profile.get('firstName', '')} {profile.get('lastName', '')}".lower(),
                profile.get("email", "").lower(),
                (profile.get("companyName") or "").lower(),
            ])
            if term not in searchable:
                continue
        result.append(profile)

    total = len(result)
    total_pages = max(1, (total + limit - 1) // limit)
    offset = (page - 1) * limit

    return {
        "employers": result[offset: offset + limit],
        "total": total,
        "page": page,
        "limit": limit,
        "totalPages": total_pages,
        "hasNext": offset + limit < total,
        "hasPrev": page > 1,
    }


# ─── Employers: Get ───────────────────────────────────────────────────────────

@router.get("/employers/{uid}", summary="Get Employer")
async def get_employer(
    uid: str,
    _: dict = Depends(get_super_admin_user),
    db: Session = Depends(get_session),
):
    user = db.query(User).filter(User.id == uid).one_or_none()
    if user is None:
        raise HTTPException(404, "Employer not found.")
    if user.role != "employer":
        raise HTTPException(400, "User is not an employer.")
    return _user_to_profile(user)


# ─── Employers: Update ────────────────────────────────────────────────────────

@router.patch(
    "/employers/{uid}",
    response_model=MutationResponse,
    summary="Update Employer",
)
async def update_employer(
    uid: str,
    req: UpdateUserRequest,
    _: dict = Depends(get_super_admin_user),
    db: Session = Depends(get_session),
):
    return await _update_user(uid, req, expected_role="employer", db=db)


# ─── Employers: Deactivate / Reactivate ───────────────────────────────────────

@router.post("/employers/{uid}/deactivate", response_model=MutationResponse, summary="Deactivate Employer")
async def deactivate_employer(
    uid: str,
    admin: dict = Depends(get_super_admin_user),
    db: Session = Depends(get_session),
):
    result = await _set_user_active(uid, active=False, role_check="employer", db=db)
    target = db.query(User).filter(User.id == uid).one_or_none()
    log_audit(
        actor_uid=admin.get("id", ""),
        actor_role="super_admin",
        action="employer.deactivate",
        company_id=str(target.company_id) if target and target.company_id else "",
        target_uid=uid,
        target_type="employer",
    )
    return result


@router.post("/employers/{uid}/reactivate", response_model=MutationResponse, summary="Reactivate Employer")
async def reactivate_employer(
    uid: str,
    admin: dict = Depends(get_super_admin_user),
    db: Session = Depends(get_session),
):
    result = await _set_user_active(uid, active=True, role_check="employer", db=db)
    target = db.query(User).filter(User.id == uid).one_or_none()
    log_audit(
        actor_uid=admin.get("id", ""),
        actor_role="super_admin",
        action="employer.reactivate",
        company_id=str(target.company_id) if target and target.company_id else "",
        target_uid=uid,
        target_type="employer",
    )
    return result


# ─── Employers: Hard Delete ───────────────────────────────────────────────────

@router.delete(
    "/employers/{uid}",
    response_model=MutationResponse,
    summary="Hard-Delete Employer",
    description=(
        "Irreversible. Deletes the employer's user row and company row. "
        "Employees are NOT deleted — their company_id FK is set NULL by the DB cascade."
    ),
)
async def delete_employer(
    uid: str,
    admin: dict = Depends(get_super_admin_user),
    db: Session = Depends(get_session),
):
    user = db.query(User).filter(User.id == uid).one_or_none()
    if user is None:
        raise HTTPException(404, "Employer not found.")
    if user.role != "employer":
        raise HTTPException(400, "User is not an employer.")

    company_id = user.company_id

    # 1. Delete user row (FK ON DELETE SET NULL handles employees' company_id)
    db.query(User).filter(User.id == uid).delete()

    # 2. Delete company row
    if company_id is not None:
        db.query(Company).filter(Company.id == company_id).delete()

    db.commit()

    log_audit(
        actor_uid=admin.get("id", ""),
        actor_role="super_admin",
        action="employer.delete",
        company_id=str(company_id) if company_id is not None else "",
        target_uid=uid,
        target_type="employer",
    )

    return MutationResponse(success=True, message="Employer deleted.")


# ─── Employees: List (cross-company) ─────────────────────────────────────────

@router.get(
    "/employees",
    summary="List All Employees",
    description="List all non-employer, non-admin users. Optionally filter by company.",
)
async def list_all_employees(
    company_id:       Optional[str]  = Query(None),
    role_filter:      Optional[str]  = Query(None, alias="role"),
    include_inactive: bool           = Query(False),
    search:           Optional[str]  = Query(None, description="Search by name, email, or company"),
    page:             int            = Query(1, ge=1),
    limit:            int            = Query(20, ge=1, le=100),
    _: dict = Depends(get_super_admin_user),
    db: Session = Depends(get_session),
):
    try:
        query = db.query(User).filter(User.role.notin_(["employer", "super_admin"]))

        if company_id:
            try:
                cid_uuid = uuid.UUID(company_id)
            except ValueError:
                raise HTTPException(400, "Invalid company_id format.")
            query = query.filter(User.company_id == cid_uuid)

        if not include_inactive:
            query = query.filter(User.is_active.is_(True))

        if role_filter:
            query = query.filter(User.role == role_filter)

        users: List[User] = query.all()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Query failed: {e}")

    result = []
    for user in users:
        profile = _user_to_profile(user)
        if search:
            term = search.lower().strip()
            searchable = " ".join([
                f"{profile.get('firstName', '')} {profile.get('lastName', '')}".lower(),
                profile.get("email", "").lower(),
                (profile.get("companyName") or "").lower(),
                (profile.get("department") or "").lower(),
            ])
            if term not in searchable:
                continue
        result.append(profile)

    total = len(result)
    total_pages = max(1, (total + limit - 1) // limit)
    offset = (page - 1) * limit

    return {
        "employees": result[offset: offset + limit],
        "total": total,
        "page": page,
        "limit": limit,
        "totalPages": total_pages,
        "hasNext": offset + limit < total,
        "hasPrev": page > 1,
    }


# ─── Employees: Get ───────────────────────────────────────────────────────────

@router.get("/employees/{uid}", summary="Get Employee (any company)")
async def admin_get_employee(
    uid: str,
    _: dict = Depends(get_super_admin_user),
    db: Session = Depends(get_session),
):
    user = db.query(User).filter(User.id == uid).one_or_none()
    if user is None:
        raise HTTPException(404, "User not found.")
    if user.role in ("employer", "super_admin"):
        raise HTTPException(400, "Use /employers endpoint for employer/admin profiles.")
    return _user_to_profile(user)


# ─── Employees: Update ────────────────────────────────────────────────────────

@router.patch(
    "/employees/{uid}",
    response_model=MutationResponse,
    summary="Update Employee (any company)",
)
async def admin_update_employee(
    uid: str,
    req: UpdateUserRequest,
    _: dict = Depends(get_super_admin_user),
    db: Session = Depends(get_session),
):
    return await _update_user(uid, req, expected_role=None, db=db)


# ─── Employees: Hard Delete ───────────────────────────────────────────────────

@router.delete(
    "/employees/{uid}",
    response_model=MutationResponse,
    summary="Hard-Delete Employee",
)
async def admin_delete_employee(
    uid: str,
    admin: dict = Depends(get_super_admin_user),
    db: Session = Depends(get_session),
):
    user = db.query(User).filter(User.id == uid).one_or_none()
    if user is None:
        raise HTTPException(404, "User not found.")

    if user.role in ("employer", "super_admin"):
        raise HTTPException(400, "Use the employer delete endpoint for employer accounts.")

    company_id = user.company_id
    target_role = user.role

    # Delete user row. FK ON DELETE SET NULL handles manager_id references from
    # direct reports automatically at the DB level.
    db.query(User).filter(User.id == uid).delete()

    # Decrement company employee_count
    if company_id is not None:
        company = db.query(Company).filter(Company.id == company_id).one_or_none()
        if company is not None and company.employee_count > 0:
            company.employee_count -= 1

    db.commit()

    log_audit(
        actor_uid=admin.get("id", ""),
        actor_role="super_admin",
        action="user.delete",
        company_id=str(company_id) if company_id is not None else "",
        target_uid=uid,
        target_type="user",
        metadata={"role": target_role},
    )

    return MutationResponse(success=True, message="Employee deleted.")


# ─── Companies: List ─────────────────────────────────────────────────────────

@router.get("/companies", summary="List All Companies")
async def list_all_companies(
    search: Optional[str] = Query(None, description="Search by company name or industry"),
    page:   int           = Query(1, ge=1),
    limit:  int           = Query(20, ge=1, le=100),
    _: dict = Depends(get_super_admin_user),
    db: Session = Depends(get_session),
):
    try:
        all_companies: List[Company] = db.query(Company).all()
    except Exception as e:
        raise HTTPException(500, f"Query failed: {e}")

    companies = []
    for company in all_companies:
        s = company.settings or {}
        if search:
            term = search.lower().strip()
            searchable = " ".join([
                company.name.lower(),
                (s.get("industry") or "").lower(),
            ])
            if term not in searchable:
                continue
        companies.append({
            "id":            str(company.id),
            "name":          company.name,
            "industry":      s.get("industry"),
            "size":          s.get("size"),
            "ownerId":       company.owner_id,
            "employeeCount": company.employee_count,
            "website":       s.get("website"),
            "description":   s.get("description"),
            "createdAt":     _ts_to_iso(company.created_at),
            "updatedAt":     _ts_to_iso(company.updated_at),
        })

    total = len(companies)
    total_pages = max(1, (total + limit - 1) // limit)
    offset = (page - 1) * limit

    return {
        "companies": companies[offset: offset + limit],
        "total": total,
        "page": page,
        "limit": limit,
        "totalPages": total_pages,
        "hasNext": offset + limit < total,
        "hasPrev": page > 1,
    }


# ─── Companies: Get ───────────────────────────────────────────────────────────

@router.get("/companies/{company_id}", summary="Get Company")
async def admin_get_company(
    company_id: str,
    _: dict = Depends(get_super_admin_user),
    db: Session = Depends(get_session),
):
    try:
        cid_uuid = uuid.UUID(company_id)
    except ValueError:
        raise HTTPException(400, "Invalid company_id format.")

    company = db.query(Company).filter(Company.id == cid_uuid).one_or_none()
    if company is None:
        raise HTTPException(404, "Company not found.")

    s = company.settings or {}
    return {
        "id":            str(company.id),
        "name":          company.name,
        "industry":      s.get("industry"),
        "size":          s.get("size"),
        "ownerId":       company.owner_id,
        "employeeCount": company.employee_count,
        "website":       s.get("website"),
        "address":       s.get("address"),
        "phone":         s.get("phone"),
        "description":   s.get("description"),
        "logoUrl":       s.get("logo_url"),
        "createdAt":     _ts_to_iso(company.created_at),
        "updatedAt":     _ts_to_iso(company.updated_at),
    }


# ─── Companies: Update ───────────────────────────────────────────────────────

@router.patch(
    "/companies/{company_id}",
    response_model=MutationResponse,
    summary="Update Company",
)
async def admin_update_company(
    company_id: str,
    req: UpdateCompanyRequest,
    _: dict = Depends(get_super_admin_user),
    db: Session = Depends(get_session),
):
    try:
        cid_uuid = uuid.UUID(company_id)
    except ValueError:
        raise HTTPException(400, "Invalid company_id format.")

    company = db.query(Company).filter(Company.id == cid_uuid).one_or_none()
    if company is None:
        raise HTTPException(404, "Company not found.")

    updated_fields: List[str] = []

    # Top-level column: name
    if req.name is not None:
        company.name = req.name
        updated_fields.append("name")

    # settings JSONB fields
    settings_map = {
        "industry":    "industry",
        "size":        "size",
        "website":     "website",
        "address":     "address",
        "phone":       "phone",
        "description": "description",
        "logoUrl":     "logo_url",
    }
    new_settings = dict(company.settings or {})
    for req_field, settings_key in settings_map.items():
        val = getattr(req, req_field, None)
        if val is not None:
            new_settings[settings_key] = val
            updated_fields.append(req_field)

    if not updated_fields:
        raise HTTPException(400, "No fields to update.")

    company.settings = new_settings
    db.commit()

    # Sync name change to all employees' profile JSONB in this company
    if req.name is not None:
        try:
            employees = db.query(User).filter(User.company_id == cid_uuid).all()
            for emp in employees:
                p = dict(emp.profile or {})
                p["company_name"] = req.name
                emp.profile = p
            db.commit()
        except Exception as e:
            print(f"[admin] company name sync error: {e}")

    log_audit(
        actor_uid=admin.get("id", ""),
        actor_role="super_admin",
        action="company.update",
        company_id=company_id,
        db=db,
        target_uid=None,
        target_type="company",
        metadata={"updated_fields": updated_fields},
    )

    return MutationResponse(
        success=True,
        message="Company updated successfully.",
        updatedFields=updated_fields,
    )


# ─── Password Reset (any user) ────────────────────────────────────────────────

@router.post(
    "/users/{uid}/reset-password",
    response_model=MutationResponse,
    summary="Force Reset Any User's Password",
    description=(
        "Super admin can force-set a new password for any user on the platform. "
        "New password must be at least 8 characters."
    ),
)
async def admin_reset_password(
    uid: str,
    req: ResetPasswordRequest,
    _: dict = Depends(get_super_admin_user),
    db: Session = Depends(get_session),
):
    if len(req.new_password) < 8:
        raise HTTPException(400, "New password must be at least 8 characters.")

    user = db.query(User).filter(User.id == uid).one_or_none()
    if user is None:
        raise HTTPException(404, "User not found.")

    user.password_hash = hash_password(req.new_password)
    db.commit()

    db = get_db()
    log_audit(
        actor_uid=admin.get("id", ""),
        actor_role="super_admin",
        action="user.password_reset",
        company_id="",
        db=db,
        target_uid=uid,
        target_type="user",
    )

    return MutationResponse(
        success=True,
        message=f"Password reset successfully for user {uid}.",
    )


# ─── Admin Change Password ────────────────────────────────────────────────────

@router.post(
    "/change-password",
    response_model=MutationResponse,
    summary="Change Super Admin Password",
)
async def admin_change_password(
    req: AdminChangePasswordRequest,
    admin: dict = Depends(get_super_admin_user),
    db: Session = Depends(get_session),
):
    if len(req.new_password) < 8:
        raise HTTPException(400, "New password must be at least 8 characters.")
    if req.current_password == req.new_password:
        raise HTTPException(400, "New password must differ from current.")

    uid = admin.get("id")
    user = db.query(User).filter(User.id == uid).one_or_none()
    if user is None:
        raise HTTPException(404, "User not found.")

    if user.password_hash is None or not verify_password(req.current_password, user.password_hash):
        raise HTTPException(401, "Current password is incorrect.")

    user.password_hash = hash_password(req.new_password)
    db.commit()

    return MutationResponse(
        success=True,
        message="Super admin password changed. Please log in again.",
    )


# ─── Internal helpers ─────────────────────────────────────────────────────────

VALID_ROLES = {"employee", "manager", "hr", "employer", "super_admin"}

async def _update_user(
    uid: str,
    req: UpdateUserRequest,
    expected_role: Optional[str],
    db: Session,
) -> MutationResponse:
    user = db.query(User).filter(User.id == uid).one_or_none()
    if user is None:
        raise HTTPException(404, "User not found.")

    if expected_role and user.role != expected_role:
        raise HTTPException(400, f"User role mismatch. Expected '{expected_role}'.")
    if user.role == "super_admin":
        raise HTTPException(403, "Cannot modify super admin profile via this endpoint.")

    updated_fields: List[str] = []

    # Top-level column: role, department, is_active
    if req.role is not None:
        if req.role not in VALID_ROLES:
            raise HTTPException(400, f"Invalid role '{req.role}'.")
        user.role = req.role
        updated_fields.append("role")

    if req.department is not None:
        user.department = req.department
        updated_fields.append("department")

    if req.isActive is not None:
        user.is_active = req.isActive
        updated_fields.append("isActive")

    # profile JSONB fields
    profile_map = {
        "firstName":      "first_name",
        "lastName":       "last_name",
        "phone":          "phone",
        "position":       "position",
        "jobTitle":       "job_title",
        "hierarchyLevel": "hierarchy_level",
    }
    new_profile = dict(user.profile or {})
    for req_field, profile_key in profile_map.items():
        val = getattr(req, req_field, None)
        if val is not None:
            new_profile[profile_key] = val
            updated_fields.append(req_field)

    if not updated_fields:
        raise HTTPException(400, "No valid fields to update.")

    # Sync display_name when name fields change
    if req.firstName is not None or req.lastName is not None:
        fn = new_profile.get("first_name", "")
        ln = new_profile.get("last_name", "")
        new_profile["display_name"] = f"{fn} {ln}".strip()

    user.profile = new_profile
    db.commit()

    return MutationResponse(
        success=True,
        message="User updated successfully.",
        updatedFields=updated_fields,
    )


async def _set_user_active(uid: str, active: bool, role_check: str, db: Session) -> MutationResponse:
    user = db.query(User).filter(User.id == uid).one_or_none()
    if user is None:
        raise HTTPException(404, "User not found.")

    if user.role != role_check:
        raise HTTPException(400, f"User is not a {role_check}.")

    user.is_active = active
    db.commit()

    if actor:
        audit_action = f"employer.{'reactivate' if active else 'deactivate'}"
        log_audit(
            actor_uid=actor.get("id", ""),
            actor_role="super_admin",
            action=audit_action,
            company_id=d.get("company_id", ""),
            db=db,
            target_uid=uid,
            target_type="employer",
        )

    action = "reactivated" if active else "deactivated"
    return MutationResponse(success=True, message=f"User {action} successfully.")
