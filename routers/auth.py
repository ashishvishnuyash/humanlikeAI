"""Auth router — Postgres-backed, self-issued JWT.

Endpoints (preserved from Firebase era):
  POST /api/auth/register        - Employer self-signup (public)
  POST /api/auth/login           - Any user login
  GET  /api/auth/me              - Current user claims (authenticated)
  GET  /api/auth/profile         - Full profile with company details
  POST /api/auth/refresh-profile - Re-query profile

Endpoints (new):
  POST /api/auth/refresh         - Trade refresh token for new access token
  POST /api/auth/logout          - Revoke refresh token

Compatibility: ``get_current_user``, ``get_employer_user``, ``get_super_admin_user``
are re-exported from ``auth.deps`` so existing
``from routers.auth import get_current_user`` imports keep working.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from auth.deps import (
    get_current_user,
    get_employer_user,
    get_super_admin_user,
    security,
)
from auth.jwt_utils import (
    InvalidTokenError,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
)
from auth.password import hash_password, verify_password
from db.models import Company, RefreshToken, User
from db.session import get_session

# Re-export for backward compatibility with downstream routers.
__all__ = [
    "router",
    "get_current_user",
    "get_employer_user",
    "get_super_admin_user",
    "security",
]

router = APIRouter(prefix="/auth", tags=["auth"])


# ─── Schemas ──────────────────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    company_name: str = Field(min_length=1)
    full_name: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


# Backward-compat alias: super_admin.py and possibly other routers still
# import RegisterResponse by name. Same shape as TokenPair.
RegisterResponse = TokenPair


class MeResponse(BaseModel):
    uid: Optional[str]
    email: Optional[str]
    role: Optional[str]
    company_id: Optional[str]


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _refresh_expires_at() -> datetime:
    days = int(os.environ.get("JWT_REFRESH_DAYS", "30"))
    return datetime.now(timezone.utc) + timedelta(days=days)


def _issue_tokens(db: Session, user: User) -> TokenPair:
    access = create_access_token({
        "sub": user.id,
        "email": user.email,
        "role": user.role,
        "company_id": str(user.company_id) if user.company_id else None,
    })
    refresh = create_refresh_token()
    # Store *hash* of refresh token — never the raw value.
    rt_row = RefreshToken(
        user_id=user.id,
        token_hash=hash_password(refresh),
        expires_at=_refresh_expires_at(),
        revoked=False,
    )
    db.add(rt_row)
    db.commit()
    return TokenPair(access_token=access, refresh_token=refresh)


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
def register(req: RegisterRequest, db: Session = Depends(get_session)) -> TokenPair:
    existing = db.query(User).filter(User.email == req.email).one_or_none()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email already exists.")

    company = Company(id=uuid.uuid4(), name=req.company_name, settings={})
    db.add(company)
    db.flush()  # gets company.id

    user = User(
        id=str(uuid.uuid4()),
        email=req.email,
        password_hash=hash_password(req.password),
        role="employer",
        company_id=company.id,
        is_active=True,
        profile={"full_name": req.full_name} if req.full_name else {},
    )
    db.add(user)
    db.flush()

    company.owner_id = user.id
    db.commit()
    db.refresh(user)
    return _issue_tokens(db, user)


@router.post("/login", response_model=TokenPair)
def login(req: LoginRequest, db: Session = Depends(get_session)) -> TokenPair:
    user = db.query(User).filter(User.email == req.email).one_or_none()
    if user is None or user.password_hash is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password.")
    if not verify_password(req.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password.")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your account has been deactivated.")
    return _issue_tokens(db, user)


@router.post("/refresh", response_model=TokenPair)
def refresh(req: RefreshRequest, db: Session = Depends(get_session)) -> TokenPair:
    try:
        raw = decode_refresh_token(req.refresh_token)
    except InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    # Refresh tokens are opaque — find the matching non-revoked, non-expired row
    # by checking each candidate hash. In practice the number of active tokens
    # per user is small, but we still scope by not-revoked + not-expired to keep
    # this bounded.
    now = datetime.now(timezone.utc)
    candidates = (
        db.query(RefreshToken)
        .filter(RefreshToken.revoked.is_(False), RefreshToken.expires_at > now)
        .all()
    )
    match: RefreshToken | None = None
    for row in candidates:
        if verify_password(raw, row.token_hash):
            match = row
            break
    if match is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token.")

    # Rotate: revoke old, issue new pair.
    match.revoked = True
    user = db.query(User).filter(User.id == match.user_id).one_or_none()
    if user is None or not user.is_active:
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User is no longer active.")
    db.commit()
    return _issue_tokens(db, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(req: RefreshRequest, db: Session = Depends(get_session)) -> None:
    # Best-effort: revoke the refresh token matching the presented value.
    candidates = (
        db.query(RefreshToken).filter(RefreshToken.revoked.is_(False)).all()
    )
    for row in candidates:
        if verify_password(req.refresh_token, row.token_hash):
            row.revoked = True
            db.commit()
            return None
    # No-op if not found — don't leak whether the token existed.
    return None


@router.get("/me", response_model=MeResponse)
def me(user_token: dict = Depends(get_current_user)) -> MeResponse:
    return MeResponse(**user_token)


@router.get("/profile")
def profile(
    user_token: dict = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    user = db.query(User).filter(User.id == user_token["uid"]).one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    company = None
    if user.company_id:
        c = db.query(Company).filter(Company.id == user.company_id).one_or_none()
        if c is not None:
            company = {
                "id": str(c.id),
                "name": c.name,
                "owner_id": c.owner_id,
                "settings": c.settings,
                "employee_count": c.employee_count,
            }
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "company_id": str(user.company_id) if user.company_id else None,
        "manager_id": user.manager_id,
        "department": user.department,
        "is_active": user.is_active,
        "profile": user.profile or {},
        "company": company,
    }


@router.post("/refresh-profile")
def refresh_profile(
    user_token: dict = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    # Same payload as /profile — kept for backward compat with frontends that
    # hit this endpoint to force a re-fetch.
    return profile(user_token, db)
