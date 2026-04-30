"""Unit tests for auth.password."""

from __future__ import annotations

import pytest

from auth.password import hash_password, verify_password


def test_hash_is_not_plaintext():
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert len(hashed) > 20


def test_hash_is_bcrypt_format():
    hashed = hash_password("any password")
    # bcrypt hashes start with $2a$, $2b$, or $2y$
    assert hashed.startswith(("$2a$", "$2b$", "$2y$"))


def test_verify_correct_password_returns_true():
    hashed = hash_password("correct password")
    assert verify_password("correct password", hashed) is True


def test_verify_wrong_password_returns_false():
    hashed = hash_password("correct password")
    assert verify_password("WRONG password", hashed) is False


def test_verify_empty_password_against_any_hash_is_false():
    hashed = hash_password("something")
    assert verify_password("", hashed) is False


def test_verify_malformed_hash_returns_false():
    # Must not raise — return False for unparseable hashes.
    assert verify_password("whatever", "not-a-real-hash") is False


def test_hash_each_time_differs_due_to_salt():
    h1 = hash_password("same password")
    h2 = hash_password("same password")
    assert h1 != h2  # different salt each call
    assert verify_password("same password", h1)
    assert verify_password("same password", h2)


def test_unicode_password_roundtrip():
    pw = "पासवर्ड-日本語-🔐"
    hashed = hash_password(pw)
    assert verify_password(pw, hashed) is True
