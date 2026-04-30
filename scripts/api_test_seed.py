"""Seed accounts for API smoke testing.

Creates a triad of test accounts on the live Azure Postgres DB so the smoke
runner can hit every role gate (super_admin, employer, employee).

Layout
------
* super_admin: inserted directly via SQL (no API path for self-signup as admin).
* employer:    created via POST /api/auth/register (also creates a Company).
* employee:    created via POST /api/employees/create using the employer JWT.

All accounts share a deterministic suffix so a teardown can clean them up:
    smoketest+admin-<suffix>@diltak.test
    smoketest+employer-<suffix>@diltak.test
    smoketest+employee-<suffix>@diltak.test

Usage
-----
    python -m scripts.api_test_seed --setup
        Creates accounts, prints credentials + tokens to stdout as JSON.

    python -m scripts.api_test_seed --teardown
        Deletes any rows whose email matches the smoketest+*@diltak.test pattern,
        plus their refresh tokens and the company they belong to.

    python -m scripts.api_test_seed --setup --suffix abc123
        Use a fixed suffix instead of a random one (idempotent — reuses existing
        accounts if found).

The script touches REAL rows on the Azure DB. It only ever creates/deletes rows
matching ``smoketest+...@diltak.test`` so it cannot accidentally damage real data.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import uuid
from typing import Optional

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from auth.password import hash_password
from db.models import Company, RefreshToken, User
from db.session import get_session_factory
from main import app

# NOTE: ``@example.com`` (RFC 6761 reserved for documentation/tests) is used
# instead of ``.test`` because pydantic's EmailStr (via email-validator) rejects
# the latter as a special-use TLD.
EMAIL_DOMAIN = "@example.com"
EMAIL_PREFIX = "smoketest+"
# Legacy domain used by an earlier version of this script — teardown still
# matches it so any orphaned rows from that run get cleaned up.
LEGACY_EMAIL_DOMAIN = "@diltak.test"
PASSWORD = "SmokeTest@2026!"


def _email(role: str, suffix: str) -> str:
    return f"{EMAIL_PREFIX}{role}-{suffix}{EMAIL_DOMAIN}"


def _existing_user(db: Session, email: str) -> Optional[User]:
    return db.query(User).filter(User.email == email).one_or_none()


def _ensure_super_admin(db: Session, email: str) -> User:
    user = _existing_user(db, email)
    if user is not None:
        return user
    user = User(
        id=str(uuid.uuid4()),
        email=email,
        password_hash=hash_password(PASSWORD),
        role="super_admin",
        company_id=None,
        is_active=True,
        profile={"full_name": "Smoke Test Super Admin"},
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client: TestClient, email: str) -> dict:
    r = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    if r.status_code != 200:
        raise RuntimeError(f"login failed for {email}: HTTP {r.status_code} {r.text[:200]}")
    return r.json()


def _ensure_employer(client: TestClient, email: str, company_name: str) -> dict:
    """Register or log in an employer. Returns token pair + uid + company_id."""
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        existing = _existing_user(db, email)

    if existing is not None:
        tokens = _login(client, email)
        with SessionLocal() as db:
            u = _existing_user(db, email)
            assert u is not None
            return {
                "uid": u.id,
                "email": email,
                "company_id": str(u.company_id) if u.company_id else None,
                **tokens,
            }

    r = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "company_name": company_name,
            "full_name": "Smoke Test Employer",
        },
    )
    if r.status_code != 201:
        raise RuntimeError(f"employer register failed: HTTP {r.status_code} {r.text[:200]}")
    tokens = r.json()
    with SessionLocal() as db:
        u = _existing_user(db, email)
        assert u is not None
        return {
            "uid": u.id,
            "email": email,
            "company_id": str(u.company_id) if u.company_id else None,
            **tokens,
        }


def _ensure_employee(client: TestClient, employer_token: str, email: str) -> dict:
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        existing = _existing_user(db, email)

    if existing is None:
        headers = {"Authorization": f"Bearer {employer_token}"}
        r = client.post(
            "/api/employees/create",
            headers=headers,
            json={
                "email": email,
                "password": PASSWORD,
                "firstName": "Smoke",
                "lastName": "Employee",
                "role": "employee",
                "department": "QA",
                "position": "Tester",
                "sendWelcomeEmail": False,
            },
        )
        if r.status_code != 201:
            raise RuntimeError(
                f"employee create failed: HTTP {r.status_code} {r.text[:200]}"
            )

    tokens = _login(client, email)
    with SessionLocal() as db:
        u = _existing_user(db, email)
        assert u is not None
        return {
            "uid": u.id,
            "email": email,
            "company_id": str(u.company_id) if u.company_id else None,
            **tokens,
        }


def setup(suffix: Optional[str]) -> dict:
    suffix = suffix or secrets.token_hex(3)
    admin_email = _email("admin", suffix)
    employer_email = _email("employer", suffix)
    employee_email = _email("employee", suffix)
    company_name = f"SmokeTest Co {suffix}"

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        admin = _ensure_super_admin(db, admin_email)
        admin_id = admin.id

    client = TestClient(app)
    admin_tokens = _login(client, admin_email)
    employer = _ensure_employer(client, employer_email, company_name)
    employee = _ensure_employee(client, employer["access_token"], employee_email)

    return {
        "suffix": suffix,
        "password": PASSWORD,
        "super_admin": {
            "uid": admin_id,
            "email": admin_email,
            **admin_tokens,
        },
        "employer": employer,
        "employee": employee,
    }


def teardown() -> dict:
    """Delete every row whose email matches the smoketest pattern + companies."""
    SessionLocal = get_session_factory()
    deleted_users = 0
    deleted_companies = 0
    deleted_refresh = 0
    with SessionLocal() as db:
        users = (
            db.query(User)
            .filter(
                User.email.like(f"{EMAIL_PREFIX}%{EMAIL_DOMAIN}")
                | User.email.like(f"{EMAIL_PREFIX}%{LEGACY_EMAIL_DOMAIN}")
            )
            .all()
        )
        company_ids = {u.company_id for u in users if u.company_id is not None}
        user_ids = [u.id for u in users]

        if user_ids:
            deleted_refresh = (
                db.query(RefreshToken)
                .filter(RefreshToken.user_id.in_(user_ids))
                .delete(synchronize_session=False)
            )

        # Null out company.owner_id so user-delete won't trip the FK.
        for cid in company_ids:
            co = db.query(Company).filter(Company.id == cid).one_or_none()
            if co is not None and co.owner_id in user_ids:
                co.owner_id = None
        db.flush()

        for u in users:
            db.delete(u)
            deleted_users += 1

        for cid in company_ids:
            co = db.query(Company).filter(Company.id == cid).one_or_none()
            if co is not None:
                # Only delete if it's a smoke-test company (name pattern check).
                if (co.name or "").startswith("SmokeTest Co "):
                    db.delete(co)
                    deleted_companies += 1
        db.commit()

    return {
        "deleted_users": deleted_users,
        "deleted_companies": deleted_companies,
        "deleted_refresh_tokens": deleted_refresh,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--setup", action="store_true", help="Create seed accounts.")
    g.add_argument("--teardown", action="store_true", help="Delete all smoke-test accounts.")
    p.add_argument("--suffix", help="Fixed suffix instead of random.")
    args = p.parse_args()

    if args.setup:
        result = setup(args.suffix)
        print(json.dumps(result, indent=2, default=str))
        return 0
    if args.teardown:
        result = teardown()
        print(json.dumps(result, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
