"""End-to-end auth smoke test against the real FastAPI app + Azure Postgres.

Sequence:
    1. Register a disposable user (random email) -> expect 201 + token pair.
    2. GET /me with access token -> expect 200 + correct email/role.
    3. GET /profile with access token -> expect 200 + company populated.
    4. POST /login with same credentials -> expect 200 + new token pair.
    5. POST /refresh with refresh token -> expect 200 + rotated pair.
    6. POST /refresh again with OLD refresh -> expect 401 (rotation works).
    7. POST /logout with new refresh -> expect 204.
    8. POST /refresh with logged-out refresh -> expect 401.
    9. Cleanup: delete the test user + refresh tokens directly via SQL.

Exits 0 on full success, 1 on first failure.

Run:  python -m scripts.auth_smoke
"""

from __future__ import annotations

import secrets
import sys

from fastapi.testclient import TestClient

from db.models import Company, RefreshToken, User
from db.session import get_session_factory
from main import app


def _pp(label: str, resp) -> None:
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    print(f"  {label}: HTTP {resp.status_code} -> {str(body)[:200]}")


def main() -> int:
    client = TestClient(app)
    email = f"smoke-{secrets.token_hex(4)}@example.com"
    password = "CorrectHorseBatteryStaple!"
    company_name = f"SmokeCo-{secrets.token_hex(3)}"

    print(f"Using email: {email}")

    # 1. Register
    r = client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "company_name": company_name},
    )
    _pp("register", r)
    if r.status_code != 201:
        return 1
    tokens = r.json()
    access = tokens["access_token"]
    refresh1 = tokens["refresh_token"]

    headers = {"Authorization": f"Bearer {access}"}

    # 2. /me
    r = client.get("/api/auth/me", headers=headers)
    _pp("me", r)
    if r.status_code != 200 or r.json()["email"] != email:
        return 1

    # 3. /profile
    r = client.get("/api/auth/profile", headers=headers)
    _pp("profile", r)
    if r.status_code != 200 or r.json()["company"]["name"] != company_name:
        return 1

    # 4. /login
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    _pp("login", r)
    if r.status_code != 200:
        return 1
    login_refresh = r.json()["refresh_token"]

    # 5. /refresh (rotate)
    r = client.post("/api/auth/refresh", json={"refresh_token": login_refresh})
    _pp("refresh (rotate)", r)
    if r.status_code != 200:
        return 1
    new_refresh = r.json()["refresh_token"]

    # 6. /refresh with OLD (should fail)
    r = client.post("/api/auth/refresh", json={"refresh_token": login_refresh})
    _pp("refresh (replay old)", r)
    if r.status_code != 401:
        return 1

    # 7. /logout
    r = client.post("/api/auth/logout", json={"refresh_token": new_refresh})
    _pp("logout", r)
    if r.status_code != 204:
        return 1

    # 8. /refresh after logout (should fail)
    r = client.post("/api/auth/refresh", json={"refresh_token": new_refresh})
    _pp("refresh (after logout)", r)
    if r.status_code != 401:
        return 1

    # 9. Cleanup
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).one_or_none()
        if user:
            db.query(RefreshToken).filter(RefreshToken.user_id == user.id).delete()
            company_id = user.company_id
            # Null out company.owner_id before deleting user, so the FK
            # (ondelete=SET NULL) doesn't need to fire and we can delete
            # company cleanly immediately after.
            if company_id:
                co = db.query(Company).filter(Company.id == company_id).one_or_none()
                if co is not None:
                    co.owner_id = None
                    db.flush()
            db.delete(user)
            if company_id:
                db.query(Company).filter(Company.id == company_id).delete()
            db.commit()
    print("Cleanup: test user + company deleted.")

    print("ALL AUTH SMOKE TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
