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

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth.password import hash_password, verify_password
from db.models import (
    ChatSession,
    Company,
    MentalHealthReport,
    PhysicalHealthCheckin,
    UsageLog,
    User,
    UserGamification,
)
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


def _safe_int(val, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _to_dt(ts) -> Optional[datetime]:
    """Coerce a timestamp-ish value to an aware UTC datetime."""
    if ts is None:
        return None
    try:
        if hasattr(ts, "timestamp") and not isinstance(ts, datetime):
            return datetime.fromtimestamp(ts.timestamp(), tz=timezone.utc)
        if isinstance(ts, datetime):
            return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    except Exception:
        return None
    return None


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


# ─── GET /employer/team-usage ─────────────────────────────────────────────────

@router.get(
    "/team-usage",
    summary="Per-Employee Engagement Summary",
    description=(
        "**Employer / HR only.** Returns engagement activity per employee for the "
        "caller's company. Privacy-safe: activity counts and engagement metrics only — "
        "no conversation content, no wellness scores, no cost data."
    ),
)
async def team_usage(
    days:       int            = Query(30, ge=1, le=365, description="Look-back window in days"),
    department: Optional[str]  = Query(None, description="Filter by department"),
    status:     Optional[str]  = Query(None, description="active | dormant | churned"),
    sort_by:    str            = Query("engagementScore", description="engagementScore | lastActive | sessions | checkIns | streak"),
    page:       int            = Query(1, ge=1),
    limit:      int            = Query(20, ge=1, le=100),
    employer: dict = Depends(get_employer_user),
    db: Session = Depends(get_session),
):
    company_id_str = employer.get("company_id")
    if not company_id_str:
        raise HTTPException(400, "Employer has no associated company_id.")

    import uuid as _uuid
    try:
        cid = _uuid.UUID(company_id_str)
    except ValueError:
        raise HTTPException(400, "Invalid company ID.")

    now    = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    # ── 1. All active employees in this company ───────────────────────────
    try:
        emp_rows = (
            db.query(User)
            .filter(User.company_id == cid, User.is_active.is_(True))
            .all()
        )
    except Exception as e:
        raise HTTPException(500, f"users query failed: {e}")

    employees: Dict[str, dict] = {}
    for u in emp_rows:
        if u.role in ("employer", "super_admin"):
            continue
        if department and (u.department or "").lower() != department.lower():
            continue
        p = u.profile or {}
        employees[u.id] = {
            "id":              u.id,
            "first_name":      p.get("first_name", ""),
            "last_name":       p.get("last_name", ""),
            "department":      u.department,
            "position":        p.get("position"),
            "role":            u.role or "employee",
            "last_active_at":  u.last_active_at,
        }

    if not employees:
        return {
            "companyId": company_id_str, "windowDays": days,
            "employees": [], "total": 0, "page": page, "limit": limit,
            "totalPages": 1, "hasNext": False, "hasPrev": False,
            "summary": {
                "totalEmployees": 0, "activeCount": 0,
                "dormantCount": 0, "churnedCount": 0,
                "avgEngagementScore": 0.0, "participationRatePct": 0.0,
            },
        }

    company_user_ids = list(employees.keys())

    # ── 2. Batch-fetch activity data ──────────────────────────────────────

    # Chat sessions: ChatSession has no company_id column → filter by user_id
    session_counts: Dict[str, int] = {}
    try:
        chat_rows = (
            db.query(ChatSession)
            .filter(
                ChatSession.user_id.in_(company_user_ids),
                ChatSession.created_at >= cutoff,
            )
            .all()
        )
        for cs in chat_rows:
            uid = cs.user_id or ""
            if uid:
                session_counts[uid] = session_counts.get(uid, 0) + 1
    except Exception as e:
        print(f"[team_usage] chat_sessions error: {e}")

    # Mental health reports (counts as wellness check-ins activity)
    checkin_counts: Dict[str, int] = {}
    try:
        mh_rows = (
            db.query(MentalHealthReport)
            .filter(
                MentalHealthReport.company_id == cid,
                MentalHealthReport.generated_at >= cutoff,
            )
            .all()
        )
        for r in mh_rows:
            uid = r.user_id or ""
            if uid:
                checkin_counts[uid] = checkin_counts.get(uid, 0) + 1
    except Exception as e:
        print(f"[team_usage] mental_health_reports error: {e}")

    physical_counts: Dict[str, int] = {}
    try:
        ph_rows = (
            db.query(PhysicalHealthCheckin)
            .filter(
                PhysicalHealthCheckin.company_id == cid,
                PhysicalHealthCheckin.created_at >= cutoff,
            )
            .all()
        )
        for r in ph_rows:
            uid = r.user_id or ""
            if uid:
                physical_counts[uid] = physical_counts.get(uid, 0) + 1
    except Exception as e:
        print(f"[team_usage] physical_health_checkins error: {e}")

    # Features used — privacy-safe: feature names only, no content/cost
    features_used: Dict[str, set] = {}
    try:
        usage_rows = (
            db.query(UsageLog)
            .filter(
                UsageLog.company_id == cid,
                UsageLog.created_at >= cutoff,
            )
            .all()
        )
        for r in usage_rows:
            uid  = r.user_id or ""
            feat = r.feature or ""
            if uid and feat:
                if uid not in features_used:
                    features_used[uid] = set()
                features_used[uid].add(feat)
    except Exception as e:
        print(f"[team_usage] usage_logs error: {e}")

    # Gamification — current state, no time filter
    gam_map: Dict[str, dict] = {}
    try:
        gam_rows = (
            db.query(UserGamification)
            .filter(UserGamification.company_id == cid)
            .all()
        )
        for g in gam_rows:
            uid = g.user_id or ""
            if uid:
                extras = g.extras or {}
                gam_map[uid] = {
                    "total_points":    g.points,
                    "level":           g.level,
                    "current_streak":  g.streak,
                    "last_check_in":   extras.get("last_check_in"),
                    "badges":          list(g.badges or []),
                }
    except Exception as e:
        print(f"[team_usage] user_gamification error: {e}")

    # ── 3. Per-employee helpers ───────────────────────────────────────────

    def _activity_status(last_active_at) -> str:
        dt = _to_dt(last_active_at)
        if dt is None:
            return "churned"
        days_since = (now - dt).days
        if days_since <= 7:   return "active"
        if days_since <= 30:  return "dormant"
        return "churned"

    def _days_ago(ts) -> Optional[int]:
        dt = _to_dt(ts)
        if dt is None:
            return None
        return max(0, (now - dt).days)

    def _engagement_score(sessions: int, checkins: int, streak: int) -> float:
        """
        (sessions/days × 40%) + (checkins/days × 40%) + (streak/30 × 20%)
        Capped at 100, rounded to 1 decimal.
        """
        s = min(sessions / days, 1.0) * 40
        c = min(checkins / days, 1.0) * 40
        t = min(streak   / 30,   1.0) * 20
        return round(min(s + c + t, 100.0), 1)

    # ── 4. Build per-employee records ─────────────────────────────────────
    result = []
    for uid, udata in employees.items():
        sessions   = session_counts.get(uid, 0)
        checkins   = checkin_counts.get(uid, 0)
        physical   = physical_counts.get(uid, 0)
        feats      = sorted(features_used.get(uid, set()))
        gam        = gam_map.get(uid, {})
        streak     = _safe_int(gam.get("current_streak", 0))
        level      = _safe_int(gam.get("level", 1))
        last_at    = udata.get("last_active_at")
        act_status = _activity_status(last_at)

        if status and act_status != status:
            continue

        score = _engagement_score(sessions, checkins, streak)

        result.append({
            "uid":                     uid,
            "firstName":               udata.get("first_name", ""),
            "lastName":                udata.get("last_name", ""),
            "department":              udata.get("department"),
            "position":                udata.get("position"),
            "role":                    udata.get("role", "employee"),
            "activityStatus":          act_status,
            "lastActiveDaysAgo":       _days_ago(last_at),
            "sessionsLast30d":         sessions,
            "checkInsLast30d":         checkins,
            "physicalCheckInsLast30d": physical,
            "featuresUsed":            feats,
            "gamificationLevel":       level,
            "currentStreak":           streak,
            "engagementScore":         score,
        })

    # ── 5. Sort ───────────────────────────────────────────────────────────
    sort_key_map = {
        "engagementScore": (lambda r: r["engagementScore"],         True),
        "lastActive":      (lambda r: r["lastActiveDaysAgo"] or 9999, False),
        "sessions":        (lambda r: r["sessionsLast30d"],           True),
        "checkIns":        (lambda r: r["checkInsLast30d"],           True),
        "streak":          (lambda r: r["currentStreak"],             True),
    }
    key_fn, reverse = sort_key_map.get(sort_by, sort_key_map["engagementScore"])
    result.sort(key=key_fn, reverse=reverse)

    # ── 6. Summary ────────────────────────────────────────────────────────
    total         = len(result)
    active_count  = sum(1 for r in result if r["activityStatus"] == "active")
    dormant_count = sum(1 for r in result if r["activityStatus"] == "dormant")
    churned_count = sum(1 for r in result if r["activityStatus"] == "churned")
    avg_score     = round(sum(r["engagementScore"] for r in result) / total, 1) if total else 0.0
    participation = round(active_count / total * 100, 1) if total else 0.0

    # ── 7. Paginate ───────────────────────────────────────────────────────
    offset      = (page - 1) * limit
    total_pages = max(1, (total + limit - 1) // limit)

    return {
        "companyId":  company_id_str,
        "windowDays": days,
        "employees":  result[offset: offset + limit],
        "total":      total,
        "page":       page,
        "limit":      limit,
        "totalPages": total_pages,
        "hasNext":    offset + limit < total,
        "hasPrev":    page > 1,
        "summary": {
            "totalEmployees":       total,
            "activeCount":          active_count,
            "dormantCount":         dormant_count,
            "churnedCount":         churned_count,
            "avgEngagementScore":   avg_score,
            "participationRatePct": participation,
        },
    }


# ─── GET /employer/gamification ───────────────────────────────────────────────

@router.get(
    "/gamification",
    summary="Company Gamification Overview + Anonymous Leaderboard",
    description=(
        "**Employer / HR only.** Returns gamification stats and an anonymous leaderboard "
        "for the caller's company. Real names are never exposed — uses anonymous display names."
    ),
)
async def employer_gamification(
    employer: dict = Depends(get_employer_user),
    db: Session = Depends(get_session),
):
    company_id_str = employer.get("company_id")
    if not company_id_str:
        raise HTTPException(400, "Employer has no associated company_id.")

    import uuid as _uuid
    try:
        cid = _uuid.UUID(company_id_str)
    except ValueError:
        raise HTTPException(400, "Invalid company ID.")

    # ── All gamification rows for this company ────────────────────────────
    try:
        gam_rows = (
            db.query(UserGamification)
            .filter(UserGamification.company_id == cid)
            .all()
        )
    except Exception as e:
        raise HTTPException(500, f"user_gamification query failed: {e}")

    if not gam_rows:
        return {
            "companyId": company_id_str,
            "totalPlayers": 0, "activePlayers7d": 0,
            "avgPoints": 0.0, "avgLevel": 0.0, "avgStreak": 0.0,
            "badgeDistribution": {}, "leaderboard": [],
            "activeChallenges": [],
        }

    now       = datetime.now(timezone.utc)
    cutoff_7d = now - timedelta(days=7)

    total_pts = total_lvl = total_streak = active_7d = 0
    badge_dist: Dict[str, int] = {}
    leaderboard_raw = []

    # Anonymous display-name lookup — uses anonymous_profiles.handle.
    # Filter to users in this company (AnonymousProfile has no company_id col).
    anon_map: Dict[str, str] = {}
    try:
        from db.models import AnonymousProfile
        gam_user_ids = [g.user_id for g in gam_rows if g.user_id]
        if gam_user_ids:
            for ap in (
                db.query(AnonymousProfile)
                .filter(AnonymousProfile.user_id.in_(gam_user_ids))
                .all()
            ):
                anon_map[ap.user_id] = ap.handle or "User ???"
    except Exception as e:
        print(f"[employer_gamification] anonymous_profiles error: {e}")

    for g in gam_rows:
        uid    = g.user_id or ""
        pts    = _safe_int(g.points)
        lvl    = _safe_int(g.level if g.level is not None else 1)
        streak = _safe_int(g.streak)

        total_pts    += pts
        total_lvl    += lvl
        total_streak += streak

        for badge in (g.badges or []):
            badge_dist[badge] = badge_dist.get(badge, 0) + 1

        extras = g.extras or {}
        last_ci = extras.get("last_check_in")
        last_dt = _to_dt(last_ci)
        if last_dt is not None and last_dt >= cutoff_7d:
            active_7d += 1

        leaderboard_raw.append({
            "rank":          0,   # filled after sort
            "displayName":   anon_map.get(uid, "User ???"),
            "level":         lvl,
            "totalPoints":   pts,
            "currentStreak": streak,
            "badges":        len(g.badges or []),
        })

    # Sort leaderboard by points desc, assign rank
    leaderboard_raw.sort(key=lambda r: r["totalPoints"], reverse=True)
    for i, entry in enumerate(leaderboard_raw):
        entry["rank"] = i + 1

    n = len(gam_rows)

    # ── Active challenges for this company ────────────────────────────────
    active_challenges = []
    try:
        from db.models import WellnessChallenge
        ch_rows = (
            db.query(WellnessChallenge)
            .filter(
                WellnessChallenge.is_active.is_(True),
                WellnessChallenge.company_id == cid,
            )
            .all()
        )
        for ch in ch_rows:
            data = ch.data or {}
            active_challenges.append({
                "id":           str(ch.id),
                "title":        ch.title,
                "description":  ch.description,
                "type":         data.get("type"),
                "target":       data.get("target"),
                "pointsReward": data.get("points_reward"),
                "endsAt":       _ts_to_iso(ch.ends_at),
            })
    except Exception as e:
        print(f"[employer_gamification] challenges error: {e}")

    return {
        "companyId":         company_id_str,
        "totalPlayers":      n,
        "activePlayers7d":   active_7d,
        "avgPoints":         round(total_pts / n, 1),
        "avgLevel":          round(total_lvl / n, 2),
        "avgStreak":         round(total_streak / n, 2),
        "badgeDistribution": badge_dist,
        "leaderboard":       leaderboard_raw[:20],   # top 20 only
        "activeChallenges":  active_challenges,
    }
