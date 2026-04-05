"""
Auth router — Employer Registration, Login, Profile
====================================================
  POST /api/auth/register        → Employer self-signup (public)
  POST /api/auth/login           → Any user login
  GET  /api/auth/me              → Current user profile (authenticated)
  GET  /api/auth/profile         → Full profile with company details
  POST /api/auth/refresh-profile → Force re-fetch profile from Firestore
"""

import httpx
from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from typing import Optional
from firebase_admin import auth as fb_auth
from firebase_config import get_db, firebaseConfig
from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from datetime import datetime, timezone

security = HTTPBearer()


# ─── Core auth dependency ─────────────────────────────────────────────────────

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """FastAPI dependency: verify Firebase JWT and return decoded token payload."""
    token = credentials.credentials
    try:
        decoded_token = fb_auth.verify_id_token(token)
        return decoded_token
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_employer_user(user_token: dict = Depends(get_current_user)) -> dict:
    """
    Dependency: requires the calling user to have role 'employer' or 'hr' in Firestore.
    Returns the full Firestore user profile dict.
    Raises 403 if the user is not an employer/hr.
    """
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    uid = user_token.get("uid")
    user_doc = db.collection("users").document(uid).get()

    if not user_doc.exists:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User profile not found. Please complete registration.",
        )

    profile = user_doc.to_dict()
    allowed_roles = ("employer", "hr")

    if profile.get("role") not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Access denied. Only employer or HR accounts can perform this action. "
                f"Your role: '{profile.get('role', 'unknown')}'."
            ),
        )

    return profile


def get_super_admin_user(user_token: dict = Depends(get_current_user)) -> dict:
    """
    Dependency: requires the calling user to have role 'super_admin' in Firestore.
    Returns the full Firestore user profile dict.
    Raises 403 if the user is not a super_admin.
    """
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    uid = user_token.get("uid")
    user_doc = db.collection("users").document(uid).get()

    if not user_doc.exists:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin profile not found.",
        )

    profile = user_doc.to_dict()

    if profile.get("role") != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Access denied. This endpoint requires super admin privileges. "
                f"Your role: '{profile.get('role', 'unknown')}'."
            ),
        )

    return profile


# ─── Router ──────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/auth", tags=["Auth"])


# ─── Schemas ─────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    firstName: str
    lastName: str
    email: EmailStr
    password: str
    companyName: str
    companySize: Optional[str] = "Not specified"
    industry: Optional[str] = "Not specified"
    phone: Optional[str] = None
    jobTitle: Optional[str] = "Owner / Founder"


class RegisterResponse(BaseModel):
    message: str
    userId: str
    companyId: str
    role: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserDetails(BaseModel):
    uid: str
    email: str
    displayName: str
    role: str
    companyId: Optional[str] = None
    companyName: Optional[str] = None


class LoginResponse(BaseModel):
    message: str
    access_token: str
    token_type: str
    expires_in: str
    user: UserDetails


class MeResponse(BaseModel):
    message: str
    token_payload: dict
    database_profile: Optional[dict] = None


class ProfileResponse(BaseModel):
    uid: str
    email: str
    firstName: str
    lastName: str
    role: str
    companyId: Optional[str] = None
    companyName: Optional[str] = None
    industry: Optional[str] = None
    companySize: Optional[str] = None
    jobTitle: Optional[str] = None
    phone: Optional[str] = None
    isActive: bool
    permissions: dict
    createdAt: Optional[str] = None


# ─── Employer Registration (Admin-Only) ──────────────────────────────────────

@router.post(
    "/register",
    status_code=status.HTTP_403_FORBIDDEN,
    summary="[DISABLED] Employer self-registration",
    description=(
        "**Disabled.** Self-registration is no longer allowed. "
        "Employers can only be created by the super admin via `POST /api/admin/employers`. "
        "Use `POST /api/auth/login` to log in to an existing account."
    ),
)
async def register_employer_disabled(req: RegisterRequest):
    """Self-registration is disabled. Only super admin can create employer accounts."""
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            "Self-registration is disabled. "
            "Employer accounts must be created by the Diltak admin. "
            "Contact admin@diltak.ai or use POST /api/admin/employers."
        ),
    )


# kept for internal use by super_admin router
async def _create_employer_account(req: RegisterRequest) -> RegisterResponse:

    if len(req.password) < 8:
        raise HTTPException(
            status_code=400,
            detail={"password": ["Password must be at least 8 characters"]},
        )

    if len(req.companyName.strip()) < 2:
        raise HTTPException(400, "Company name is too short.")

    try:
        # 1. Create Firebase Auth user
        fb_user = fb_auth.create_user(
            email=req.email,
            password=req.password,
            display_name=f"{req.firstName} {req.lastName}",
        )
        uid = fb_user.uid
        company_id = f"company_{uid}"

        db = get_db()
        if not db:
            # Firebase Auth already created — don't leave orphan; clean up
            fb_auth.delete_user(uid)
            raise HTTPException(503, "Database unavailable. Please try again.")

        now = datetime.now(timezone.utc).isoformat()

        # 2. Create company document
        db.collection("companies").document(company_id).set({
            "id": company_id,
            "name": req.companyName.strip(),
            "size": req.companySize,
            "industry": req.industry,
            "owner_id": uid,
            "employee_count": 0,
            "created_at": SERVER_TIMESTAMP,
            "updated_at": SERVER_TIMESTAMP,
        })

        # 3. Create employer user profile (full permissions)
        db.collection("users").document(uid).set({
            "id": uid,
            "email": req.email,
            "first_name": req.firstName,
            "last_name": req.lastName,
            "display_name": f"{req.firstName} {req.lastName}",
            "role": "employer",
            "job_title": req.jobTitle,
            "phone": req.phone,
            "company_id": company_id,
            "company_name": req.companyName.strip(),
            "is_active": True,
            "hierarchy_level": 0,
            # Permissions — employer has full access
            "can_view_team_reports": True,
            "can_manage_employees": True,
            "can_approve_leaves": True,
            "can_view_analytics": True,
            "can_create_programs": True,
            "is_department_head": True,
            "skip_level_access": True,
            "direct_reports": [],
            "department": None,
            "manager_id": None,
            "reporting_chain": [],
            "registered_at": SERVER_TIMESTAMP,
            "created_at": SERVER_TIMESTAMP,
            "updated_at": SERVER_TIMESTAMP,
        })

        return RegisterResponse(
            message="Company account created successfully! Please log in to get started.",
            userId=uid,
            companyId=company_id,
            role="employer",
        )

    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        if "EMAIL_EXISTS" in error_msg or "email-already-exists" in error_msg:
            raise HTTPException(
                status_code=409,
                detail="An account with this email already exists.",
            )
        print(f"[auth] Employer registration error: {e}")
        raise HTTPException(500, "Failed to create account. Please try again.")


# ─── Login ────────────────────────────────────────────────────────────────────

@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Login (any user)",
)
async def login(req: LoginRequest):
    """Authenticate any user (employer or employee) and return a Firebase ID token."""
    api_key = firebaseConfig.get("apiKey")
    if not api_key:
        raise HTTPException(500, "Server misconfiguration: Missing Firebase API Key.")

    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}"

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json={
            "email": req.email,
            "password": req.password,
            "returnSecureToken": True,
        })

    if resp.status_code != 200:
        err = resp.json().get("error", {}).get("message", "")
        if err in ("INVALID_LOGIN_CREDENTIALS", "INVALID_PASSWORD", "EMAIL_NOT_FOUND"):
            err = "Invalid email or password."
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=err)

    data = resp.json()
    uid  = data["localId"]

    # Fetch role and company from Firestore to enrich login response
    db = get_db()
    role, company_id, company_name = "unknown", None, None
    if db:
        try:
            doc = db.collection("users").document(uid).get()
            if doc.exists:
                p = doc.to_dict()
                role         = p.get("role", "unknown")
                company_id   = p.get("company_id")
                company_name = p.get("company_name")
        except Exception as e:
            print(f"[auth] Profile fetch on login error: {e}")

    return LoginResponse(
        message="Login successful!",
        access_token=data["idToken"],
        token_type="bearer",
        expires_in=data["expiresIn"],
        user=UserDetails(
            uid=uid,
            email=data["email"],
            displayName=data.get("displayName", ""),
            role=role,
            companyId=company_id,
            companyName=company_name,
        ),
    )


# ─── /me (lightweight token info) ────────────────────────────────────────────

@router.get(
    "/me",
    response_model=MeResponse,
    summary="Current user (JWT payload + profile)",
)
async def get_me(user_token: dict = Depends(get_current_user)):
    """Returns the decoded JWT payload and the Firestore profile for the current user."""
    uid = user_token.get("uid")
    db = get_db()

    profile = None
    if db:
        try:
            doc = db.collection("users").document(uid).get()
            if doc.exists:
                profile = doc.to_dict()
                # Never expose sensitive fields
                profile.pop("password", None)
        except Exception as e:
            print(f"[auth] /me Firestore fetch error: {e}")

    return MeResponse(
        message="Authenticated",
        token_payload=user_token,
        database_profile=profile,
    )


# ─── /profile (rich structured response) ─────────────────────────────────────

@router.get(
    "/profile",
    response_model=ProfileResponse,
    summary="Current user full profile",
)
async def get_profile(user_token: dict = Depends(get_current_user)):
    """Returns a fully structured profile for the authenticated user."""
    uid = user_token.get("uid")
    db  = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    doc = db.collection("users").document(uid).get()
    if not doc.exists:
        raise HTTPException(404, "User profile not found.")

    p = doc.to_dict()

    # Build permissions dict
    permissions = {
        "can_view_team_reports":  p.get("can_view_team_reports", False),
        "can_manage_employees":   p.get("can_manage_employees", False),
        "can_approve_leaves":     p.get("can_approve_leaves", False),
        "can_view_analytics":     p.get("can_view_analytics", False),
        "can_create_programs":    p.get("can_create_programs", False),
        "skip_level_access":      p.get("skip_level_access", False),
    }

    # Firestore timestamp → ISO string
    created_raw = p.get("created_at") or p.get("registered_at")
    created_str = None
    if created_raw and hasattr(created_raw, "timestamp"):
        created_str = datetime.fromtimestamp(created_raw.timestamp(), tz=timezone.utc).isoformat()

    return ProfileResponse(
        uid=uid,
        email=p.get("email", ""),
        firstName=p.get("first_name", ""),
        lastName=p.get("last_name", ""),
        role=p.get("role", "unknown"),
        companyId=p.get("company_id"),
        companyName=p.get("company_name"),
        industry=None,  # fetched from companies collection if needed
        companySize=None,
        jobTitle=p.get("job_title"),
        phone=p.get("phone"),
        isActive=p.get("is_active", True),
        permissions=permissions,
        createdAt=created_str,
    )
