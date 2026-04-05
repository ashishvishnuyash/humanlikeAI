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

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from firebase_admin import auth as fb_auth, firestore as admin_firestore
from firebase_config import get_db, firebaseConfig
from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from pydantic import BaseModel, EmailStr

from routers.auth import get_current_user, get_employer_user

router = APIRouter(prefix="/employer", tags=["Employer CRUD"])

_Increment = admin_firestore.firestore.Increment


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
