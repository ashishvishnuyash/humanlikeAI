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
  POST /api/admin/employers/{uid}/deactivate  → Disable employer + all their Firebase Auth
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

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from firebase_admin import auth as fb_auth, firestore as admin_firestore
from firebase_config import get_db, firebaseConfig
from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from pydantic import BaseModel, EmailStr

from routers.auth import get_super_admin_user, RegisterRequest, RegisterResponse

router = APIRouter(prefix="/admin", tags=["Super Admin"])

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


def _safe_profile(data: dict, uid: str) -> dict:
    """Sanitise a Firestore user dict into a clean response dict."""
    return {
        "uid":            uid,
        "email":          data.get("email", ""),
        "firstName":      data.get("first_name", ""),
        "lastName":       data.get("last_name", ""),
        "displayName":    data.get("display_name", ""),
        "role":           data.get("role", "unknown"),
        "companyId":      data.get("company_id"),
        "companyName":    data.get("company_name"),
        "department":     data.get("department"),
        "position":       data.get("position"),
        "phone":          data.get("phone"),
        "jobTitle":       data.get("job_title"),
        "hierarchyLevel": data.get("hierarchy_level", 0),
        "isActive":       data.get("is_active", True),
        "createdAt":      _ts_to_iso(data.get("created_at") or data.get("registered_at")),
        "updatedAt":      _ts_to_iso(data.get("updated_at")),
        "createdBy":      data.get("created_by"),
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
async def platform_stats(_: dict = Depends(get_super_admin_user)):
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    try:
        all_users = list(db.collection("users").stream())
    except Exception as e:
        raise HTTPException(500, f"Query failed: {e}")

    now = datetime.now(timezone.utc)
    thirty_ago = now.timestamp() - (30 * 86400)

    total_employers = total_employees = active = inactive = recent = 0
    roles: Dict[str, int] = {}

    for doc in all_users:
        d = doc.to_dict()
        role = d.get("role", "unknown")
        roles[role] = roles.get(role, 0) + 1

        if role == "employer":
            total_employers += 1
        elif role != "super_admin":
            total_employees += 1

        if d.get("is_active", True):
            active += 1
        else:
            inactive += 1

        ts = d.get("created_at") or d.get("registered_at")
        if ts and hasattr(ts, "timestamp") and ts.timestamp() >= thirty_ago:
            recent += 1

    try:
        total_companies = len(list(db.collection("companies").stream()))
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
):
    from routers.auth import _create_employer_account
    return await _create_employer_account(req)


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
):
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    try:
        docs = db.collection("users").where("role", "==", "employer").stream()
    except Exception as e:
        raise HTTPException(500, f"Query failed: {e}")

    result = []
    for doc in docs:
        d = doc.to_dict()
        if not include_inactive and not d.get("is_active", True):
            continue
        if search:
            term = search.lower().strip()
            searchable = " ".join([
                f"{d.get('first_name', '')} {d.get('last_name', '')}".lower(),
                d.get("email", "").lower(),
                d.get("company_name", "").lower(),
            ])
            if term not in searchable:
                continue
        result.append(_safe_profile(d, doc.id))

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
async def get_employer(uid: str, _: dict = Depends(get_super_admin_user)):
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")
    doc = db.collection("users").document(uid).get()
    if not doc.exists:
        raise HTTPException(404, "Employer not found.")
    d = doc.to_dict()
    if d.get("role") != "employer":
        raise HTTPException(400, "User is not an employer.")
    return _safe_profile(d, uid)


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
):
    return await _update_user(uid, req, expected_role="employer")


# ─── Employers: Deactivate / Reactivate ───────────────────────────────────────

@router.post("/employers/{uid}/deactivate", response_model=MutationResponse, summary="Deactivate Employer")
async def deactivate_employer(uid: str, _: dict = Depends(get_super_admin_user)):
    return await _set_user_active(uid, active=False, role_check="employer")


@router.post("/employers/{uid}/reactivate", response_model=MutationResponse, summary="Reactivate Employer")
async def reactivate_employer(uid: str, _: dict = Depends(get_super_admin_user)):
    return await _set_user_active(uid, active=True, role_check="employer")


# ─── Employers: Hard Delete ───────────────────────────────────────────────────

@router.delete(
    "/employers/{uid}",
    response_model=MutationResponse,
    summary="Hard-Delete Employer",
    description=(
        "⚠️ **Irreversible.** Deletes the employer's Firestore profile, company document, "
        "and Firebase Auth account. Employees are NOT deleted — their company_id is preserved."
    ),
)
async def delete_employer(uid: str, _: dict = Depends(get_super_admin_user)):
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    doc = db.collection("users").document(uid).get()
    if not doc.exists:
        raise HTTPException(404, "Employer not found.")
    d = doc.to_dict()
    if d.get("role") != "employer":
        raise HTTPException(400, "User is not an employer.")

    company_id = d.get("company_id")
    errors: List[str] = []

    # 1. Delete user document
    try:
        db.collection("users").document(uid).delete()
    except Exception as e:
        errors.append(f"user_doc: {e}")

    # 2. Delete company document
    if company_id:
        try:
            db.collection("companies").document(company_id).delete()
        except Exception as e:
            errors.append(f"company_doc: {e}")

    # 3. Delete Firebase Auth
    try:
        fb_auth.delete_user(uid)
    except Exception as e:
        errors.append(f"firebase_auth: {e}")

    return MutationResponse(
        success=not errors,
        message="Employer deleted." + (f" Warnings: {errors}" if errors else ""),
    )


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
):
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    try:
        if company_id:
            docs = db.collection("users").where("company_id", "==", company_id).stream()
        else:
            docs = db.collection("users").stream()
    except Exception as e:
        raise HTTPException(500, f"Query failed: {e}")

    result = []
    for doc in docs:
        d = doc.to_dict()
        role = d.get("role", "unknown")
        if role in ("employer", "super_admin"):
            continue
        if not include_inactive and not d.get("is_active", True):
            continue
        if role_filter and role != role_filter:
            continue
        if search:
            term = search.lower().strip()
            searchable = " ".join([
                f"{d.get('first_name', '')} {d.get('last_name', '')}".lower(),
                d.get("email", "").lower(),
                d.get("company_name", "").lower(),
                d.get("department", "").lower(),
            ])
            if term not in searchable:
                continue
        result.append(_safe_profile(d, doc.id))

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
async def admin_get_employee(uid: str, _: dict = Depends(get_super_admin_user)):
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")
    doc = db.collection("users").document(uid).get()
    if not doc.exists:
        raise HTTPException(404, "User not found.")
    d = doc.to_dict()
    if d.get("role") in ("employer", "super_admin"):
        raise HTTPException(400, "Use /employers endpoint for employer/admin profiles.")
    return _safe_profile(d, uid)


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
):
    return await _update_user(uid, req, expected_role=None)


# ─── Employees: Hard Delete ───────────────────────────────────────────────────

@router.delete(
    "/employees/{uid}",
    response_model=MutationResponse,
    summary="Hard-Delete Employee",
)
async def admin_delete_employee(uid: str, _: dict = Depends(get_super_admin_user)):
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    doc = db.collection("users").document(uid).get()
    if not doc.exists:
        raise HTTPException(404, "User not found.")

    d = doc.to_dict()
    if d.get("role") in ("employer", "super_admin"):
        raise HTTPException(400, "Use the employer delete endpoint for employer accounts.")

    errors: List[str] = []

    # Clean up manager's direct_reports
    manager_id = d.get("manager_id")
    if manager_id:
        try:
            from google.cloud.firestore_v1 import ArrayRemove
            db.collection("users").document(manager_id).update({
                "direct_reports": ArrayRemove([uid]),
                "updated_at":     SERVER_TIMESTAMP,
            })
        except Exception as e:
            errors.append(f"direct_reports: {e}")

    # Reassign this user's direct reports upward
    for report_uid in d.get("direct_reports", []):
        try:
            db.collection("users").document(report_uid).update({
                "manager_id": manager_id,
                "updated_at": SERVER_TIMESTAMP,
            })
        except Exception as e:
            errors.append(f"reassign_{report_uid}: {e}")

    # Decrement company count
    company_id = d.get("company_id")
    if company_id:
        try:
            db.collection("companies").document(company_id).update({
                "employee_count": _Increment(-1),
                "updated_at":     SERVER_TIMESTAMP,
            })
        except Exception as e:
            errors.append(f"count: {e}")

    # Delete Firestore doc
    try:
        db.collection("users").document(uid).delete()
    except Exception as e:
        errors.append(f"firestore: {e}")

    # Delete Firebase Auth
    try:
        fb_auth.delete_user(uid)
    except Exception as e:
        errors.append(f"firebase_auth: {e}")

    return MutationResponse(
        success=not errors,
        message="Employee deleted." + (f" Warnings: {errors}" if errors else ""),
    )


# ─── Companies: List ─────────────────────────────────────────────────────────

@router.get("/companies", summary="List All Companies")
async def list_all_companies(
    search: Optional[str] = Query(None, description="Search by company name or industry"),
    page:   int           = Query(1, ge=1),
    limit:  int           = Query(20, ge=1, le=100),
    _: dict = Depends(get_super_admin_user),
):
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    try:
        docs = list(db.collection("companies").stream())
    except Exception as e:
        raise HTTPException(500, f"Query failed: {e}")

    companies = []
    for doc in docs:
        d = doc.to_dict()
        if search:
            term = search.lower().strip()
            searchable = " ".join([
                d.get("name", "").lower(),
                d.get("industry", "").lower(),
            ])
            if term not in searchable:
                continue
        companies.append({
            "id":            doc.id,
            "name":          d.get("name"),
            "industry":      d.get("industry"),
            "size":          d.get("size"),
            "ownerId":       d.get("owner_id"),
            "employeeCount": d.get("employee_count", 0),
            "website":       d.get("website"),
            "description":   d.get("description"),
            "createdAt":     _ts_to_iso(d.get("created_at")),
            "updatedAt":     _ts_to_iso(d.get("updated_at")),
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
async def admin_get_company(company_id: str, _: dict = Depends(get_super_admin_user)):
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")
    doc = db.collection("companies").document(company_id).get()
    if not doc.exists:
        raise HTTPException(404, "Company not found.")
    d = doc.to_dict()
    d["createdAt"] = _ts_to_iso(d.pop("created_at", None))
    d["updatedAt"] = _ts_to_iso(d.pop("updated_at", None))
    d["ownerId"] = d.pop("owner_id", None)
    d["employeeCount"] = d.pop("employee_count", 0)
    d["logoUrl"] = d.pop("logo_url", None)
    return d


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
):
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    doc = db.collection("companies").document(company_id).get()
    if not doc.exists:
        raise HTTPException(404, "Company not found.")

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
        raise HTTPException(400, "No fields to update.")

    db.collection("companies").document(company_id).update(updates)

    # Sync name change to all employees in this company
    if "name" in updates:
        try:
            emp_docs = db.collection("users").where("company_id", "==", company_id).stream()
            for emp_doc in emp_docs:
                db.collection("users").document(emp_doc.id).update({
                    "company_name": updates["name"],
                    "updated_at":   SERVER_TIMESTAMP,
                })
        except Exception as e:
            print(f"[admin] company name sync error: {e}")

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
):
    if len(req.new_password) < 8:
        raise HTTPException(400, "New password must be at least 8 characters.")

    try:
        fb_auth.update_user(uid, password=req.new_password)
    except Exception as e:
        raise HTTPException(500, f"Password reset failed: {e}")

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
):
    if len(req.new_password) < 8:
        raise HTTPException(400, "New password must be at least 8 characters.")
    if req.current_password == req.new_password:
        raise HTTPException(400, "New password must differ from current.")

    api_key = firebaseConfig.get("apiKey")
    if not api_key:
        raise HTTPException(500, "Firebase API key not configured.")

    # Re-authenticate
    verify_url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}"
    async with httpx.AsyncClient() as client:
        resp = await client.post(verify_url, json={
            "email":             admin.get("email"),
            "password":          req.current_password,
            "returnSecureToken": False,
        })

    if resp.status_code != 200:
        raise HTTPException(401, "Current password is incorrect.")

    uid = admin.get("id")
    try:
        fb_auth.update_user(uid, password=req.new_password)
    except Exception as e:
        raise HTTPException(500, f"Password update failed: {e}")

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
) -> MutationResponse:
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    doc = db.collection("users").document(uid).get()
    if not doc.exists:
        raise HTTPException(404, "User not found.")

    d = doc.to_dict()
    if expected_role and d.get("role") != expected_role:
        raise HTTPException(400, f"User role mismatch. Expected '{expected_role}'.")
    if d.get("role") == "super_admin":
        raise HTTPException(403, "Cannot modify super admin profile via this endpoint.")

    updates: Dict[str, Any] = {"updated_at": SERVER_TIMESTAMP}
    updated_fields: List[str] = []

    field_map = {
        "firstName":      "first_name",
        "lastName":       "last_name",
        "phone":          "phone",
        "department":     "department",
        "position":       "position",
        "jobTitle":       "job_title",
        "hierarchyLevel": "hierarchy_level",
        "isActive":       "is_active",
        "role":           "role",
    }
    for req_field, db_field in field_map.items():
        val = getattr(req, req_field, None)
        if val is not None:
            if req_field == "role" and val not in VALID_ROLES:
                raise HTTPException(400, f"Invalid role '{val}'.")
            updates[db_field] = val
            updated_fields.append(req_field)

    if len(updates) == 1:
        raise HTTPException(400, "No valid fields to update.")

    # Sync name
    if "first_name" in updates or "last_name" in updates:
        fn = updates.get("first_name", d.get("first_name", ""))
        ln = updates.get("last_name",  d.get("last_name",  ""))
        updates["display_name"] = f"{fn} {ln}"
        try:
            fb_auth.update_user(uid, display_name=updates["display_name"])
        except Exception as e:
            print(f"[admin] display_name sync error: {e}")

    # Sync is_active → Firebase Auth disabled flag
    if "is_active" in updates:
        try:
            fb_auth.update_user(uid, disabled=not updates["is_active"])
        except Exception as e:
            print(f"[admin] is_active sync error: {e}")

    db.collection("users").document(uid).update(updates)
    return MutationResponse(
        success=True,
        message="User updated successfully.",
        updatedFields=updated_fields,
    )


async def _set_user_active(uid: str, active: bool, role_check: str) -> MutationResponse:
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    doc = db.collection("users").document(uid).get()
    if not doc.exists:
        raise HTTPException(404, "User not found.")

    d = doc.to_dict()
    if d.get("role") != role_check:
        raise HTTPException(400, f"User is not a {role_check}.")

    try:
        fb_auth.update_user(uid, disabled=not active)
        # Revoke all existing tokens when deactivating so active sessions end immediately
        if not active:
            fb_auth.revoke_refresh_tokens(uid)
    except Exception as e:
        print(f"[admin] Firebase Auth toggle error: {e}")

    db.collection("users").document(uid).update({
        "is_active":  active,
        "updated_at": SERVER_TIMESTAMP,
    })

    action = "reactivated" if active else "deactivated"
    return MutationResponse(success=True, message=f"User {action} successfully.")
