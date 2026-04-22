"""JWT issuance and verification.

Access tokens are short-lived (default 15 min) HS256 JWTs with user claims.
Refresh tokens are opaque random strings; their *hashed* form is stored in
the ``refresh_tokens`` table and compared on use (handled in the auth router,
not here).

Environment variables:
    JWT_SECRET            - required, min 32 chars
    JWT_ACCESS_MINUTES    - optional, default 15
    JWT_REFRESH_DAYS      - optional, default 30 (informational only — refresh
                            token lifetime is enforced by the DB ``expires_at``
                            column, not by the token itself)
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt as pyjwt

_ALGORITHM = "HS256"


class InvalidTokenError(Exception):
    """Raised when a token is missing, tampered with, expired, or malformed."""


def _secret() -> str:
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET is not set")
    return secret


def _access_minutes() -> int:
    return int(os.environ.get("JWT_ACCESS_MINUTES", "15"))


def create_access_token(claims: dict) -> str:
    """Issue a signed, short-lived access token with the caller-supplied claims."""
    now = datetime.now(timezone.utc)
    payload = {
        **claims,
        "typ": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=_access_minutes())).timestamp()),
    }
    return pyjwt.encode(payload, _secret(), algorithm=_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and verify an access token. Raise InvalidTokenError on any failure."""
    if not token:
        raise InvalidTokenError("empty token")
    try:
        payload = pyjwt.decode(token, _secret(), algorithms=[_ALGORITHM])
    except pyjwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc
    if payload.get("typ") != "access":
        raise InvalidTokenError("not an access token")
    return payload


def create_refresh_token() -> str:
    """Issue an opaque random refresh token (URL-safe base64, ~64 chars)."""
    return secrets.token_urlsafe(48)


def decode_refresh_token(token: str) -> str:
    """Validate the shape of a refresh token. Returns it unchanged if non-empty.

    Real validation (not-revoked, not-expired, matches stored hash) happens in
    the auth router against the ``refresh_tokens`` table.
    """
    if not token:
        raise InvalidTokenError("empty refresh token")
    return token
