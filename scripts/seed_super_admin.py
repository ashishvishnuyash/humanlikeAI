"""
Seed Super Admin Account
========================
Run this ONCE to create the super admin account in Firebase Auth + Firestore.

    python scripts/seed_super_admin.py

Credentials:
  Email:    admin@diltak.ai
  Password: Diltak#911@
  Role:     super_admin

Safe to re-run — skips creation if account already exists.
"""

import os
import sys

import firebase_admin
from firebase_admin import auth as fb_auth, credentials, firestore
from google.cloud.firestore_v1 import SERVER_TIMESTAMP

# ─── Config ───────────────────────────────────────────────────────────────────

ADMIN_EMAIL    = "admin@diltak.ai"
ADMIN_PASSWORD = "Diltak#911@"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(SCRIPT_DIR)
CRED_PATH  = os.environ.get(
    "FIREBASE_CREDENTIALS_PATH",
    os.path.join(PARENT_DIR, "firebaseadmn.json"),
)


# ─── Initialise Firebase ──────────────────────────────────────────────────────

def init_firebase():
    if firebase_admin._apps:
        return firestore.client()

    if not os.path.exists(CRED_PATH):
        print(f"  Service account file not found: {CRED_PATH}")
        print(f"  Set FIREBASE_CREDENTIALS_PATH or place firebaseadmn.json in: {PARENT_DIR}")
        sys.exit(1)

    cred = credentials.Certificate(CRED_PATH)
    firebase_admin.initialize_app(cred)
    print(f"  Firebase initialised with: {os.path.basename(CRED_PATH)}")
    return firestore.client()


# ─── Seed ─────────────────────────────────────────────────────────────────────

def seed():
    print("=" * 60)
    print("  Diltak Super Admin - Seed Script")
    print("=" * 60)

    db = init_firebase()

    # 1. Check / Create Firebase Auth account
    existing_uid = None
    try:
        fb_user = fb_auth.get_user_by_email(ADMIN_EMAIL)
        existing_uid = fb_user.uid
        print(f"  Firebase Auth account already exists: {existing_uid}")
    except fb_auth.UserNotFoundError:
        print(f"  Creating Firebase Auth account for {ADMIN_EMAIL} ...")
        try:
            fb_user = fb_auth.create_user(
                email=ADMIN_EMAIL,
                password=ADMIN_PASSWORD,
                display_name="Diltak Super Admin",
                email_verified=True,
            )
            existing_uid = fb_user.uid
            print(f"  Firebase Auth account created: {existing_uid}")
        except Exception as e:
            print(f"  FAILED to create Firebase Auth account: {e}")
            sys.exit(1)
    except Exception as e:
        print(f"  Error checking Firebase Auth: {e}")
        sys.exit(1)

    uid = existing_uid

    # 2. Upsert Firestore super_admin profile
    admin_doc = {
        "id":              uid,
        "email":           ADMIN_EMAIL,
        "first_name":      "Diltak",
        "last_name":       "Admin",
        "display_name":    "Diltak Super Admin",
        "role":            "super_admin",
        "is_active":       True,
        "is_super_admin":  True,
        "company_id":      None,
        "company_name":    None,
        "phone":           None,
        "department":      None,
        "manager_id":      None,
        "hierarchy_level": -1,
        "direct_reports":  [],
        "reporting_chain": [],
        "can_view_team_reports": True,
        "can_manage_employees":  True,
        "can_approve_leaves":    True,
        "can_view_analytics":    True,
        "can_create_programs":   True,
        "skip_level_access":     True,
        "is_department_head":    True,
        "created_at":            SERVER_TIMESTAMP,
        "updated_at":            SERVER_TIMESTAMP,
    }

    try:
        db.collection("users").document(uid).set(admin_doc, merge=True)
        print(f"  Firestore super_admin profile upserted (uid: {uid})")
    except Exception as e:
        print(f"  FAILED to write Firestore profile: {e}")
        sys.exit(1)

    print()
    print("-" * 60)
    print("  Super Admin account ready!")
    print(f"  Email   : {ADMIN_EMAIL}")
    print(f"  Password: {ADMIN_PASSWORD}")
    print(f"  UID     : {uid}")
    print(f"  Role    : super_admin")
    print("-" * 60)
    print()
    print("  Login  >>>  POST /api/auth/login")
    print("  Admin  >>>  GET  /api/admin/me")
    print()


if __name__ == "__main__":
    seed()
