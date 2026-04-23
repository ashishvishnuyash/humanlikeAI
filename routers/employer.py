"""
Employer CRUD — Full profile & company management
==================================================
  GET    /api/employer/profile          → Get own employer profile + company
  PATCH  /api/employer/profile          → Update employer profile fields
  PATCH  /api/employer/company          → Update company details
  GET    /api/employer/company          → Get company document
  GET    /api/employer/company/stats    → Company summary stats (headcount, roles, depts)
  DELETE /api/employer/account          → Permanently delete employer account + company
                                          ⚠  Requires confirmation_phrase in body
  POST   /api/employer/change-password  → Change own password via bcrypt verify + Postgres update

All endpoints require JWT with role == employer (only the account owner).
HR users can read profile/company but cannot mutate or delete.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from auth.password import hash_password, verify_password
from db.models import Company, User
from db.session import get_session
from routers.auth import get_current_user, get_employer_user

router = APIRouter(prefix="/employer", tags=["Employer CRUD"])


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _ts_to_iso(ts) -> Optional[str]:
    if ts is None:
        return None
    if hasattr(ts, "timestamp"):
        return datetime.fromtimestamp(ts.timestamp(), tz=timezone.utc).isoformat()
    if isinstance(ts, datetime):
        return ts.isoformat()
    return str(ts)


def _require_owner(employer: dict, expected_role: str = "employer") -> None:
    """Raise 403 if the caller isn't the account owner (role == employer)."""
    if employer.get("role") != expected_role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Only the company owner (employer) can perform this action. "
                f"Your role: '{employer.get('role', 'unknown')}'."
            ),
        )


def _company_to_response(company: Company) -> "CompanyResponse":
    """Map a Company ORM instance to CompanyResponse using settings JSONB for extended fields."""
    s = company.settings or {}
    return CompanyResponse(
        id=str(company.id),
        name=company.name,
        industry=s.get("industry"),
        size=s.get("size"),
        ownerId=company.owner_id or "",
        employeeCount=company.employee_count,
        website=s.get("website"),
        address=s.get("address"),
        phone=s.get("phone"),
        description=s.get("description"),
        logoUrl=s.get("logo_url"),
        createdAt=_ts_to_iso(company.created_at),
        updatedAt=_ts_to_iso(company.updated_at),
    )


# ─── Schemas ─────────────────────────────────────────────────────────────────

class EmployerProfileResponse(BaseModel):
    uid: str
    email: str
    firstName: str
    lastName: str
    displayName: str
    role: str
    jobTitle: Optional[str]
    phone: Optional[str]
    companyId: str
    companyName: str
    isActive: bool
    hierarchyLevel: int
    permissions: Dict[str, bool]
    registeredAt: Optional[str]
    updatedAt: Optional[str]
    company: Optional[Dict[str, Any]] = None      # embedded company doc


class CompanyResponse(BaseModel):
    id: str
    name: str
    industry: Optional[str]
    size: Optional[str]
    ownerId: str
    employeeCount: int
    website: Optional[str]
    address: Optional[str]
    phone: Optional[str]
    description: Optional[str]
    logoUrl: Optional[str]
    createdAt: Optional[str]
    updatedAt: Optional[str]


class CompanyStatsResponse(BaseModel):
    companyId: str
    totalEmployees: int
    activeEmployees: int
    inactiveEmployees: int
    roleBreakdown: Dict[str, int]
    departmentBreakdown: Dict[str, int]
    recentJoins: int          # employees added in last 30 days
    computedAt: str


class UpdateEmployerProfileRequest(BaseModel):
    firstName: Optional[str]  = None
    lastName:  Optional[str]  = None
    phone:     Optional[str]  = None
    jobTitle:  Optional[str]  = None


class UpdateCompanyRequest(BaseModel):
    name:        Optional[str] = None
    industry:    Optional[str] = None
    size:        Optional[str] = None
    website:     Optional[str] = None
    address:     Optional[str] = None
    phone:       Optional[str] = None
    description: Optional[str] = None
    logoUrl:     Optional[str] = None


class DeleteAccountRequest(BaseModel):
    confirmation_phrase: str    # must equal "DELETE MY ACCOUNT"
    password: str               # re-authenticate for safety


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str           # min 8 chars


class MutationResponse(BaseModel):
    success: bool
    message: str
    updatedFields: Optional[List[str]] = None


# ─── GET /employer/profile ────────────────────────────────────────────────────

@router.get(
    "/profile",
    response_model=EmployerProfileResponse,
    summary="Get Employer Profile",
    description="Returns the caller's full employer profile, with embedded company document.",
)
async def get_employer_profile(
    employer: dict = Depends(get_employer_user),
    db: Session = Depends(get_session),
):
    company_id_str = employer.get("company_id", "")
    company_doc = None

    if company_id_str:
        try:
            import uuid as _uuid
            cid = _uuid.UUID(company_id_str)
            company = db.query(Company).filter(Company.id == cid).one_or_none()
            if company is not None:
                s = company.settings or {}
                company_doc = {
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
        except (ValueError, Exception) as e:
            print(f"[employer] company fetch error: {e}")

    perms = {k: employer.get(k, False) for k in (
        "can_view_team_reports", "can_manage_employees",
        "can_approve_leaves",    "can_view_analytics",
        "can_create_programs",   "skip_level_access",
    )}

    return EmployerProfileResponse(
        uid=employer.get("id", ""),
        email=employer.get("email", ""),
        firstName=employer.get("first_name", ""),
        lastName=employer.get("last_name", ""),
        displayName=employer.get("display_name", ""),
        role=employer.get("role", "employer"),
        jobTitle=employer.get("job_title"),
        phone=employer.get("phone"),
        companyId=company_id_str,
        companyName=employer.get("company_name", ""),
        isActive=employer.get("is_active", True),
        hierarchyLevel=employer.get("hierarchy_level", 0),
        permissions=perms,
        registeredAt=_ts_to_iso(employer.get("registered_at") or employer.get("created_at")),
        updatedAt=_ts_to_iso(employer.get("updated_at")),
        company=company_doc,
    )


# ─── PATCH /employer/profile ──────────────────────────────────────────────────

@router.patch(
    "/profile",
    response_model=MutationResponse,
    summary="Update Employer Profile",
    description="Update the employer's personal fields (name, phone, jobTitle). Only provided fields are changed.",
)
async def update_employer_profile(
    req: UpdateEmployerProfileRequest,
    employer: dict = Depends(get_employer_user),
    db: Session = Depends(get_session),
):
    _require_owner(employer)
    uid = employer.get("id")

    # Build the profile JSONB patch — only include fields that were provided
    profile_updates: Dict[str, Any] = {}
    updated_fields: List[str] = []

    field_map = {
        "firstName": "first_name",
        "lastName":  "last_name",
        "phone":     "phone",
        "jobTitle":  "job_title",
    }
    for req_field, profile_key in field_map.items():
        val = getattr(req, req_field, None)
        if val is not None:
            profile_updates[profile_key] = val
            updated_fields.append(req_field)

    if not profile_updates:
        raise HTTPException(400, "No valid fields provided to update.")

    # Rebuild display_name if name changed
    if "first_name" in profile_updates or "last_name" in profile_updates:
        fn = profile_updates.get("first_name", employer.get("first_name", ""))
        ln = profile_updates.get("last_name",  employer.get("last_name",  ""))
        profile_updates["display_name"] = f"{fn} {ln}"

    # Merge the new values into the existing profile JSONB
    user = db.query(User).filter(User.id == uid).one_or_none()
    if user is None:
        raise HTTPException(404, "User not found.")
    merged = dict(user.profile or {})
    merged.update(profile_updates)
    user.profile = merged
    db.commit()

    return MutationResponse(
        success=True,
        message="Employer profile updated successfully.",
        updatedFields=updated_fields,
    )


# ─── GET /employer/company ────────────────────────────────────────────────────

@router.get(
    "/company",
    response_model=CompanyResponse,
    summary="Get Company Details",
    description="Returns the full company document for the employer's company.",
)
async def get_company(
    employer: dict = Depends(get_employer_user),
    db: Session = Depends(get_session),
):
    company_id_str = employer.get("company_id")
    if not company_id_str:
        raise HTTPException(404, "No company associated with this account.")

    import uuid as _uuid
    try:
        cid = _uuid.UUID(company_id_str)
    except ValueError:
        raise HTTPException(400, "Invalid company ID.")

    company = db.query(Company).filter(Company.id == cid).one_or_none()
    if company is None:
        raise HTTPException(404, "Company not found.")

    return _company_to_response(company)


# ─── PATCH /employer/company ──────────────────────────────────────────────────

@router.patch(
    "/company",
    response_model=MutationResponse,
    summary="Update Company Details",
    description=(
        "**Owner only (role=employer).** "
        "Update company metadata — name, industry, size, website, address, phone, description, logoUrl."
    ),
)
async def update_company(
    req: UpdateCompanyRequest,
    employer: dict = Depends(get_employer_user),
    db: Session = Depends(get_session),
):
    _require_owner(employer)
    company_id_str = employer.get("company_id")
    if not company_id_str:
        raise HTTPException(400, "Employer has no associated company_id.")

    import uuid as _uuid
    try:
        cid = _uuid.UUID(company_id_str)
    except ValueError:
        raise HTTPException(400, "Invalid company ID.")

    company = db.query(Company).filter(Company.id == cid).one_or_none()
    if company is None:
        raise HTTPException(404, "Company document not found.")

    updated_fields: List[str] = []

    # Top-level column: name
    if req.name is not None:
        company.name = req.name
        updated_fields.append("name")

    # Extended fields stored in settings JSONB
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
        raise HTTPException(400, "No valid fields provided to update.")

    company.settings = new_settings
    db.commit()

    return MutationResponse(
        success=True,
        message="Company details updated successfully.",
        updatedFields=updated_fields,
    )


# ─── GET /employer/company/stats ─────────────────────────────────────────────

@router.get(
    "/company/stats",
    response_model=CompanyStatsResponse,
    summary="Company Stats",
    description=(
        "Returns headcount, active/inactive split, role breakdown, "
        "department breakdown, and recent joins (last 30 days) for the employer's company."
    ),
)
async def get_company_stats(
    employer: dict = Depends(get_employer_user),
    db: Session = Depends(get_session),
):
    company_id_str = employer.get("company_id")

    import uuid as _uuid
    if not company_id_str:
        raise HTTPException(400, "Employer has no associated company_id.")
    try:
        cid = _uuid.UUID(company_id_str)
    except ValueError:
        raise HTTPException(400, "Invalid company ID.")

    try:
        users = (
            db.query(User)
            .filter(User.company_id == cid)
            .all()
        )
    except Exception as e:
        raise HTTPException(500, f"Database query failed: {e}")

    now = datetime.now(timezone.utc)
    thirty_days_ago = now - timedelta(days=30)

    total = active = inactive = recent_joins = 0
    roles: Dict[str, int] = {}
    depts: Dict[str, int] = {}

    for user in users:
        if user.role == "employer":
            continue   # don't count the owner in employee stats
        total += 1
        if user.is_active:
            active += 1
        else:
            inactive += 1

        role = user.role or "unknown"
        roles[role] = roles.get(role, 0) + 1

        dept = user.department or "Unassigned"
        depts[dept] = depts.get(dept, 0) + 1

        created_at = user.created_at
        if created_at is not None:
            # Ensure timezone-aware comparison
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if created_at >= thirty_days_ago:
                recent_joins += 1

    return CompanyStatsResponse(
        companyId=company_id_str,
        totalEmployees=total,
        activeEmployees=active,
        inactiveEmployees=inactive,
        roleBreakdown=roles,
        departmentBreakdown=depts,
        recentJoins=recent_joins,
        computedAt=now.isoformat(),
    )


# ─── POST /employer/change-password ──────────────────────────────────────────

@router.post(
    "/change-password",
    response_model=MutationResponse,
    summary="Change Employer Password",
    description=(
        "Re-authenticates via bcrypt, then updates the password in Postgres. "
        "New password must be at least 8 characters."
    ),
)
async def change_password(
    req: ChangePasswordRequest,
    employer: dict = Depends(get_employer_user),
    db: Session = Depends(get_session),
):
    _require_owner(employer)

    if len(req.new_password) < 8:
        raise HTTPException(400, "New password must be at least 8 characters.")

    if req.current_password == req.new_password:
        raise HTTPException(400, "New password must be different from the current password.")

    uid = employer.get("id")
    user = db.query(User).filter(User.id == uid).one_or_none()
    if user is None:
        raise HTTPException(404, "User not found.")

    # Re-authenticate: verify current password against stored hash
    if user.password_hash is None or not verify_password(req.current_password, user.password_hash):
        raise HTTPException(401, "Current password is incorrect.")

    user.password_hash = hash_password(req.new_password)
    db.commit()

    return MutationResponse(
        success=True,
        message="Password changed successfully. Please log in again with your new password.",
    )


# ─── DELETE /employer/account ─────────────────────────────────────────────────

@router.delete(
    "/account",
    response_model=MutationResponse,
    summary="Delete Employer Account",
    description=(
        "**Irreversible.** Permanently deletes the employer's Postgres user row "
        "and company document. Employee company_id FKs are SET NULL on cascade. "
        "Requires `confirmation_phrase = 'DELETE MY ACCOUNT'` and the current `password`."
    ),
)
async def delete_employer_account(
    req: DeleteAccountRequest,
    employer: dict = Depends(get_employer_user),
    db: Session = Depends(get_session),
):
    _require_owner(employer)

    if req.confirmation_phrase != "DELETE MY ACCOUNT":
        raise HTTPException(
            400,
            "Confirmation phrase must be exactly: DELETE MY ACCOUNT",
        )

    uid = employer.get("id")
    company_id_str = employer.get("company_id")

    # Re-authenticate: verify password before destructive action
    user = db.query(User).filter(User.id == uid).one_or_none()
    if user is None:
        raise HTTPException(404, "User not found.")
    if user.password_hash is None or not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "Password is incorrect. Account deletion aborted.")

    # 1. Delete company document (SET NULL cascade handles users.company_id)
    if company_id_str:
        import uuid as _uuid
        try:
            cid = _uuid.UUID(company_id_str)
            db.query(Company).filter(Company.id == cid).delete()
        except Exception as e:
            print(f"[employer] Company deletion error for {company_id_str}: {e}")

    # 2. Delete user row (ON DELETE CASCADE handles refresh_tokens, sessions, check_ins)
    db.query(User).filter(User.id == uid).delete()
    db.commit()

    return MutationResponse(
        success=True,
        message="Employer account and company deleted permanently.",
    )
