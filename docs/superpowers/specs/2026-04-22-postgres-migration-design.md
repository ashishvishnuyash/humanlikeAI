# Firestore → Azure Postgres Migration Design

**Date:** 2026-04-22
**Scope:** Full migration of data, auth, and file storage off Firebase onto Azure Postgres + Azure Blob Storage.

## 1. Goals & Non-Goals

### Goals
- Replace Firestore with Azure Postgres as the system of record.
- Replace Firebase Auth with a self-issued JWT system (PyJWT + bcrypt).
- Replace Firebase Storage with Azure Blob Storage.
- Preserve all existing API contracts — every current endpoint continues to work with the same request/response shapes.
- Migrate production data and live users with a single maintenance-window cutover.

### Non-Goals
- Rewriting the LangGraph pipeline in `main.py` (it doesn't touch Firestore).
- Changing the RAG store (`rag.py`, `data/documents.json`) — stays as-is.
- Changing public API schemas or URL paths.
- Moving in-memory session dict to Postgres (future work; not blocking cutover).
- Multi-region / read-replica setup (single Azure Postgres instance is sufficient).

## 2. Decisions Ratified (from brainstorming)

| Decision | Choice |
|---|---|
| Migration scope | Full: DB + Auth + Storage |
| Data state | Production data + live users; real ETL required |
| DB access | SQLAlchemy 2.0 + Alembic |
| File storage | Azure Blob Storage |
| Cutover | Big-bang, maintenance window |
| Schema philosophy | Pragmatic relational redesign (fix obvious denormalization; don't rewrite everything) |

## 3. Target Architecture

| Component | Today | Target |
|---|---|---|
| Database | Firestore | Azure Postgres via SQLAlchemy 2.0 |
| Schema migrations | None | Alembic |
| Auth provider | Firebase Auth | Self-hosted: PyJWT HS256 + bcrypt |
| Token verification | `fb_auth.verify_id_token` | Local JWT decode against `JWT_SECRET` |
| File storage | Firebase Storage | Azure Blob Storage |
| Server timestamps | `SERVER_TIMESTAMP` | Postgres `NOW()` / SQLAlchemy `func.now()` |
| Counters | `firestore.Increment(n)` | `UPDATE ... SET x = x + n` |

### Connection
```
DATABASE_URL=postgresql+psycopg2://diltak_db:Backend%40DB14@diltakdb.postgres.database.azure.com:5432/postgres?sslmode=require
```
Azure Postgres requires SSL — `sslmode=require` is non-negotiable. `@` in the password must be URL-encoded as `%40`.

### New Modules
- `db/session.py` — engine, `SessionLocal`, FastAPI `get_session` dependency.
- `db/models/__init__.py` — `Base = declarative_base()`, re-export of all models.
- `db/models/user.py`, `company.py`, `mental_health.py`, `physical_health.py`, `community.py`, `calls.py`, `imports.py` — one file per domain.
- `alembic/` — migrations directory, `env.py` wired to `Base.metadata`.
- `auth/jwt_utils.py` — `create_access_token`, `create_refresh_token`, `decode_token`.
- `auth/password.py` — `hash_password`, `verify_password` (bcrypt, 12 rounds).
- `auth/deps.py` — `get_current_user`, `get_employer_user`, `get_super_admin_user` (same signatures as today's Firebase-based versions, returning the same dict shape).
- `storage/blob.py` — `upload_file`, `delete_file`, `generate_signed_url`.
- `migration/export_firestore.py`, `migration/transform.py`, `migration/import_postgres.py`, `migration/copy_storage.py`.

### Modules to Delete After Cutover
- `firebase_config.py`
- `firebaseadmn.json`
- `firestore.indexes.json`
- `firebase-admin` from `requirements.txt`

## 4. Schema Design

22 tables. User IDs preserve existing Firebase UIDs (stored as TEXT PK on `users`) so no foreign-key reference in any document needs rewriting during ETL. All other PKs are UUID.

### 4.1 Identity

**`users`**
- `id` TEXT PK (Firebase UID preserved; new users get UUID string)
- `email` TEXT UNIQUE NOT NULL
- `password_hash` TEXT (nullable until user completes post-migration password reset)
- `role` TEXT NOT NULL CHECK IN (`'employee'`, `'hr'`, `'employer'`, `'super_admin'`)
- `company_id` UUID FK → `companies.id` ON DELETE SET NULL
- `manager_id` TEXT FK → `users.id` ON DELETE SET NULL (self-ref)
- `department` TEXT
- `is_active` BOOLEAN DEFAULT TRUE
- `profile` JSONB DEFAULT `'{}'` (long-tail fields: phone, avatar_url, onboarding flags, etc.)
- `created_at`, `updated_at` TIMESTAMPTZ
- Indices: `(company_id, is_active)`, `(company_id, is_active, department)`, `(manager_id)`, `(role)`

**Denormalization fixed:** `manager.direct_reports` array → removed. Use `SELECT ... WHERE manager_id = :mgr`.

**`companies`**
- `id` UUID PK
- `name` TEXT NOT NULL
- `owner_id` TEXT FK → `users.id`
- `settings` JSONB DEFAULT `'{}'`
- `employee_count` INTEGER DEFAULT 0 (denormalized for dashboard; maintained in app layer)
- `created_at`, `updated_at`

**`refresh_tokens`**
- `id` UUID PK
- `user_id` TEXT FK → `users.id` ON DELETE CASCADE
- `token_hash` TEXT NOT NULL (bcrypt of refresh token — only hash stored)
- `expires_at` TIMESTAMPTZ
- `revoked` BOOLEAN DEFAULT FALSE
- `created_at`
- Index: `(user_id, revoked)`

### 4.2 Mental Health

**`check_ins`** — id UUID, user_id FK, company_id FK, mood fields, `data` JSONB, created_at. Index: `(company_id, created_at DESC)`, `(user_id, created_at DESC)`.

**`sessions`** — id UUID, user_id FK, company_id FK, `messages` JSONB, `summary` TEXT, created_at, ended_at. Index: `(company_id, created_at DESC)`, `(user_id, created_at DESC)`.

**`mental_health_reports`** — id UUID, user_id FK, company_id FK, `report` JSONB, `risk_level` TEXT, generated_at. Index: `(company_id, generated_at DESC)`, `(user_id, generated_at DESC)`.

**`chat_sessions`** — id UUID, user_id FK, `messages` JSONB, created_at, updated_at.

**`ai_recommendations`** — id UUID, user_id FK, company_id FK, `recommendation` JSONB, `category` TEXT, created_at.

**`interventions`** — id UUID, company_id FK, user_id FK (target), `data` JSONB, `status` TEXT, created_at.

**`escalation_tickets`** — id UUID, company_id FK, user_id FK, assigned_to FK, `status` TEXT, `priority` TEXT, `data` JSONB, created_at, updated_at.

### 4.3 Physical Health

**`physical_health_checkins`** — id UUID, user_id FK, company_id FK, `vitals` JSONB, `symptoms` JSONB, created_at. Index: `(user_id, created_at DESC)`.

**`physical_health_reports`** — id UUID, user_id FK, company_id FK, `report` JSONB, generated_at. Index: `(user_id, generated_at DESC)`.

**`medical_documents`** — id UUID, user_id FK, `filename` TEXT, `blob_url` TEXT (Azure), `mime_type` TEXT, `size_bytes` BIGINT, `extracted_text` TEXT, uploaded_at. Index: `(user_id, uploaded_at DESC)`.

**`wellness_events`** — id UUID, user_id FK, company_id FK, `event_type` TEXT, `data` JSONB, created_at. Index: `(user_id, created_at DESC)`.

### 4.4 Community / Gamification

**`community_posts`** — id UUID, company_id FK, anonymous_profile_id FK, `content` TEXT, `likes` INTEGER DEFAULT 0, `replies` INTEGER DEFAULT 0, `is_approved` BOOLEAN, created_at.

**`community_replies`** — id UUID, post_id FK ON DELETE CASCADE, anonymous_profile_id FK, `content` TEXT, `is_approved` BOOLEAN, created_at. Index: `(post_id, is_approved, created_at)`.

**`anonymous_profiles`** — id UUID, user_id FK UNIQUE, `handle` TEXT UNIQUE, `avatar` TEXT, created_at.

**`user_gamification`** — id UUID, user_id FK UNIQUE, company_id FK, `points` INTEGER, `level` INTEGER, `badges` TEXT[], `streak` INTEGER, updated_at.

**`wellness_challenges`** — id UUID, company_id FK, `title` TEXT, `description` TEXT, `is_active` BOOLEAN, `data` JSONB, starts_at, ends_at, created_at.

### 4.5 Calls

**`calls`** — id UUID, caller_id FK, callee_id FK, `status` TEXT, `start_time`, `answered_at`, `end_time`, `end_reason` TEXT, `ended_by` FK, created_at, updated_at.

**`call_sessions`** — id UUID, call_id FK ON DELETE CASCADE, `status` TEXT, `metadata` JSONB, created_at, updated_at.

### 4.6 Imports

**`import_jobs`** — id UUID, company_id FK, created_by FK, `status` TEXT, `stats` JSONB (rows_processed, rows_succeeded, rows_failed), `errors` JSONB, `blob_url` TEXT (uploaded file), created_at, updated_at.

### 4.7 Cross-cutting Conventions

- All `created_at` / `updated_at`: `TIMESTAMPTZ DEFAULT NOW()`.
- `updated_at` auto-maintained via SQLAlchemy `onupdate=func.now()`.
- All FKs have explicit `ON DELETE` behavior; matches current manual cleanup paths in routers.
- Firestore freeform maps (peek analysis, settings, metadata) → `JSONB`.
- Arrays in Firestore → Postgres `TEXT[]` or `JSONB` (TEXT[] where we need GIN index, JSONB otherwise).
- Indices mirror `firestore.indexes.json` 1:1 plus additional FK indices.

## 5. Auth Replacement

### 5.1 Endpoints

All same paths, same response shapes:

| Method | Path | Notes |
|---|---|---|
| POST | `/api/auth/register` | Employer self-signup. bcrypt hash password, create user row, create company row, issue JWT pair. |
| POST | `/api/auth/login` | Verify password, issue JWT pair. |
| POST | `/api/auth/refresh` | Exchange refresh token for new access token. |
| POST | `/api/auth/logout` | Revoke refresh token. |
| POST | `/api/auth/forgot-password` | Send reset email via Resend. |
| POST | `/api/auth/reset-password` | Consume reset token, set new password. |
| GET | `/api/auth/me` | Decoded user — same shape as today. |
| GET | `/api/auth/profile` | Full profile + company — same shape as today. |
| POST | `/api/auth/refresh-profile` | No-op or re-query; same shape. |

### 5.2 Dependencies (contract preserved)

```python
# auth/deps.py
def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """Returns dict with at minimum: uid, email, role. Same shape as today's Firebase-decoded token."""

def get_employer_user(user_token: dict = Depends(get_current_user)) -> dict:
    """Returns full user profile dict (not ORM object) — same shape as today."""

def get_super_admin_user(user_token: dict = Depends(get_current_user)) -> dict:
    """Returns full user profile dict — same shape as today."""
```

Because the signatures and return shapes are identical, every router importing these continues to work without modification. Only internals change.

### 5.3 JWT Details

- Algorithm: HS256.
- Access token: 15 min, claims `{sub: user_id, role, company_id, exp, iat}`.
- Refresh token: 30 days, opaque token + bcrypt'd copy stored in `refresh_tokens`.
- Secret: `JWT_SECRET` env var (min 32 chars, set in Azure config).
- Password hashing: bcrypt, rounds=12.

## 6. Storage Replacement

### 6.1 Module

```python
# storage/blob.py
def upload_file(container: str, key: str, data: bytes, content_type: str) -> str: ...
def delete_file(url: str) -> None: ...
def generate_signed_url(url: str, expires_in: int = 3600) -> str: ...
```

Uses `azure-storage-blob` with connection string from `AZURE_STORAGE_CONNECTION_STRING` env var.

### 6.2 Containers

| Container | Purpose |
|---|---|
| `medical-documents` | Medical document uploads (from `routers/physical_health.py`) |
| `employee-imports` | CSV/Excel employee rosters (from `routers/employee_import.py`) |
| `profile-avatars` | User avatar images (future — reserved) |

### 6.3 Call-Site Changes

- `routers/physical_health.py` — replace `firebase_admin.storage` calls with `storage.blob.upload_file` / `delete_file`. Store returned URL in `medical_documents.blob_url`.
- `routers/employee_import.py` — same pattern for uploaded roster files.

## 7. ETL / Migration Pipeline

### 7.1 Pre-Cutover (Run in Staging)

1. Provision Azure Postgres and Azure Blob (done / in progress).
2. Apply Alembic migrations → empty schema in staging DB.
3. Firestore snapshot export: `gcloud firestore export gs://humasql-migration-staging`.
4. Download export to local disk or to a VM with access to Azure Postgres.
5. Run ETL scripts (`migration/transform.py` + `migration/import_postgres.py`) against staging DB.
6. Verify: row counts per table match Firestore doc counts; spot-check 10+ records per collection.
7. Copy Firebase Storage objects → Azure Blob via `migration/copy_storage.py`. Rewrite `medical_documents.blob_url` to new Azure URLs.
8. Run the new app against staging DB + Azure Blob. Smoke-test every endpoint. Fix any mapping issues; rerun.

### 7.2 Cutover Window

1. Enable maintenance mode (reverse proxy returns 503 with "back in 2 hours" page).
2. Final Firestore export.
3. Final Firebase Storage copy (incremental — only files newer than last staging copy).
4. Run ETL against production Azure Postgres.
5. Apply schema + data verification checks (row counts, referential integrity).
6. Deploy new app code pointing at Azure Postgres.
7. Send password-reset email to every user (Resend, bulk send with rate limit).
8. Smoke-test critical paths: login flow (after password reset), employer dashboard, one physical health check-in, one chat session, one file upload, one community post.
9. Disable maintenance mode.
10. Monitor error rates for 4 hours; be ready to rollback.

### 7.3 Rollback Plan

- Keep Firestore **read-only** (not deleted) for 14 days post-cutover.
- If catastrophic failure within 24h: redeploy previous commit, flip DNS back, Firestore is already intact.
- After 14 days clean: delete Firestore project.

## 8. Phased Implementation

Each phase leaves the app in a runnable state. Phases are sequential, but tasks within Phase 4 are parallelizable per router.

### Phase 1 — Infrastructure
- Add `sqlalchemy>=2.0`, `alembic`, `psycopg2-binary`, `azure-storage-blob`, `PyJWT`, `bcrypt`, `passlib[bcrypt]` to `requirements.txt`.
- Create `db/session.py`, `db/models/__init__.py`.
- Create `auth/`, `storage/`, `migration/` empty module skeletons.
- Add `.env.example` with `DATABASE_URL`, `JWT_SECRET`, `AZURE_STORAGE_CONNECTION_STRING`, `AZURE_STORAGE_ACCOUNT_NAME`.
- Verify connection to Azure Postgres with a `SELECT 1` smoke test.

### Phase 2 — Schema
- Define all 22 SQLAlchemy models across `db/models/*.py`.
- Initialize Alembic, generate initial migration.
- Apply migration to Azure Postgres.
- Verify tables, indices, and FKs with `\d+` queries.

### Phase 3 — Auth
- Implement `auth/jwt_utils.py`, `auth/password.py`, `auth/deps.py`.
- Rewrite `routers/auth.py` to use Postgres + bcrypt + PyJWT.
- Wire dependencies: every existing router's `Depends(get_current_user)` keeps working because the dependency function is now imported from `auth.deps` but maintains the same return shape.
- Unit tests: register → login → me → refresh → logout cycle.

### Phase 4 — Router Migration
One task per router. Ordered by dependency graph:

1. `users.py`
2. `employer.py`
3. `employer_org.py`
4. `employer_dashboard.py`
5. `employer_insights.py`
6. `super_admin.py`
7. `employee_import.py`
8. `physical_health.py`
9. `reports_escalation.py`
10. `recommendations.py`
11. `voice_calls.py`
12. `community_gamification.py`
13. `chat_wrapper.py`

For each: replace every `db.collection("...").doc(...).get()` / `.set()` / `.update()` / `.stream()` with SQLAlchemy equivalents. Convert `SERVER_TIMESTAMP` → `func.now()`. Convert `Increment` → `UPDATE ... x = x + n`. Preserve response shape exactly.

### Phase 5 — Storage
- Implement `storage/blob.py`.
- Swap Firebase Storage calls in `routers/physical_health.py` and `routers/employee_import.py`.
- Provision Azure Blob containers.
- Test upload / download / delete.

### Phase 6 — ETL
- `migration/export_firestore.py` — read Firestore export JSON files.
- `migration/transform.py` — per-collection transform function → SQL row dicts. Handles type coercion (Firestore Timestamp → TIMESTAMPTZ, DocumentReference → FK string, nested maps → JSONB).
- `migration/import_postgres.py` — bulk insert via SQLAlchemy Core. Runs in dependency order (companies → users → everything else) to satisfy FKs.
- `migration/copy_storage.py` — iterate Firebase Storage objects, upload to Azure Blob, return mapping of old URL → new URL for later SQL update.
- Run end-to-end against a Firestore staging export.

### Phase 7 — Cutover
- Final staging rehearsal — full end-to-end with a fresh Firestore export, timed.
- Schedule maintenance window, notify users 72h in advance.
- Execute cutover runbook (Section 7.2).
- Monitor.

### Phase 8 — Cleanup
- Remove `firebase_config.py`, `firebaseadmn.json`, `firestore.indexes.json`.
- Remove `firebase-admin` from `requirements.txt`.
- Remove Firebase-specific imports across codebase (already unused after Phase 4).
- After 14-day rollback window: delete Firebase project.

## 9. Risk Register

| Risk | Mitigation |
|---|---|
| ETL field-mapping errors silently corrupt data | Staging rehearsal with row-count + spot-check verification; diff reports per table |
| User can't log in post-cutover (password hash unusable) | Forced password reset flow — email sent at cutover; clear UX on login page |
| Azure Postgres connection issues under load | Connection pooling via SQLAlchemy (pool_size=20, max_overflow=10); staging load test before cutover |
| Cutover window overruns | Rehearse twice in staging; time each phase; set hard rollback trigger at 2x expected duration |
| Referential integrity violations during ETL | Import in dependency order; wrap in transaction per table; fail loud if FK missing |
| Signed URL format change breaks medical-document downloads | Phase 5 smoke tests cover upload → URL → signed URL → download round trip |
| Firestore export incomplete | Use `gcloud firestore export` (atomic snapshot); verify export completion status before proceeding |

## 10. Open Items (resolve before Phase 1)

- None currently. All design decisions ratified.
