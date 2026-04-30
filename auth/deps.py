"""FastAPI dependencies: extract bearer token, load user, check role.

These three functions preserve the exact signatures and return shapes of the
Firebase-based equivalents in the prior version of ``routers/auth.py``, so
every downstream router keeps working without modification.

Return shape of ``get_current_user``::

    {
        "uid": "<user_id>",      # matches Firebase's "uid" key name
        "email": "...",
        "role": "...",
        "company_id": "<uuid>",  # str form, or None
    }

``get_employer_user`` and ``get_super_admin_user`` return the full user row
joined with its company, as a plain dict (matching the old
``user_doc.to_dict()`` pattern).
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from auth.jwt_utils import InvalidTokenError, decode_access_token
from db.models import Company, User
from db.session import get_session

security = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """Verify the bearer JWT and return a compact claim dict."""
    try:
        claims = decode_access_token(credentials.credentials)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired authentication token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    uid = claims.get("sub")
    if uid:
        # Fire-and-forget last-active stamp so analytics dashboards stay fresh.
        # Imported lazily to avoid a circular import at module load time.
        from middleware.activity_tracker import update_last_active
        update_last_active(uid)
    return {
        "uid": uid,
        "email": claims.get("email"),
        "role": claims.get("role"),
        "company_id": claims.get("company_id"),
    }


def _load_user_profile(db: Session, uid: str) -> dict:
    user = db.query(User).filter(User.id == uid).one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User profile not found. Please complete registration.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been deactivated. Please contact your administrator.",
        )
    profile: dict[str, Any] = {
        "id": user.id,
        "uid": user.id,  # legacy alias — some routers read "uid"
        "email": user.email,
        "role": user.role,
        "company_id": str(user.company_id) if user.company_id else None,
        "manager_id": user.manager_id,
        "department": user.department,
        "is_active": user.is_active,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
        # Flatten profile JSONB so downstream code that reads e.g.
        # ``profile.get("phone")`` keeps working.
        **(user.profile or {}),
    }
    if user.company_id:
        company = db.query(Company).filter(Company.id == user.company_id).one_or_none()
        if company is not None:
            profile["company"] = {
                "id": str(company.id),
                "name": company.name,
                "owner_id": company.owner_id,
                "settings": company.settings,
                "employee_count": company.employee_count,
            }
    return profile


def get_employer_user(
    user_token: dict = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    """Require role 'employer' or 'hr'. Return full profile."""
    profile = _load_user_profile(db, user_token["uid"])
    if profile["role"] not in ("employer", "hr"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Access denied. Only employer or HR accounts can perform this action. "
                f"Your role: '{profile['role']}'."
            ),
        )
    return profile


def get_super_admin_user(
    user_token: dict = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    """Require role 'super_admin'. Return full profile."""
    profile = _load_user_profile(db, user_token["uid"])
    if profile["role"] != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Access denied. Only super_admin accounts can perform this action. "
                f"Your role: '{profile['role']}'."
            ),
        )
    return profile
