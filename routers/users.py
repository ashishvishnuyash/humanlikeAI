"""
Employee Management — Employer-Only APIs
=========================================
  POST   /api/employees/create          → Create employee (employer/hr only)
  GET    /api/employees                 → List all employees in company (employer/hr only)
  GET    /api/employees/{uid}           → Get single employee profile (employer/hr only)
  PATCH  /api/employees/{uid}           → Update employee details (employer/hr only)
  POST   /api/employees/{uid}/deactivate → Soft-deactivate employee (employer/hr only)
  POST   /api/employees/{uid}/reactivate → Reactivate employee (employer/hr only)

  GET    /api/hierarchy/test             → Hierarchy test (any authenticated user)
  POST   /api/hierarchy/test             → Hierarchy access check (any authenticated user)

All write operations require the caller to be role: employer or hr AND share the same company_id.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from firebase_admin import auth as fb_auth, firestore as admin_firestore
from firebase_config import get_db
from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from pydantic import BaseModel, EmailStr

from routers.auth import get_current_user, get_employer_user

# Firestore atomic increment helper
_firestore_increment = admin_firestore.firestore.Increment


router = APIRouter(tags=["Employees"])


# ─── Schemas ─────────────────────────────────────────────────────────────────

class CreateEmployeeRequest(BaseModel):
    email: EmailStr
    password: str
    firstName: str
    lastName: str
    role: str = "employee"          # employee / manager / hr
    department: Optional[str] = ""
    position: Optional[str] = ""
    phone: Optional[str] = None
    managerId: Optional[str] = None
    hierarchyLevel: Optional[int] = 1
    permissions: Optional[Dict[str, bool]] = {}
    sendWelcomeEmail: Optional[bool] = True


class UpdateEmployeeRequest(BaseModel):
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    department: Optional[str] = None
    position: Optional[str] = None
    phone: Optional[str] = None
    managerId: Optional[str] = None
    hierarchyLevel: Optional[int] = None
    role: Optional[str] = None
    permissions: Optional[Dict[str, bool]] = None


class EmployeeProfile(BaseModel):
    uid: str
    email: str
    firstName: str
    lastName: str
    role: str
    department: Optional[str]
    position: Optional[str]
    phone: Optional[str]
    companyId: str
    managerId: Optional[str]
    hierarchyLevel: int
    isActive: bool
    permissions: Dict[str, bool]
    createdAt: Optional[str]
    createdBy: Optional[str]        # uid of the employer who created them


class CreateEmployeeResponse(BaseModel):
    success: bool
    uid: str
    message: str


class ListEmployeesResponse(BaseModel):
    success: bool
    employees: List[EmployeeProfile]
    total: int
    companyId: str


class UpdateEmployeeResponse(BaseModel):
    success: bool
    uid: str
    message: str
    updatedFields: List[str]


class DeactivateResponse(BaseModel):
    success: bool
    uid: str
    message: str


# ─── Helpers ─────────────────────────────────────────────────────────────────

VALID_EMPLOYEE_ROLES = {"employee", "manager", "hr"}

def _ts_to_iso(ts) -> Optional[str]:
    if ts is None:
        return None
    if hasattr(ts, "timestamp"):
        return datetime.fromtimestamp(ts.timestamp(), tz=timezone.utc).isoformat()
    if isinstance(ts, datetime):
        return ts.isoformat()
    return str(ts)


def _build_profile(uid: str, data: dict) -> EmployeeProfile:
    perms = {k: data.get(k, False) for k in (
        "can_view_team_reports",
        "can_manage_employees",
        "can_approve_leaves",
        "can_view_analytics",
        "can_create_programs",
        "skip_level_access",
    )}
    return EmployeeProfile(
        uid=uid,
        email=data.get("email", ""),
        firstName=data.get("first_name", ""),
        lastName=data.get("last_name", ""),
        role=data.get("role", "employee"),
        department=data.get("department"),
        position=data.get("position"),
        phone=data.get("phone"),
        companyId=data.get("company_id", ""),
        managerId=data.get("manager_id"),
        hierarchyLevel=data.get("hierarchy_level", 1),
        isActive=data.get("is_active", True),
        permissions=perms,
        createdAt=_ts_to_iso(data.get("created_at")),
        createdBy=data.get("created_by"),
    )


def _default_permissions(role: str) -> Dict[str, bool]:
    """Return safe default permissions for a given role."""
    base = {
        "can_view_team_reports": False,
        "can_manage_employees":  False,
        "can_approve_leaves":    False,
        "can_view_analytics":    False,
        "can_create_programs":   False,
        "skip_level_access":     False,
    }
    if role == "manager":
        base["can_view_team_reports"] = True
        base["can_approve_leaves"]    = True
        base["can_view_analytics"]    = True
    elif role == "hr":
        base["can_view_team_reports"] = True
        base["can_manage_employees"]  = True
        base["can_approve_leaves"]    = True
        base["can_view_analytics"]    = True
        base["can_create_programs"]   = True
    return base


# ─── Create Employee (Employer-Only) ─────────────────────────────────────────

@router.post(
    "/employees/create",
    response_model=CreateEmployeeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Employee",
    description=(
        "**Employer / HR only.** Creates a new employee account under the caller's company. "
        "The employee account is automatically linked to the employer's company_id. "
        "Allowed roles for new employee: employee, manager, hr."
    ),
)
async def create_employee(
    req: CreateEmployeeRequest,
    employer: dict = Depends(get_employer_user),
):
    # ── Validation ────────────────────────────────────────────────────────
    if len(req.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")

    if req.role not in VALID_EMPLOYEE_ROLES:
        raise HTTPException(
            400,
            f"Invalid role '{req.role}'. Allowed: {sorted(VALID_EMPLOYEE_ROLES)}.",
        )

    employer_company_id = employer.get("company_id")
    if not employer_company_id:
        raise HTTPException(400, "Employer profile is missing a company_id.")

    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    # ── Validate managerId belongs to same company ─────────────────────────
    manager_id = req.managerId if req.managerId and req.managerId != "none" else None
    if manager_id:
        mgr_doc = db.collection("users").document(manager_id).get()
        if not mgr_doc.exists or mgr_doc.to_dict().get("company_id") != employer_company_id:
            raise HTTPException(
                400,
                "Specified managerId does not belong to your company.",
            )

    # ── Create Firebase Auth account ───────────────────────────────────────
    try:
        fb_user = fb_auth.create_user(
            email=req.email,
            password=req.password,
            display_name=f"{req.firstName} {req.lastName}",
        )
    except Exception as e:
        err = str(e)
        if "EMAIL_EXISTS" in err or "email-already-exists" in err:
            raise HTTPException(409, "An account with this email already exists.")
        raise HTTPException(500, f"Firebase Auth error: {err}")

    uid = fb_user.uid

    # ── Build Firestore document ───────────────────────────────────────────
    default_perms = _default_permissions(req.role)
    # Employer can override specific permissions
    if req.permissions:
        default_perms.update(req.permissions)

    doc_data: Dict[str, Any] = {
        "id": uid,
        "email": req.email,
        "first_name": req.firstName,
        "last_name": req.lastName,
        "display_name": f"{req.firstName} {req.lastName}",
        "role": req.role,
        "department": req.department,
        "position": req.position,
        "phone": req.phone,
        "company_id": employer_company_id,
        "company_name": employer.get("company_name", ""),
        "manager_id": manager_id,
        "hierarchy_level": req.hierarchyLevel,
        "direct_reports": [],
        "reporting_chain": [],
        "is_active": True,
        "created_by": employer.get("id"),          # audit trail — employer uid
        "created_at": SERVER_TIMESTAMP,
        "updated_at": SERVER_TIMESTAMP,
        **default_perms,
    }

    try:
        db.collection("users").document(uid).set(doc_data)

        # Update company employee_count atomically
        db.collection("companies").document(employer_company_id).update({
            "employee_count": _firestore_increment(1),
            "updated_at": SERVER_TIMESTAMP,
        })
    except Exception as e:
        # Rollback Firebase Auth user if Firestore write fails
        try:
            fb_auth.delete_user(uid)
        except Exception:
            pass
        print(f"[users] create_employee Firestore error: {e}")
        raise HTTPException(500, "Failed to save employee profile. Account creation rolled back.")

    return CreateEmployeeResponse(
        success=True,
        uid=uid,
        message=f"Employee '{req.firstName} {req.lastName}' created successfully.",
    )


# ─── List Employees ───────────────────────────────────────────────────────────

@router.get(
    "/employees",
    response_model=ListEmployeesResponse,
    summary="List All Employees",
    description="**Employer / HR only.** Returns all employees in the caller's company.",
)
async def list_employees(
    include_inactive: bool = Query(False, description="Include deactivated accounts"),
    department: Optional[str] = Query(None),
    role_filter: Optional[str] = Query(None, alias="role"),
    employer: dict = Depends(get_employer_user),
):
    company_id = employer.get("company_id")
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    try:
        query = db.collection("users").where("company_id", "==", company_id)
        docs  = query.stream()
    except Exception as e:
        raise HTTPException(500, f"Database query failed: {e}")

    employees: List[EmployeeProfile] = []
    for doc in docs:
        data = doc.to_dict()
        uid  = doc.id

        # Skip the employer themselves
        if data.get("role") == "employer":
            continue
        # Filter inactive
        if not include_inactive and not data.get("is_active", True):
            continue
        # Filter by department
        if department and data.get("department", "").lower() != department.lower():
            continue
        # Filter by role
        if role_filter and data.get("role") != role_filter:
            continue

        employees.append(_build_profile(uid, data))

    return ListEmployeesResponse(
        success=True,
        employees=employees,
        total=len(employees),
        companyId=company_id,
    )


# ─── Get Single Employee ──────────────────────────────────────────────────────

@router.get(
    "/employees/{uid}",
    response_model=EmployeeProfile,
    summary="Get Employee Profile",
    description="**Employer / HR only.** Fetch a single employee's profile.",
)
async def get_employee(
    uid: str,
    employer: dict = Depends(get_employer_user),
):
    company_id = employer.get("company_id")
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    doc = db.collection("users").document(uid).get()
    if not doc.exists:
        raise HTTPException(404, "Employee not found.")

    data = doc.to_dict()
    if data.get("company_id") != company_id:
        raise HTTPException(403, "Employee does not belong to your company.")

    if data.get("role") == "employer":
        raise HTTPException(403, "Cannot view employer profiles via this endpoint.")

    return _build_profile(uid, data)


# ─── Update Employee ──────────────────────────────────────────────────────────

@router.patch(
    "/employees/{uid}",
    response_model=UpdateEmployeeResponse,
    summary="Update Employee",
    description="**Employer / HR only.** Update employee details. Only provided fields are changed.",
)
async def update_employee(
    uid: str,
    req: UpdateEmployeeRequest,
    employer: dict = Depends(get_employer_user),
):
    company_id = employer.get("company_id")
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    doc = db.collection("users").document(uid).get()
    if not doc.exists:
        raise HTTPException(404, "Employee not found.")

    data = doc.to_dict()
    if data.get("company_id") != company_id:
        raise HTTPException(403, "Employee does not belong to your company.")

    if data.get("role") == "employer":
        raise HTTPException(403, "Cannot modify employer accounts via this endpoint.")

    # Build update payload — only set fields that were explicitly provided
    updates: Dict[str, Any] = {"updated_at": SERVER_TIMESTAMP}
    updated_fields: List[str] = []

    field_map = {
        "firstName":     "first_name",
        "lastName":      "last_name",
        "department":    "department",
        "position":      "position",
        "phone":         "phone",
        "hierarchyLevel":"hierarchy_level",
        "role":          "role",
    }
    for req_field, db_field in field_map.items():
        val = getattr(req, req_field, None)
        if val is not None:
            if req_field == "role" and val not in VALID_EMPLOYEE_ROLES:
                raise HTTPException(400, f"Invalid role '{val}'.")
            updates[db_field] = val
            updated_fields.append(db_field)

    # managerId special handling
    if req.managerId is not None:
        manager_id = req.managerId if req.managerId != "none" else None
        if manager_id:
            mgr_doc = db.collection("users").document(manager_id).get()
            if not mgr_doc.exists or mgr_doc.to_dict().get("company_id") != company_id:
                raise HTTPException(400, "Specified managerId does not belong to your company.")
        updates["manager_id"] = manager_id
        updated_fields.append("manager_id")

    # Permissions override
    if req.permissions:
        updates.update(req.permissions)
        updated_fields.extend(list(req.permissions.keys()))

    # Rebuild display_name if name changed
    if "first_name" in updates or "last_name" in updates:
        fn = updates.get("first_name", data.get("first_name", ""))
        ln = updates.get("last_name",  data.get("last_name",  ""))
        updates["display_name"] = f"{fn} {ln}"
        # Sync to Firebase Auth
        try:
            fb_auth.update_user(uid, display_name=updates["display_name"])
        except Exception as e:
            print(f"[users] Firebase display_name sync failed: {e}")

    if len(updates) == 1:  # only updated_at
        raise HTTPException(400, "No valid fields to update.")

    db.collection("users").document(uid).update(updates)

    return UpdateEmployeeResponse(
        success=True,
        uid=uid,
        message="Employee updated successfully.",
        updatedFields=updated_fields,
    )


# ─── Deactivate / Reactivate ──────────────────────────────────────────────────

@router.post(
    "/employees/{uid}/deactivate",
    response_model=DeactivateResponse,
    summary="Deactivate Employee",
    description="**Employer / HR only.** Soft-deactivates an employee (does not delete the account).",
)
async def deactivate_employee(
    uid: str,
    employer: dict = Depends(get_employer_user),
):
    return await _set_employee_active(uid, active=False, employer=employer)


@router.post(
    "/employees/{uid}/reactivate",
    response_model=DeactivateResponse,
    summary="Reactivate Employee",
    description="**Employer / HR only.** Re-activates a previously deactivated employee.",
)
async def reactivate_employee(
    uid: str,
    employer: dict = Depends(get_employer_user),
):
    return await _set_employee_active(uid, active=True, employer=employer)


async def _set_employee_active(uid: str, active: bool, employer: dict) -> DeactivateResponse:
    company_id = employer.get("company_id")
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    doc = db.collection("users").document(uid).get()
    if not doc.exists:
        raise HTTPException(404, "Employee not found.")

    data = doc.to_dict()
    if data.get("company_id") != company_id:
        raise HTTPException(403, "Employee does not belong to your company.")
    if data.get("role") == "employer":
        raise HTTPException(403, "Cannot modify employer accounts.")

    # Sync disabled status to Firebase Auth
    try:
        fb_auth.update_user(uid, disabled=not active)
    except Exception as e:
        print(f"[users] Firebase Auth update_user error: {e}")

    db.collection("users").document(uid).update({
        "is_active":  active,
        "updated_at": SERVER_TIMESTAMP,
    })

    action = "reactivated" if active else "deactivated"
    return DeactivateResponse(
        success=True,
        uid=uid,
        message=f"Employee {action} successfully.",
    )


# ─── Hard Delete Employee ─────────────────────────────────────────────────────

class DeleteEmployeeResponse(BaseModel):
    success: bool
    uid: str
    message: str


@router.delete(
    "/employees/{uid}",
    response_model=DeleteEmployeeResponse,
    summary="Permanently Delete Employee",
    description=(
        "**Employer only (not HR).** Permanently deletes a Firebase Auth account and "
        "Firestore profile. This action is irreversible. "
        "Use `POST /employees/{uid}/deactivate` for a reversible soft-delete."
    ),
)
async def delete_employee(
    uid: str,
    employer: dict = Depends(get_employer_user),
):
    if employer.get("role") != "employer":
        raise HTTPException(403, "Only the company owner can permanently delete employee accounts.")

    company_id = employer.get("company_id")
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    doc = db.collection("users").document(uid).get()
    if not doc.exists:
        raise HTTPException(404, "Employee not found.")

    data = doc.to_dict()
    if data.get("company_id") != company_id:
        raise HTTPException(403, "Employee does not belong to your company.")
    if data.get("role") == "employer":
        raise HTTPException(403, "Cannot delete the employer owner account via this endpoint.")

    errors: List[str] = []

    # 1. Remove from manager's direct_reports list
    manager_id = data.get("manager_id")
    if manager_id:
        try:
            from google.cloud.firestore_v1 import ArrayRemove
            db.collection("users").document(manager_id).update({
                "direct_reports": ArrayRemove([uid]),
                "updated_at":     SERVER_TIMESTAMP,
            })
        except Exception as e:
            errors.append(f"direct_reports_cleanup: {e}")

    # 2. Reassign this employee's direct reports to their manager
    direct_reports: List[str] = data.get("direct_reports", [])
    for report_uid in direct_reports:
        try:
            db.collection("users").document(report_uid).update({
                "manager_id": manager_id,   # could be None (becomes top-level)
                "updated_at": SERVER_TIMESTAMP,
            })
        except Exception as e:
            errors.append(f"reassign_{report_uid}: {e}")

    # 3. Delete Firestore document
    try:
        db.collection("users").document(uid).delete()
    except Exception as e:
        errors.append(f"firestore_delete: {e}")

    # 4. Decrement company employee_count
    try:
        db.collection("companies").document(company_id).update({
            "employee_count": _firestore_increment(-1),
            "updated_at":     SERVER_TIMESTAMP,
        })
    except Exception as e:
        errors.append(f"count_decrement: {e}")

    # 5. Delete Firebase Auth account
    try:
        fb_auth.delete_user(uid)
    except Exception as e:
        errors.append(f"firebase_auth: {e}")

    if errors:
        print(f"[users] delete_employee partial errors for {uid}: {errors}")

    return DeleteEmployeeResponse(
        success=True,
        uid=uid,
        message=(
            f"Employee deleted permanently."
            + (f" Warnings: {errors}" if errors else "")
        ),
    )


# ─── Bulk Create Employees ────────────────────────────────────────────────────

class BulkCreateItem(BaseModel):
    email: EmailStr
    password: str
    firstName: str
    lastName: str
    role: str = "employee"
    department: Optional[str] = ""
    position: Optional[str] = ""
    phone: Optional[str] = None
    managerId: Optional[str] = None
    hierarchyLevel: Optional[int] = 1


class BulkCreateResult(BaseModel):
    email: str
    success: bool
    uid: Optional[str] = None
    error: Optional[str] = None


class BulkCreateResponse(BaseModel):
    success: bool
    created: int
    failed: int
    results: List[BulkCreateResult]
    companyId: str


@router.post(
    "/employees/bulk-create",
    response_model=BulkCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Bulk Create Employees",
    description=(
        "**Employer / HR only.** Create up to 50 employees in a single request. "
        "Each item is processed independently — partial success is allowed and reported."
    ),
)
async def bulk_create_employees(
    employees: List[BulkCreateItem],
    employer: dict = Depends(get_employer_user),
):
    if not employees:
        raise HTTPException(400, "Employee list cannot be empty.")
    if len(employees) > 50:
        raise HTTPException(400, "Cannot create more than 50 employees per request.")

    company_id   = employer.get("company_id")
    company_name = employer.get("company_name", "")
    creator_uid  = employer.get("id")
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    results: List[BulkCreateResult] = []
    created = 0
    failed  = 0

    for item in employees:
        # Basic validation
        if len(item.password) < 6:
            results.append(BulkCreateResult(
                email=item.email, success=False, error="Password too short (min 6 chars)."))
            failed += 1
            continue

        if item.role not in VALID_EMPLOYEE_ROLES:
            results.append(BulkCreateResult(
                email=item.email, success=False, error=f"Invalid role '{item.role}'."))
            failed += 1
            continue

        try:
            fb_user = fb_auth.create_user(
                email=item.email,
                password=item.password,
                display_name=f"{item.firstName} {item.lastName}",
            )
            uid = fb_user.uid
        except Exception as e:
            err = str(e)
            if "EMAIL_EXISTS" in err or "email-already-exists" in err:
                err = "Email already exists."
            results.append(BulkCreateResult(email=item.email, success=False, error=err))
            failed += 1
            continue

        manager_id = item.managerId if item.managerId and item.managerId != "none" else None
        perms = _default_permissions(item.role)
        doc_data: Dict[str, Any] = {
            "id":             uid,
            "email":          item.email,
            "first_name":     item.firstName,
            "last_name":      item.lastName,
            "display_name":   f"{item.firstName} {item.lastName}",
            "role":           item.role,
            "department":     item.department,
            "position":       item.position,
            "phone":          item.phone,
            "company_id":     company_id,
            "company_name":   company_name,
            "manager_id":     manager_id,
            "hierarchy_level":item.hierarchyLevel,
            "direct_reports": [],
            "reporting_chain":[],
            "is_active":      True,
            "created_by":     creator_uid,
            "created_at":     SERVER_TIMESTAMP,
            "updated_at":     SERVER_TIMESTAMP,
            **perms,
        }

        try:
            db.collection("users").document(uid).set(doc_data)
            created += 1
            results.append(BulkCreateResult(email=item.email, success=True, uid=uid))
        except Exception as e:
            # Rollback auth if Firestore write fails
            try:
                fb_auth.delete_user(uid)
            except Exception:
                pass
            results.append(BulkCreateResult(
                email=item.email, success=False, error=f"Firestore write failed: {e}"))
            failed += 1

    # Update company employee_count once at end
    if created > 0:
        try:
            db.collection("companies").document(company_id).update({
                "employee_count": _firestore_increment(created),
                "updated_at":     SERVER_TIMESTAMP,
            })
        except Exception as e:
            print(f"[users] bulk_create count update error: {e}")

    return BulkCreateResponse(
        success=failed == 0,
        created=created,
        failed=failed,
        results=results,
        companyId=company_id,
    )


# ─── Transfer Employee ────────────────────────────────────────────────────────

class TransferEmployeeRequest(BaseModel):
    newManagerId: Optional[str] = None      # None to make top-level
    newDepartment: Optional[str] = None
    newPosition: Optional[str] = None
    newHierarchyLevel: Optional[int] = None


class TransferEmployeeResponse(BaseModel):
    success: bool
    uid: str
    message: str
    changes: Dict[str, Any]


@router.put(
    "/employees/{uid}/transfer",
    response_model=TransferEmployeeResponse,
    summary="Transfer / Reassign Employee",
    description=(
        "**Employer / HR only.** Move an employee to a different manager, department, or position. "
        "Automatically updates the old manager's `direct_reports` list and adds to new manager's list."
    ),
)
async def transfer_employee(
    uid: str,
    req: TransferEmployeeRequest,
    employer: dict = Depends(get_employer_user),
):
    company_id = employer.get("company_id")
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    doc = db.collection("users").document(uid).get()
    if not doc.exists:
        raise HTTPException(404, "Employee not found.")

    data = doc.to_dict()
    if data.get("company_id") != company_id:
        raise HTTPException(403, "Employee does not belong to your company.")
    if data.get("role") == "employer":
        raise HTTPException(403, "Cannot transfer employer accounts.")

    old_manager_id = data.get("manager_id")
    new_manager_id = req.newManagerId if req.newManagerId and req.newManagerId != "none" else None

    # Validate new manager belongs to same company
    if new_manager_id:
        mgr_doc = db.collection("users").document(new_manager_id).get()
        if not mgr_doc.exists or mgr_doc.to_dict().get("company_id") != company_id:
            raise HTTPException(400, "New managerId does not belong to your company.")

    changes: Dict[str, Any] = {}
    updates: Dict[str, Any] = {"updated_at": SERVER_TIMESTAMP}

    if req.newManagerId is not None:      # explicit field provided (even if None)
        updates["manager_id"] = new_manager_id
        changes["manager_id"] = {"from": old_manager_id, "to": new_manager_id}

    if req.newDepartment is not None:
        updates["department"] = req.newDepartment
        changes["department"] = {"from": data.get("department"), "to": req.newDepartment}

    if req.newPosition is not None:
        updates["position"] = req.newPosition
        changes["position"] = {"from": data.get("position"), "to": req.newPosition}

    if req.newHierarchyLevel is not None:
        updates["hierarchy_level"] = req.newHierarchyLevel
        changes["hierarchy_level"] = {"from": data.get("hierarchy_level"), "to": req.newHierarchyLevel}

    if not changes:
        raise HTTPException(400, "No transfer fields provided.")

    # Apply main update
    db.collection("users").document(uid).update(updates)

    # ── Update direct_reports lists ───────────────────────────────────────
    if "manager_id" in changes:
        try:
            from google.cloud.firestore_v1 import ArrayRemove, ArrayUnion

            # Remove from old manager
            if old_manager_id:
                db.collection("users").document(old_manager_id).update({
                    "direct_reports": ArrayRemove([uid]),
                    "updated_at":     SERVER_TIMESTAMP,
                })

            # Add to new manager
            if new_manager_id:
                db.collection("users").document(new_manager_id).update({
                    "direct_reports": ArrayUnion([uid]),
                    "updated_at":     SERVER_TIMESTAMP,
                })
        except Exception as e:
            print(f"[users] transfer direct_reports update error: {e}")

    return TransferEmployeeResponse(
        success=True,
        uid=uid,
        message="Employee transferred successfully.",
        changes=changes,
    )


# ─── Employee Activity Summary ────────────────────────────────────────────────

class ActivitySummaryResponse(BaseModel):
    uid: str
    companyId: str
    totalCheckIns: int
    totalSessions: int
    lastActiveAt: Optional[str]
    avgMoodScore: Optional[float]
    avgStressLevel: Optional[float]
    riskLevel: Optional[str]
    sessionModalities: Dict[str, int]
    computedAt: str


@router.get(
    "/employees/{uid}/activity",
    response_model=ActivitySummaryResponse,
    summary="Employee Activity Summary",
    description=(
        "**Employer / HR only.** Returns aggregated activity stats for an employee — "
        "check-in count, session count, average mood/stress, last active date, and risk level. "
        "No raw message content or individual responses are ever returned."
    ),
)
async def get_employee_activity(
    uid: str,
    employer: dict = Depends(get_employer_user),
):
    company_id = employer.get("company_id")
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    doc = db.collection("users").document(uid).get()
    if not doc.exists:
        raise HTTPException(404, "Employee not found.")

    data = doc.to_dict()
    if data.get("company_id") != company_id:
        raise HTTPException(403, "Employee does not belong to your company.")
    if data.get("role") == "employer":
        raise HTTPException(403, "Cannot view employer activity via this endpoint.")

    now = datetime.now(timezone.utc)

    # ── Check-ins ─────────────────────────────────────────────────────────
    total_checkins = 0
    mood_sum = 0.0
    stress_sum = 0.0
    last_active_ts = None
    risk_counts: Dict[str, int] = {"low": 0, "medium": 0, "high": 0}

    try:
        ci_docs = db.collection("check_ins").where("user_id", "==", uid).stream()
        for ci in ci_docs:
            d = ci.to_dict()
            total_checkins += 1
            mood_sum   += float(d.get("mood_score", 0) or 0)
            stress_sum += float(d.get("stress_level", 0) or 0)
            ts = d.get("created_at")
            if ts and hasattr(ts, "timestamp"):
                if last_active_ts is None or ts.timestamp() > last_active_ts:
                    last_active_ts = ts.timestamp()
            risk = d.get("risk_level", "low")
            if risk in risk_counts:
                risk_counts[risk] += 1
    except Exception as e:
        print(f"[users] check_ins query error: {e}")

    # ── Sessions ──────────────────────────────────────────────────────────
    total_sessions = 0
    modalities: Dict[str, int] = {}

    try:
        sess_docs = db.collection("sessions").where("user_id", "==", uid).stream()
        for s in sess_docs:
            d = s.to_dict()
            total_sessions += 1
            mod = d.get("modality", "unknown")
            modalities[mod] = modalities.get(mod, 0) + 1
            ts = d.get("created_at")
            if ts and hasattr(ts, "timestamp"):
                if last_active_ts is None or ts.timestamp() > last_active_ts:
                    last_active_ts = ts.timestamp()
    except Exception as e:
        print(f"[users] sessions query error: {e}")

    avg_mood   = round(mood_sum   / total_checkins, 1) if total_checkins > 0 else None
    avg_stress = round(stress_sum / total_checkins, 1) if total_checkins > 0 else None

    # Dominant risk level
    dominant_risk = max(risk_counts, key=risk_counts.get) if total_checkins > 0 else None

    last_active_iso = (
        datetime.fromtimestamp(last_active_ts, tz=timezone.utc).isoformat()
        if last_active_ts
        else None
    )

    return ActivitySummaryResponse(
        uid=uid,
        companyId=company_id,
        totalCheckIns=total_checkins,
        totalSessions=total_sessions,
        lastActiveAt=last_active_iso,
        avgMoodScore=avg_mood,
        avgStressLevel=avg_stress,
        riskLevel=dominant_risk,
        sessionModalities=modalities,
        computedAt=now.isoformat(),
    )




class HierarchyTestPost(BaseModel):
    userId: str
    targetUserId: str
    companyId: str


class HierarchyTestGetResponse(BaseModel):
    success: bool
    userId: str
    companyId: str
    testType: str
    results: dict


class HierarchyTestPostResponse(BaseModel):
    success: bool
    canAccess: bool
    userId: str
    targetUserId: str
    message: str


@router.get(
    "/hierarchy/test",
    response_model=HierarchyTestGetResponse,
    dependencies=[Depends(get_current_user)],
)
async def test_hierarchy_get(userId: str, companyId: str, testType: str = "all"):
    return {
        "success": True,
        "userId": userId,
        "companyId": companyId,
        "testType": testType,
        "results": {"message": "Hierarchy tests migrated to Python stub."},
    }


@router.post(
    "/hierarchy/test",
    response_model=HierarchyTestPostResponse,
    dependencies=[Depends(get_current_user)],
)
async def test_hierarchy_post(req: HierarchyTestPost):
    return {
        "success": True,
        "canAccess": True,
        "userId": req.userId,
        "targetUserId": req.targetUserId,
        "message": "User has access to target employee data (mocked).",
    }
