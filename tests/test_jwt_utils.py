"""Unit tests for auth.jwt_utils."""

from __future__ import annotations

import pytest

from auth.jwt_utils import (
    InvalidTokenError,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
)


def test_access_token_roundtrip():
    claims = {"sub": "user-123", "role": "employer", "company_id": "c-abc"}
    token = create_access_token(claims)
    decoded = decode_access_token(token)
    assert decoded["sub"] == "user-123"
    assert decoded["role"] == "employer"
    assert decoded["company_id"] == "c-abc"
    assert decoded["typ"] == "access"
    # exp and iat stamped by the issuer
    assert "exp" in decoded
    assert "iat" in decoded


def test_access_token_short_lived():
    token = create_access_token({"sub": "u1"})
    decoded = decode_access_token(token)
    # 15-minute default lifetime -> exp - iat ~ 900 seconds (± 2s for clock skew)
    assert 880 <= decoded["exp"] - decoded["iat"] <= 920


def test_refresh_token_is_opaque_string():
    token = create_refresh_token()
    assert isinstance(token, str)
    assert len(token) >= 32  # token_urlsafe(48) -> 64 chars; at minimum 32


def test_refresh_token_each_call_is_unique():
    assert create_refresh_token() != create_refresh_token()


def test_decode_access_token_rejects_tampered_signature():
    token = create_access_token({"sub": "u1"})
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(InvalidTokenError):
        decode_access_token(tampered)


def test_decode_access_token_rejects_expired_token(monkeypatch):
    # _access_minutes() re-reads the env var every call, so no module reload
    # needed (and reload would break pytest.raises class identity for later
    # tests — there'd be two copies of InvalidTokenError).
    monkeypatch.setenv("JWT_ACCESS_MINUTES", "-1")
    token = create_access_token({"sub": "u1"})  # already expired
    with pytest.raises(InvalidTokenError):
        decode_access_token(token)


def test_decode_access_token_rejects_refresh_token_shape():
    # Refresh tokens are opaque strings, not JWTs. Decoding one as an access
    # token must raise.
    refresh = create_refresh_token()
    with pytest.raises(InvalidTokenError):
        decode_access_token(refresh)


def test_decode_refresh_token_returns_same_string():
    refresh = create_refresh_token()
    # decode_refresh_token is a thin validator — in our scheme refresh tokens
    # are opaque and validated by hash-compare against the DB. The function
    # must return the raw string and raise InvalidTokenError on empty input.
    assert decode_refresh_token(refresh) == refresh
    with pytest.raises(InvalidTokenError):
        decode_refresh_token("")
