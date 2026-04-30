# Phase 1 — Infrastructure Setup

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the Python packages, module skeletons, and Azure Postgres connectivity required for the rest of the migration. No routers touched. No data moved. End state: the app still runs on Firebase exactly as today, AND a new `db.session` module can open a connection to Azure Postgres on demand.

**Architecture:** Additive only. Install SQLAlchemy 2.0, Alembic, psycopg2, Azure Blob SDK, PyJWT, bcrypt. Create `db/`, `auth/`, `storage/`, `migration/` packages as empty skeletons. Add a SQLAlchemy engine + session factory in `db/session.py`. Add a smoke-test CLI (`python -m db.smoke`) that runs `SELECT 1` against Azure Postgres to prove connectivity.

**Tech Stack:** SQLAlchemy 2.0, Alembic, psycopg2-binary, python-dotenv (already present), Azure Postgres Flexible Server (via `diltakdb.postgres.database.azure.com`).

**Spec reference:** `docs/superpowers/specs/2026-04-22-postgres-migration-design.md` — Section 8, Phase 1.

---

## File Structure

New files created in this phase:

| Path | Responsibility |
|---|---|
| `requirements.txt` | **modified** — add new dependencies |
| `.env.example` | template for required env vars (committed) |
| `.env` | **modified** (gitignored) — local dev values |
| `db/__init__.py` | empty package marker |
| `db/session.py` | engine factory, `SessionLocal`, FastAPI `get_session` dependency |
| `db/smoke.py` | CLI smoke-test script: `python -m db.smoke` |
| `db/models/__init__.py` | declarative `Base` + model re-exports (empty re-exports for now) |
| `auth/__init__.py` | empty package marker |
| `storage/__init__.py` | empty package marker |
| `migration/__init__.py` | empty package marker |

Nothing in the existing codebase is modified except `requirements.txt` and `.env`.

---

## Task 1: Add Python Dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Append new dependencies to `requirements.txt`**

Open `requirements.txt` and append these lines at the end (keep existing lines intact):

```
sqlalchemy>=2.0,<3.0
alembic>=1.13,<2.0
psycopg2-binary>=2.9,<3.0
azure-storage-blob>=12.19,<13.0
PyJWT>=2.8,<3.0
bcrypt>=4.1,<5.0
passlib[bcrypt]>=1.7.4,<2.0
```

- [ ] **Step 2: Install the new dependencies into the project venv**

Run (from `d:/bai/humasql`, using git-bash):

```bash
source venv/Scripts/activate && pip install -r requirements.txt
```

Expected: pip installs seven new packages + transitive deps. No errors. Final line similar to `Successfully installed ...`.

- [ ] **Step 3: Verify imports work**

Run:

```bash
source venv/Scripts/activate && python -c "import sqlalchemy, alembic, psycopg2, azure.storage.blob, jwt, bcrypt, passlib.hash; print('all imports ok')"
```

Expected output: `all imports ok`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "Add SQLAlchemy, Alembic, psycopg2, Azure Blob, PyJWT, bcrypt deps"
```

---

## Task 2: Environment Variable Template

**Files:**
- Create: `.env.example`
- Modify: `.env` (gitignored — create if missing; do NOT commit)

- [ ] **Step 1: Create `.env.example`**

Create file at `d:/bai/humasql/.env.example` with exact content:

```
# OpenAI / existing
OPENAI_API_KEY=

# Firebase (still required during migration — can be removed in Phase 8)
FIREBASE_CREDENTIALS_PATH=firebaseadmn.json

# Azure Postgres
# Password special chars must be URL-encoded (@ -> %40)
DATABASE_URL=postgresql+psycopg2://diltak_db:Backend%40DB14@diltakdb.postgres.database.azure.com:5432/postgres?sslmode=require

# Auth
# Generate with: python -c "import secrets; print(secrets.token_urlsafe(48))"
JWT_SECRET=
JWT_ACCESS_MINUTES=15
JWT_REFRESH_DAYS=30

# Azure Blob Storage (provision in Phase 5, leave blank for now)
AZURE_STORAGE_CONNECTION_STRING=
AZURE_STORAGE_ACCOUNT_NAME=

# Resend (already in use for email)
RESEND_API_KEY=
```

- [ ] **Step 2: Update local `.env` with the Azure Postgres URL**

If `d:/bai/humasql/.env` does not exist, create it by copying `.env.example`. Then set the `DATABASE_URL` value to the same string shown above (it contains the real credentials the user provided).

Command to create / append (only run if not already set — check first):

```bash
grep -q "^DATABASE_URL=" d:/bai/humasql/.env 2>/dev/null || echo 'DATABASE_URL=postgresql+psycopg2://diltak_db:Backend%40DB14@diltakdb.postgres.database.azure.com:5432/postgres?sslmode=require' >> d:/bai/humasql/.env
```

Also generate and set a JWT_SECRET:

```bash
grep -q "^JWT_SECRET=" d:/bai/humasql/.env 2>/dev/null || echo "JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" >> d:/bai/humasql/.env
```

- [ ] **Step 3: Verify `.env` is gitignored**

Run:

```bash
git check-ignore d:/bai/humasql/.env
```

Expected output: `d:/bai/humasql/.env` (the file path itself, meaning it IS ignored).
If no output / exit code 1, stop and investigate `.gitignore`.

- [ ] **Step 4: Commit `.env.example` only**

```bash
git add .env.example
git commit -m "Add .env.example with Azure Postgres and JWT config"
```

Verify with `git status` that `.env` is NOT listed as untracked or modified at commit time.

---

## Task 3: Create Package Skeletons

**Files:**
- Create: `db/__init__.py`, `db/models/__init__.py`, `auth/__init__.py`, `storage/__init__.py`, `migration/__init__.py`

- [ ] **Step 1: Create empty package markers**

Create these five files with the exact contents shown:

`d:/bai/humasql/db/__init__.py`:
```python
"""Database layer: SQLAlchemy session, models, and migration helpers."""
```

`d:/bai/humasql/auth/__init__.py`:
```python
"""Authentication: JWT issuance, password hashing, FastAPI dependencies."""
```

`d:/bai/humasql/storage/__init__.py`:
```python
"""File storage backed by Azure Blob Storage."""
```

`d:/bai/humasql/migration/__init__.py`:
```python
"""One-shot ETL scripts for Firestore → Postgres / Firebase Storage → Azure Blob."""
```

`d:/bai/humasql/db/models/__init__.py`:
```python
"""SQLAlchemy declarative models.

Import all model modules here once they exist so ``Base.metadata`` sees every table
when Alembic autogenerates migrations. Keep this file's imports lazy-safe — never
import from application code that would create a circular dependency.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


__all__ = ["Base"]
```

- [ ] **Step 2: Verify packages import cleanly**

Run:

```bash
source venv/Scripts/activate && python -c "import db, db.models, auth, storage, migration; from db.models import Base; print('packages ok, Base =', Base)"
```

Expected: `packages ok, Base = <class 'db.models.Base'>`

- [ ] **Step 3: Commit**

```bash
git add db auth storage migration
git commit -m "Create db, auth, storage, migration package skeletons"
```

---

## Task 4: SQLAlchemy Session Module

**Files:**
- Create: `db/session.py`

- [ ] **Step 1: Write `db/session.py`**

Create `d:/bai/humasql/db/session.py` with this exact content:

```python
"""SQLAlchemy engine, session factory, and FastAPI dependency.

Reads ``DATABASE_URL`` from the environment (loaded via ``python-dotenv``).
Azure Postgres requires SSL — the URL must include ``?sslmode=require``.

Usage in FastAPI::

    from fastapi import Depends
    from sqlalchemy.orm import Session
    from db.session import get_session

    @router.get("/items")
    def list_items(db: Session = Depends(get_session)):
        return db.query(Item).all()
"""

from __future__ import annotations

import os
from typing import Iterator

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()

_DATABASE_URL = os.environ.get("DATABASE_URL")

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Return the process-wide SQLAlchemy engine, creating it on first call."""
    global _engine
    if _engine is None:
        if not _DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL is not set. Copy .env.example to .env and fill it in."
            )
        _engine = create_engine(
            _DATABASE_URL,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=10,
            future=True,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the process-wide session factory, creating it on first call."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            future=True,
        )
    return _SessionLocal


def get_session() -> Iterator[Session]:
    """FastAPI dependency that yields a session and guarantees close."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
```

- [ ] **Step 2: Verify the module imports without connecting**

Run:

```bash
source venv/Scripts/activate && python -c "from db.session import get_engine, get_session_factory, get_session; print('session module ok')"
```

Expected: `session module ok`. No connection attempted yet (lazy).

- [ ] **Step 3: Commit**

```bash
git add db/session.py
git commit -m "Add SQLAlchemy engine, session factory, and FastAPI dependency"
```

---

## Task 5: Connectivity Smoke Test

**Files:**
- Create: `db/smoke.py`

- [ ] **Step 1: Write `db/smoke.py`**

Create `d:/bai/humasql/db/smoke.py` with this exact content:

```python
"""Smoke-test script: verify we can connect to Azure Postgres.

Run with::

    python -m db.smoke

Exits 0 on success, 1 on failure. Prints the Postgres server version on success.
"""

from __future__ import annotations

import sys

from sqlalchemy import text

from db.session import get_engine


def main() -> int:
    try:
        engine = get_engine()
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version()"))
            version = result.scalar_one()
        print(f"OK: connected to Azure Postgres")
        print(f"Server version: {version}")
        return 0
    except Exception as exc:  # noqa: BLE001 — we want the full error surfaced
        print(f"FAIL: could not connect to Postgres", file=sys.stderr)
        print(f"Error: {exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run the smoke test**

Run:

```bash
source venv/Scripts/activate && python -m db.smoke
```

Expected output (exit code 0):
```
OK: connected to Azure Postgres
Server version: PostgreSQL 16.x ...
```

If it fails with a network / SSL / auth error, stop and investigate before proceeding. Common causes:
- Azure Postgres firewall does not include the current machine's public IP → add it in Azure Portal → Postgres → Networking.
- `DATABASE_URL` missing `sslmode=require`.
- `@` in password not URL-encoded as `%40`.

- [ ] **Step 3: Commit**

```bash
git add db/smoke.py
git commit -m "Add Azure Postgres connectivity smoke test (python -m db.smoke)"
```

---

## Task 6: Update `.gitignore` for Alembic & Azure Artifacts

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Append entries to `.gitignore`**

Open `d:/bai/humasql/.gitignore` and append at the end:

```
# Alembic local artifacts (committed migrations live under alembic/versions/ — only ignore caches)
alembic/__pycache__/

# Azure local dev artifacts
.azurite/
azurite-data/
```

- [ ] **Step 2: Verify `.gitignore` still ignores `.env`**

Run:

```bash
git check-ignore d:/bai/humasql/.env
```

Expected: prints `d:/bai/humasql/.env`.

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "Ignore Alembic cache and Azurite local dev artifacts"
```

---

## Task 7: Verify App Still Runs on Firebase

This phase is additive only — the existing Firestore-backed app must still run exactly as before.

- [ ] **Step 1: Start the app**

Run:

```bash
source venv/Scripts/activate && uvicorn main:app --host 127.0.0.1 --port 8000 --log-level info
```

Expected: app starts, no ImportError, Firestore initialization messages print as before.

- [ ] **Step 2: Hit the health endpoint**

In a second terminal:

```bash
curl -s http://127.0.0.1:8000/health
```

Expected JSON: `{"status":"ok","api_key_set":...,"rag_chunks":...}`

- [ ] **Step 3: Hit the OpenAPI docs**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/docs
```

Expected: `200`

- [ ] **Step 4: Stop the server**

Ctrl+C in the first terminal.

- [ ] **Step 5: No commit needed** — this task only verifies nothing regressed. If any step failed, stop and investigate before starting Phase 2.

---

## Phase 1 Exit Criteria

All boxes below must be checked before Phase 2:

- [ ] `pip install -r requirements.txt` succeeds in the venv.
- [ ] `python -c "import sqlalchemy, alembic, psycopg2, azure.storage.blob, jwt, bcrypt, passlib.hash"` passes.
- [ ] `python -c "from db.session import get_session; from db.models import Base"` passes.
- [ ] `python -m db.smoke` returns exit code 0 and prints the Postgres server version.
- [ ] `uvicorn main:app` still starts and `/health` returns 200.
- [ ] `.env.example` is committed; `.env` is NOT committed.
- [ ] At least 6 commits on the branch for this phase (one per task that produced files).

When all boxes are checked, report back and I'll write the Phase 2 plan (schema + Alembic initial migration).
