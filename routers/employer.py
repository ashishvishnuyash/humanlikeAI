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
  POST   /api/employer/change-password  → Change own password via Firebase Auth REST API

All endpoints require JWT with role == employer (only the account owner).
HR users can read profile/company but cannot mutate or delete.
"""

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from firebase_admin import auth as fb_auth, firestore as admin_firestore
from firebase_config import get_db, firebaseConfig
from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from pydantic import BaseModel, EmailStr

from routers.auth import get_current_user, get_employer_user

router = APIRouter(prefix="/employer", tags=["Employer CRUD"])

_Increment = admin_firestore.firestore.Increment


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _safe_int(val, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default

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
async def get_employer_profile(employer: dict = Depends(get_employer_user)):
    db = get_db()
    company_id = employer.get("company_id", "")
    company_doc = None

    if db and company_id:
        try:
            cdoc = db.collection("companies").document(company_id).get()
            if cdoc.exists:
                raw = cdoc.to_dict()
                company_doc = {
                    "id":            raw.get("id", company_id),
                    "name":          raw.get("name"),
                    "industry":      raw.get("industry"),
                    "size":          raw.get("size"),
                    "ownerId":       raw.get("owner_id"),
                    "employeeCount": raw.get("employee_count", 0),
                    "website":       raw.get("website"),
                    "address":       raw.get("address"),
                    "phone":         raw.get("phone"),
                    "description":   raw.get("description"),
                    "logoUrl":       raw.get("logo_url"),
                    "createdAt":     _ts_to_iso(raw.get("created_at")),
                    "updatedAt":     _ts_to_iso(raw.get("updated_at")),
                }
        except Exception as e:
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
        companyId=company_id,
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
):
    _require_owner(employer)
    uid = employer.get("id")
    db  = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    updates: Dict[str, Any] = {"updated_at": SERVER_TIMESTAMP}
    updated_fields: List[str] = []

    field_map = {
        "firstName": "first_name",
        "lastName":  "last_name",
        "phone":     "phone",
        "jobTitle":  "job_title",
    }
    for req_field, db_field in field_map.items():
        val = getattr(req, req_field, None)
        if val is not None:
            updates[db_field] = val
            updated_fields.append(req_field)

    if len(updates) == 1:
        raise HTTPException(400, "No valid fields provided to update.")

    # Rebuild display_name if name changed
    if "first_name" in updates or "last_name" in updates:
        fn = updates.get("first_name", employer.get("first_name", ""))
        ln = updates.get("last_name",  employer.get("last_name",  ""))
        updates["display_name"] = f"{fn} {ln}"
        try:
            fb_auth.update_user(uid, display_name=updates["display_name"])
        except Exception as e:
            print(f"[employer] Firebase display_name sync error: {e}")

    db.collection("users").document(uid).update(updates)

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
async def get_company(employer: dict = Depends(get_employer_user)):
    company_id = employer.get("company_id")
    if not company_id:
        raise HTTPException(404, "No company associated with this account.")

    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    cdoc = db.collection("companies").document(company_id).get()
    if not cdoc.exists:
        raise HTTPException(404, "Company not found.")

    raw = cdoc.to_dict()
    return CompanyResponse(
        id=raw.get("id", company_id),
        name=raw.get("name", ""),
        industry=raw.get("industry"),
        size=raw.get("size"),
        ownerId=raw.get("owner_id", ""),
        employeeCount=raw.get("employee_count", 0),
        website=raw.get("website"),
        address=raw.get("address"),
        phone=raw.get("phone"),
        description=raw.get("description"),
        logoUrl=raw.get("logo_url"),
        createdAt=_ts_to_iso(raw.get("created_at")),
        updatedAt=_ts_to_iso(raw.get("updated_at")),
    )


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
):
    _require_owner(employer)
    company_id = employer.get("company_id")
    if not company_id:
        raise HTTPException(400, "Employer has no associated company_id.")

    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    cdoc = db.collection("companies").document(company_id).get()
    if not cdoc.exists:
        raise HTTPException(404, "Company document not found.")

    updates: Dict[str, Any] = {"updated_at": SERVER_TIMESTAMP}
    updated_fields: List[str] = []

    field_map = {
        "name":        "name",
        "industry":    "industry",
        "size":        "size",
        "website":     "website",
        "address":     "address",
        "phone":       "phone",
        "description": "description",
        "logoUrl":     "logo_url",
    }
    for req_field, db_field in field_map.items():
        val = getattr(req, req_field, None)
        if val is not None:
            updates[db_field] = val
            updated_fields.append(req_field)

    if len(updates) == 1:
        raise HTTPException(400, "No valid fields provided to update.")

    db.collection("companies").document(company_id).update(updates)

    # If name changed, sync to employer user profile too
    if "name" in updates:
        uid = employer.get("id")
        try:
            db.collection("users").document(uid).update({
                "company_name": updates["name"],
                "updated_at":   SERVER_TIMESTAMP,
            })
        except Exception as e:
            print(f"[employer] company_name sync to user failed: {e}")

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
async def get_company_stats(employer: dict = Depends(get_employer_user)):
    company_id = employer.get("company_id")
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    try:
        docs = db.collection("users").where("company_id", "==", company_id).stream()
    except Exception as e:
        raise HTTPException(500, f"Database query failed: {e}")

    now = datetime.now(timezone.utc)
    thirty_days_ago_ts = now.timestamp() - (30 * 86400)

    total = active = inactive = recent_joins = 0
    roles: Dict[str, int] = {}
    depts: Dict[str, int] = {}

    for doc in docs:
        d = doc.to_dict()
        if d.get("role") == "employer":
            continue   # don't count the owner in employee stats
        total += 1
        if d.get("is_active", True):
            active += 1
        else:
            inactive += 1

        role = d.get("role", "unknown")
        roles[role] = roles.get(role, 0) + 1

        dept = d.get("department") or "Unassigned"
        depts[dept] = depts.get(dept, 0) + 1

        created_ts = d.get("created_at")
        if created_ts and hasattr(created_ts, "timestamp"):
            if created_ts.timestamp() >= thirty_days_ago_ts:
                recent_joins += 1

    return CompanyStatsResponse(
        companyId=company_id,
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
        "Re-authenticates via Firebase REST API, then updates the password. "
        "New password must be at least 8 characters."
    ),
)
async def change_password(
    req: ChangePasswordRequest,
    employer: dict = Depends(get_employer_user),
):
    _require_owner(employer)

    if len(req.new_password) < 8:
        raise HTTPException(400, "New password must be at least 8 characters.")

    if req.current_password == req.new_password:
        raise HTTPException(400, "New password must be different from the current password.")

    api_key = firebaseConfig.get("apiKey")
    if not api_key:
        raise HTTPException(500, "Server misconfiguration: Missing Firebase API Key.")

    email = employer.get("email", "")

    # Step 1: Re-authenticate with current password
    verify_url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}"
    async with httpx.AsyncClient() as client:
        resp = await client.post(verify_url, json={
            "email":             email,
            "password":          req.current_password,
            "returnSecureToken": False,
        })

    if resp.status_code != 200:
        raise HTTPException(401, "Current password is incorrect.")

    # Step 2: Update password in Firebase Auth
    uid = employer.get("id")
    try:
        fb_auth.update_user(uid, password=req.new_password)
    except Exception as e:
        raise HTTPException(500, f"Password update failed: {e}")

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
        "⚠️ **Irreversible.** Permanently deletes the employer's Firebase Auth account, "
        "user profile, and company document. All employee accounts remain intact. "
        "Requires `confirmation_phrase = 'DELETE MY ACCOUNT'` and the current `password`."
    ),
)
async def delete_employer_account(
    req: DeleteAccountRequest,
    employer: dict = Depends(get_employer_user),
):
    _require_owner(employer)

    if req.confirmation_phrase != "DELETE MY ACCOUNT":
        raise HTTPException(
            400,
            "Confirmation phrase must be exactly: DELETE MY ACCOUNT",
        )

    # Re-authenticate before destructive action
    api_key = firebaseConfig.get("apiKey")
    if not api_key:
        raise HTTPException(500, "Server misconfiguration: Missing Firebase API Key.")

    email = employer.get("email", "")
    verify_url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}"
    async with httpx.AsyncClient() as client:
        resp = await client.post(verify_url, json={
            "email":             email,
            "password":          req.password,
            "returnSecureToken": False,
        })

    if resp.status_code != 200:
        raise HTTPException(401, "Password is incorrect. Account deletion aborted.")

    uid        = employer.get("id")
    company_id = employer.get("company_id")
    db         = get_db()

    if not db:
        raise HTTPException(503, "Database unavailable")

    errors = []

    # 1. Delete Firestore user profile
    try:
        db.collection("users").document(uid).delete()
    except Exception as e:
        errors.append(f"user_profile: {e}")

    # 2. Delete Firestore company document
    if company_id:
        try:
            db.collection("companies").document(company_id).delete()
        except Exception as e:
            errors.append(f"company: {e}")

    # 3. Delete Firebase Auth account
    try:
        fb_auth.delete_user(uid)
    except Exception as e:
        errors.append(f"firebase_auth: {e}")

    if errors:
        print(f"[employer] Partial deletion errors for {uid}: {errors}")
        raise HTTPException(
            500,
            f"Account partially deleted. Manual cleanup may be needed: {errors}",
        )

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
):
    company_id = employer.get("company_id")
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    now    = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    # ── 1. All active employees in this company ───────────────────────────
    try:
        emp_docs = list(
            db.collection("users")
            .where("company_id", "==", company_id)
            .where("is_active",  "==", True)
            .stream()
        )
    except Exception as e:
        raise HTTPException(500, f"users query failed: {e}")

    employees: Dict[str, dict] = {}
    for doc in emp_docs:
        d = doc.to_dict()
        if d.get("role") in ("employer", "super_admin"):
            continue
        if department and (d.get("department") or "").lower() != department.lower():
            continue
        employees[doc.id] = d

    if not employees:
        return {
            "companyId": company_id, "windowDays": days,
            "employees": [], "total": 0, "page": page, "limit": limit,
            "totalPages": 1, "hasNext": False, "hasPrev": False,
            "summary": {
                "totalEmployees": 0, "activeCount": 0,
                "dormantCount": 0, "churnedCount": 0,
                "avgEngagementScore": 0.0, "participationRatePct": 0.0,
            },
        }

    # ── 2. Batch-fetch activity data (all company-scoped, single query each) ──

    session_counts:  Dict[str, int] = {}
    try:
        for doc in (
            db.collection("chat_sessions")
            .where("company_id", "==", company_id)
            .where("started_at", ">=", cutoff)
            .stream()
        ):
            uid = doc.to_dict().get("user_id", "")
            if uid:
                session_counts[uid] = session_counts.get(uid, 0) + 1
    except Exception as e:
        print(f"[team_usage] chat_sessions error: {e}")

    checkin_counts: Dict[str, int] = {}
    try:
        for doc in (
            db.collection("check_ins")
            .where("company_id", "==", company_id)
            .where("created_at", ">=", cutoff)
            .stream()
        ):
            uid = doc.to_dict().get("user_id", "")
            if uid:
                checkin_counts[uid] = checkin_counts.get(uid, 0) + 1
    except Exception as e:
        print(f"[team_usage] check_ins error: {e}")

    physical_counts: Dict[str, int] = {}
    try:
        for doc in (
            db.collection("physical_health_checkins")
            .where("company_id", "==", company_id)
            .where("created_at", ">=", cutoff)
            .stream()
        ):
            uid = doc.to_dict().get("user_id", "")
            if uid:
                physical_counts[uid] = physical_counts.get(uid, 0) + 1
    except Exception as e:
        print(f"[team_usage] physical_health_checkins error: {e}")

    # Features used — privacy-safe: feature names only, no content/cost
    features_used: Dict[str, set] = {}
    try:
        for doc in (
            db.collection("usage_logs")
            .where("company_id", "==", company_id)
            .where("timestamp",  ">=", cutoff)
            .stream()
        ):
            d    = doc.to_dict()
            uid  = d.get("user_id", "")
            feat = d.get("feature", "")
            if uid and feat:
                if uid not in features_used:
                    features_used[uid] = set()
                features_used[uid].add(feat)
    except Exception as e:
        print(f"[team_usage] usage_logs error: {e}")

    # Gamification — current state, no time filter
    gam_map: Dict[str, dict] = {}
    try:
        for doc in (
            db.collection("user_gamification")
            .where("company_id", "==", company_id)
            .stream()
        ):
            d   = doc.to_dict()
            uid = d.get("employee_id", "")
            if uid:
                gam_map[uid] = d
    except Exception as e:
        print(f"[team_usage] user_gamification error: {e}")

    # ── 3. Per-employee helpers ───────────────────────────────────────────

    def _activity_status(last_active_at) -> str:
        if last_active_at is None:
            return "churned"
        try:
            if hasattr(last_active_at, "timestamp"):
                dt = datetime.fromtimestamp(last_active_at.timestamp(), tz=timezone.utc)
            elif isinstance(last_active_at, datetime):
                dt = last_active_at if last_active_at.tzinfo else last_active_at.replace(tzinfo=timezone.utc)
            else:
                return "churned"
            days_since = (now - dt).days
            if days_since <= 7:   return "active"
            if days_since <= 30:  return "dormant"
            return "churned"
        except Exception:
            return "churned"

    def _days_ago(ts) -> Optional[int]:
        if ts is None:
            return None
        try:
            if hasattr(ts, "timestamp"):
                dt = datetime.fromtimestamp(ts.timestamp(), tz=timezone.utc)
            elif isinstance(ts, datetime):
                dt = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            else:
                return None
            return max(0, (now - dt).days)
        except Exception:
            return None

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
        streak     = int(gam.get("current_streak", 0))
        level      = int(gam.get("level", 1))
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
        "companyId":  company_id,
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
):
    company_id = employer.get("company_id")
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    # ── All gamification docs for this company ────────────────────────────
    gam_docs = []
    try:
        gam_docs = list(
            db.collection("user_gamification")
            .where("company_id", "==", company_id)
            .stream()
        )
    except Exception as e:
        raise HTTPException(500, f"user_gamification query failed: {e}")

    if not gam_docs:
        return {
            "companyId": company_id,
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

    # Anonymous profile map
    anon_map: Dict[str, str] = {}
    try:
        for doc in (
            db.collection("anonymous_profiles")
            .where("company_id", "==", company_id)
            .stream()
        ):
            d = doc.to_dict()
            anon_map[d.get("employee_id", "")] = d.get("display_name", "User ???")
    except Exception:
        pass

    for doc in gam_docs:
        d    = doc.to_dict()
        uid  = d.get("employee_id", "")
        pts  = _safe_int(d.get("total_points"))
        lvl  = _safe_int(d.get("level", 1))
        str_ = _safe_int(d.get("current_streak"))

        total_pts    += pts
        total_lvl    += lvl
        total_streak += str_

        for badge in d.get("badges", []):
            badge_dist[badge] = badge_dist.get(badge, 0) + 1

        lci = d.get("last_check_in")
        if lci and hasattr(lci, "timestamp"):
            dt = datetime.fromtimestamp(lci.timestamp(), tz=timezone.utc)
            if dt >= cutoff_7d:
                active_7d += 1

        leaderboard_raw.append({
            "rank":          0,   # filled after sort
            "displayName":   anon_map.get(uid, "User ???"),
            "level":         lvl,
            "totalPoints":   pts,
            "currentStreak": str_,
            "badges":        len(d.get("badges", [])),
        })

    # Sort leaderboard by points desc, assign rank
    leaderboard_raw.sort(key=lambda r: r["totalPoints"], reverse=True)
    for i, entry in enumerate(leaderboard_raw):
        entry["rank"] = i + 1

    n = len(gam_docs)

    # ── Active challenges for this company ────────────────────────────────
    active_challenges = []
    try:
        for doc in db.collection("challenges").stream():
            d = doc.to_dict()
            if not d.get("is_active", False):
                continue
            cid = d.get("company_id")
            if cid is not None and cid != company_id:
                continue   # skip challenges scoped to other companies
            active_challenges.append({
                "id":           doc.id,
                "title":        d.get("title"),
                "description":  d.get("description"),
                "type":         d.get("type"),
                "target":       d.get("target"),
                "pointsReward": d.get("points_reward"),
                "endsAt":       d.get("ends_at"),
            })
    except Exception as e:
        print(f"[employer_gamification] challenges error: {e}")

    return {
        "companyId":         company_id,
        "totalPlayers":      n,
        "activePlayers7d":   active_7d,
        "avgPoints":         round(total_pts / n, 1),
        "avgLevel":          round(total_lvl / n, 2),
        "avgStreak":         round(total_streak / n, 2),
        "badgeDistribution": badge_dist,
        "leaderboard":       leaderboard_raw[:20],   # top 20 only
        "activeChallenges":  active_challenges,
    }
