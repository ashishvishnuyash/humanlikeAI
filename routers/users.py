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

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from auth.password import hash_password
from db.models.company import Company
from db.models.mental_health import CheckIn
from db.models.mental_health import Session as MHSession
from db.models.user import User
from db.session import get_session
from routers.auth import get_current_user, get_employer_user
from utils.audit import log_audit

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
    page: Optional[int] = None
    limit: Optional[int] = None
    totalPages: Optional[int] = None
    hasNext: Optional[bool] = None
    hasPrev: Optional[bool] = None


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


def _build_profile_from_user(user: User) -> EmployeeProfile:
    profile = user.profile or {}
    perms = {k: profile.get(k, False) for k in (
        "can_view_team_reports",
        "can_manage_employees",
        "can_approve_leaves",
        "can_view_analytics",
        "can_create_programs",
        "skip_level_access",
    )}
    return EmployeeProfile(
        uid=user.id,
        email=user.email,
        firstName=profile.get("first_name", ""),
        lastName=profile.get("last_name", ""),
        role=user.role,
        department=user.department,
        position=profile.get("position"),
        phone=profile.get("phone"),
        companyId=str(user.company_id) if user.company_id else "",
        managerId=user.manager_id,
        hierarchyLevel=profile.get("hierarchy_level", 1),
        isActive=user.is_active,
        permissions=perms,
        createdAt=_ts_to_iso(user.created_at),
        createdBy=profile.get("created_by"),
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


def _parse_company_uuid(cid_str: str) -> uuid.UUID:
    """Convert a company_id string to UUID, raising HTTP 400 on invalid input."""
    try:
        return uuid.UUID(cid_str)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail=f"Invalid company_id: {cid_str!r}")


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
    db: Session = Depends(get_session),
):
    # ── Validation ────────────────────────────────────────────────────────
    if len(req.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")

    if req.role not in VALID_EMPLOYEE_ROLES:
        raise HTTPException(
            400,
            f"Invalid role '{req.role}'. Allowed: {sorted(VALID_EMPLOYEE_ROLES)}.",
        )

    employer_company_id_str = employer.get("company_id")
    if not employer_company_id_str:
        raise HTTPException(400, "Employer profile is missing a company_id.")

    employer_company_uuid = _parse_company_uuid(str(employer_company_id_str))

    # ── Check for duplicate email ──────────────────────────────────────────
    existing = db.query(User).filter(User.email == req.email).one_or_none()
    if existing:
        raise HTTPException(409, "An account with this email already exists.")

    # ── Validate managerId belongs to same company ─────────────────────────
    manager_id = req.managerId if req.managerId and req.managerId != "none" else None
    if manager_id:
        mgr = db.query(User).filter(User.id == manager_id).one_or_none()
        if mgr is None or str(mgr.company_id) != str(employer_company_uuid):
            raise HTTPException(
                400,
                "Specified managerId does not belong to your company.",
            )

    # ── Build permissions ──────────────────────────────────────────────────
    default_perms = _default_permissions(req.role)
    if req.permissions:
        default_perms.update(req.permissions)

    uid = str(uuid.uuid4())

    profile_data: Dict[str, Any] = {
        "first_name":      req.firstName,
        "last_name":       req.lastName,
        "display_name":    f"{req.firstName} {req.lastName}",
        "position":        req.position,
        "phone":           req.phone,
        "company_name":    employer.get("company_name", ""),
        "hierarchy_level": req.hierarchyLevel,
        "created_by":      employer.get("id"),
        **default_perms,
    }

    new_user = User(
        id=uid,
        email=req.email,
        password_hash=hash_password(req.password),
        role=req.role,
        company_id=employer_company_uuid,
        manager_id=manager_id,
        department=req.department or None,
        is_active=True,
        profile=profile_data,
    )

    try:
        db.add(new_user)
        db.flush()  # get PK into session before updating company

        # Update company employee_count
        company = db.query(Company).filter(Company.id == employer_company_uuid).one_or_none()
        if company is not None:
            db.query(Company).filter(Company.id == employer_company_uuid).update(
                {"employee_count": Company.employee_count + 1}
            )

        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[users] create_employee DB error: {e}")
        raise HTTPException(500, "Failed to save employee profile.")

    log_audit(
        actor_uid=employer.get("id", ""),
        actor_role=employer.get("role", "employer"),
        action="user.create",
        company_id=str(employer_company_uuid) if employer_company_uuid else "",
        target_uid=uid,
        target_type="user",
        metadata={"role": req.role, "email": req.email, "department": req.department},
    )

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
    description="**Employer / HR only.** Returns employees in the caller's company with pagination and search.",
)
async def list_employees(
    include_inactive: bool = Query(False, description="Include deactivated accounts"),
    department: Optional[str] = Query(None),
    role_filter: Optional[str] = Query(None, alias="role"),
    search: Optional[str] = Query(None, description="Search by name, email, department, or job title"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    limit: int = Query(20, ge=1, le=100, description="Records per page"),
    employer: dict = Depends(get_employer_user),
    db: Session = Depends(get_session),
):
    company_id_str = employer.get("company_id")
    company_uuid = _parse_company_uuid(str(company_id_str))

    try:
        query = db.query(User).filter(User.company_id == company_uuid)
        if not include_inactive:
            query = query.filter(User.is_active == True)  # noqa: E712
        all_users = query.all()
    except Exception as e:
        raise HTTPException(500, f"Database query failed: {e}")

    employees: List[EmployeeProfile] = []
    for user in all_users:
        # Skip the employer themselves
        if user.role == "employer":
            continue
        # Filter by department (exact match)
        if department and (user.department or "").lower() != department.lower():
            continue
        # Filter by role
        if role_filter and user.role != role_filter:
            continue
        # Search across name, email, department, job title
        if search:
            profile = user.profile or {}
            term = search.lower().strip()
            full_name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".lower()
            searchable = " ".join([
                full_name,
                user.email.lower(),
                (user.department or "").lower(),
                (profile.get("position", "") or "").lower(),
            ])
            if term not in searchable:
                continue

        employees.append(_build_profile_from_user(user))

    total = len(employees)
    total_pages = max(1, (total + limit - 1) // limit)
    offset = (page - 1) * limit
    page_employees = employees[offset: offset + limit]

    return ListEmployeesResponse(
        success=True,
        employees=page_employees,
        total=total,
        companyId=str(company_uuid),
        page=page,
        limit=limit,
        totalPages=total_pages,
        hasNext=offset + limit < total,
        hasPrev=page > 1,
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
    db: Session = Depends(get_session),
):
    company_id_str = employer.get("company_id")
    company_uuid = _parse_company_uuid(str(company_id_str))

    user = db.query(User).filter(User.id == uid).one_or_none()
    if user is None:
        raise HTTPException(404, "Employee not found.")

    if user.company_id != company_uuid:
        raise HTTPException(403, "Employee does not belong to your company.")

    if user.role == "employer":
        raise HTTPException(403, "Cannot view employer profiles via this endpoint.")

    return _build_profile_from_user(user)


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
    db: Session = Depends(get_session),
):
    company_id_str = employer.get("company_id")
    company_uuid = _parse_company_uuid(str(company_id_str))

    user = db.query(User).filter(User.id == uid).one_or_none()
    if user is None:
        raise HTTPException(404, "Employee not found.")

    if user.company_id != company_uuid:
        raise HTTPException(403, "Employee does not belong to your company.")

    if user.role == "employer":
        raise HTTPException(403, "Cannot modify employer accounts via this endpoint.")

    updated_fields: List[str] = []
    profile = dict(user.profile or {})

    # Profile JSONB fields
    profile_field_map = {
        "firstName":      "first_name",
        "lastName":       "last_name",
        "position":       "position",
        "phone":          "phone",
        "hierarchyLevel": "hierarchy_level",
    }
    for req_field, profile_key in profile_field_map.items():
        val = getattr(req, req_field, None)
        if val is not None:
            profile[profile_key] = val
            updated_fields.append(profile_key)

    # Top-level column fields
    if req.department is not None:
        user.department = req.department
        updated_fields.append("department")

    if req.role is not None:
        if req.role not in VALID_EMPLOYEE_ROLES:
            raise HTTPException(400, f"Invalid role '{req.role}'.")
        user.role = req.role
        updated_fields.append("role")

    # managerId special handling
    if req.managerId is not None:
        manager_id = req.managerId if req.managerId != "none" else None
        if manager_id:
            mgr = db.query(User).filter(User.id == manager_id).one_or_none()
            if mgr is None or str(mgr.company_id) != str(company_uuid):
                raise HTTPException(400, "Specified managerId does not belong to your company.")
        user.manager_id = manager_id
        updated_fields.append("manager_id")

    # Permissions stored in profile JSONB
    if req.permissions:
        profile.update(req.permissions)
        updated_fields.extend(list(req.permissions.keys()))

    # Rebuild display_name if name changed
    if "first_name" in updated_fields or "last_name" in updated_fields:
        fn = profile.get("first_name", "")
        ln = profile.get("last_name", "")
        profile["display_name"] = f"{fn} {ln}"

    if not updated_fields:
        raise HTTPException(400, "No valid fields to update.")

    # Write back merged profile (replace dict to trigger SQLAlchemy change detection)
    user.profile = profile

    db.commit()

    log_audit(
        actor_uid=employer.get("id", ""),
        actor_role=employer.get("role", "employer"),
        action="user.update",
        company_id=str(user.company_id) if user.company_id else "",
        target_uid=uid,
        target_type="user",
        metadata={"updated_fields": updated_fields},
    )

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
    db: Session = Depends(get_session),
):
    return await _set_employee_active(uid, active=False, employer=employer, db=db)


@router.post(
    "/employees/{uid}/reactivate",
    response_model=DeactivateResponse,
    summary="Reactivate Employee",
    description="**Employer / HR only.** Re-activates a previously deactivated employee.",
)
async def reactivate_employee(
    uid: str,
    employer: dict = Depends(get_employer_user),
    db: Session = Depends(get_session),
):
    return await _set_employee_active(uid, active=True, employer=employer, db=db)


async def _set_employee_active(uid: str, active: bool, employer: dict, db: Session) -> DeactivateResponse:
    company_id_str = employer.get("company_id")
    company_uuid = _parse_company_uuid(str(company_id_str))

    user = db.query(User).filter(User.id == uid).one_or_none()
    if user is None:
        raise HTTPException(404, "Employee not found.")

    if user.company_id != company_uuid:
        raise HTTPException(403, "Employee does not belong to your company.")
    if user.role == "employer":
        raise HTTPException(403, "Cannot modify employer accounts.")

    user.is_active = active
    db.commit()

    log_audit(
        actor_uid=employer.get("id", ""),
        actor_role=employer.get("role", "employer"),
        action="user.reactivate" if active else "user.deactivate",
        company_id=str(company_uuid),
        target_uid=uid,
        target_type="user",
    )

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
        "**Employer only (not HR).** Permanently deletes an employee account and "
        "profile. This action is irreversible. "
        "Use `POST /employees/{uid}/deactivate` for a reversible soft-delete."
    ),
)
async def delete_employee(
    uid: str,
    employer: dict = Depends(get_employer_user),
    db: Session = Depends(get_session),
):
    if employer.get("role") != "employer":
        raise HTTPException(403, "Only the company owner can permanently delete employee accounts.")

    company_id_str = employer.get("company_id")
    company_uuid = _parse_company_uuid(str(company_id_str))

    user = db.query(User).filter(User.id == uid).one_or_none()
    if user is None:
        raise HTTPException(404, "Employee not found.")

    if user.company_id != company_uuid:
        raise HTTPException(403, "Employee does not belong to your company.")
    if user.role == "employer":
        raise HTTPException(403, "Cannot delete the employer owner account via this endpoint.")

    manager_id = user.manager_id

    # Reassign this employee's direct reports to their manager
    # (direct_reports array is gone; query by manager_id instead)
    direct_report_users = (
        db.query(User)
        .filter(User.manager_id == uid)  # noqa: E712
        .all()
    )
    for report_user in direct_report_users:
        report_user.manager_id = manager_id  # could be None (becomes top-level)

    # Explicit cross-collection cleanup (FK CASCADE also handles these,
    # but kept for clarity / belt-and-suspenders)
    db.query(CheckIn).filter(CheckIn.user_id == uid).delete()
    db.query(MHSession).filter(MHSession.user_id == uid).delete()

    # Delete the user row
    db.query(User).filter(User.id == uid).delete()

    # Decrement company employee_count
    db.query(Company).filter(Company.id == company_uuid).update(
        {"employee_count": Company.employee_count - 1}
    )

    db.commit()

    log_audit(
        actor_uid=employer.get("id", ""),
        actor_role=employer.get("role", "employer"),
        action="user.delete",
        company_id=str(company_uuid),
        target_uid=uid,
        target_type="user",
    )

    return DeleteEmployeeResponse(
        success=True,
        uid=uid,
        message="Employee deleted permanently.",
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
    warnings: Optional[List[str]] = None


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
    db: Session = Depends(get_session),
):
    if not employees:
        raise HTTPException(400, "Employee list cannot be empty.")
    if len(employees) > 50:
        raise HTTPException(400, "Cannot create more than 50 employees per request.")

    company_id_str = employer.get("company_id")
    company_uuid = _parse_company_uuid(str(company_id_str))
    company_name = employer.get("company_name", "")
    creator_uid  = employer.get("id")

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

        # Check for duplicate email
        existing = db.query(User).filter(User.email == item.email).one_or_none()
        if existing:
            results.append(BulkCreateResult(
                email=item.email, success=False, error="Email already exists."))
            failed += 1
            continue

        manager_id = item.managerId if item.managerId and item.managerId != "none" else None
        item_warnings: List[str] = []

        # Validate manager belongs to same company
        if manager_id:
            try:
                mgr = db.query(User).filter(User.id == manager_id).one_or_none()
                if mgr is None or str(mgr.company_id) != str(company_uuid):
                    item_warnings.append(
                        f"Manager '{manager_id}' not found in your company — manager link skipped."
                    )
                    manager_id = None
            except Exception as mgr_err:
                item_warnings.append(f"Manager validation failed: {mgr_err} — manager link skipped.")
                manager_id = None

        perms = _default_permissions(item.role)
        uid = str(uuid.uuid4())

        profile_data: Dict[str, Any] = {
            "first_name":      item.firstName,
            "last_name":       item.lastName,
            "display_name":    f"{item.firstName} {item.lastName}",
            "position":        item.position,
            "phone":           item.phone,
            "company_name":    company_name,
            "hierarchy_level": item.hierarchyLevel,
            "created_by":      creator_uid,
            **perms,
        }

        new_user = User(
            id=uid,
            email=item.email,
            password_hash=hash_password(item.password),
            role=item.role,
            company_id=company_uuid,
            manager_id=manager_id,
            department=item.department or None,
            is_active=True,
            profile=profile_data,
        )

        try:
            db.add(new_user)
            db.flush()
        except Exception as e:
            db.rollback()
            results.append(BulkCreateResult(
                email=item.email, success=False,
                error=f"Profile save failed: {e}."))
            failed += 1
            continue

        created += 1
        results.append(BulkCreateResult(
            email=item.email,
            success=True,
            uid=uid,
            warnings=item_warnings if item_warnings else None,
        ))

    # Update company employee_count once at end
    if created > 0:
        try:
            db.query(Company).filter(Company.id == company_uuid).update(
                {"employee_count": Company.employee_count + created}
            )
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"[users] bulk_create count update error: {e}")
    else:
        db.rollback()

    return BulkCreateResponse(
        success=failed == 0,
        created=created,
        failed=failed,
        results=results,
        companyId=str(company_uuid),
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
        "Automatically updates manager links without denormalized direct_reports arrays."
    ),
)
async def transfer_employee(
    uid: str,
    req: TransferEmployeeRequest,
    employer: dict = Depends(get_employer_user),
    db: Session = Depends(get_session),
):
    company_id_str = employer.get("company_id")
    company_uuid = _parse_company_uuid(str(company_id_str))

    user = db.query(User).filter(User.id == uid).one_or_none()
    if user is None:
        raise HTTPException(404, "Employee not found.")

    if user.company_id != company_uuid:
        raise HTTPException(403, "Employee does not belong to your company.")
    if user.role == "employer":
        raise HTTPException(403, "Cannot transfer employer accounts.")

    old_manager_id = user.manager_id
    new_manager_id = req.newManagerId if req.newManagerId and req.newManagerId != "none" else None

    # Validate new manager belongs to same company
    if new_manager_id:
        mgr = db.query(User).filter(User.id == new_manager_id).one_or_none()
        if mgr is None or str(mgr.company_id) != str(company_uuid):
            raise HTTPException(400, "New managerId does not belong to your company.")

    changes: Dict[str, Any] = {}
    profile = dict(user.profile or {})

    if req.newManagerId is not None:      # explicit field provided (even if None)
        user.manager_id = new_manager_id
        changes["manager_id"] = {"from": old_manager_id, "to": new_manager_id}

    if req.newDepartment is not None:
        changes["department"] = {"from": user.department, "to": req.newDepartment}
        user.department = req.newDepartment

    if req.newPosition is not None:
        changes["position"] = {"from": profile.get("position"), "to": req.newPosition}
        profile["position"] = req.newPosition

    if req.newHierarchyLevel is not None:
        changes["hierarchy_level"] = {"from": profile.get("hierarchy_level"), "to": req.newHierarchyLevel}
        profile["hierarchy_level"] = req.newHierarchyLevel

    if not changes:
        raise HTTPException(400, "No transfer fields provided.")

    # Write back merged profile
    user.profile = profile

    db.commit()

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
    db: Session = Depends(get_session),
):
    company_id_str = employer.get("company_id")
    company_uuid = _parse_company_uuid(str(company_id_str))

    user = db.query(User).filter(User.id == uid).one_or_none()
    if user is None:
        raise HTTPException(404, "Employee not found.")

    if user.company_id != company_uuid:
        raise HTTPException(403, "Employee does not belong to your company.")
    if user.role == "employer":
        raise HTTPException(403, "Cannot view employer activity via this endpoint.")

    now = datetime.now(timezone.utc)

    # ── Check-ins ─────────────────────────────────────────────────────────
    total_checkins = 0
    mood_sum = 0.0
    stress_sum = 0.0
    last_active_ts: Optional[float] = None
    risk_counts: Dict[str, int] = {"low": 0, "medium": 0, "high": 0}

    try:
        ci_rows = db.query(CheckIn).filter(CheckIn.user_id == uid).all()
        for ci in ci_rows:
            d = ci.data or {}
            total_checkins += 1
            mood_sum   += float(d.get("mood_score", 0) or 0)
            stress_sum += float(d.get("stress_level", 0) or 0)
            ts = ci.created_at
            if ts is not None:
                ts_val = ts.timestamp() if hasattr(ts, "timestamp") else None
                if ts_val is not None:
                    if last_active_ts is None or ts_val > last_active_ts:
                        last_active_ts = ts_val
            risk = d.get("risk_level", "low")
            if risk in risk_counts:
                risk_counts[risk] += 1
    except Exception as e:
        print(f"[users] check_ins query error: {e}")

    # ── Sessions ──────────────────────────────────────────────────────────
    total_sessions = 0
    modalities: Dict[str, int] = {}

    try:
        sess_rows = db.query(MHSession).filter(MHSession.user_id == uid).all()
        for s in sess_rows:
            d = s.messages or {}
            total_sessions += 1
            # modality stored in messages dict or session data
            mod = d.get("modality", "unknown") if isinstance(d, dict) else "unknown"
            modalities[mod] = modalities.get(mod, 0) + 1
            ts = s.created_at
            if ts is not None:
                ts_val = ts.timestamp() if hasattr(ts, "timestamp") else None
                if ts_val is not None:
                    if last_active_ts is None or ts_val > last_active_ts:
                        last_active_ts = ts_val
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
        companyId=str(company_uuid),
        totalCheckIns=total_checkins,
        totalSessions=total_sessions,
        lastActiveAt=last_active_iso,
        avgMoodScore=avg_mood,
        avgStressLevel=avg_stress,
        riskLevel=dominant_risk,
        sessionModalities=modalities,
        computedAt=now.isoformat(),
    )


# ─── Hierarchy Test Stubs ─────────────────────────────────────────────────────

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
