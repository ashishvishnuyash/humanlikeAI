# Phase 4 — Router Migration (Firestore → SQLAlchemy)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended — one subagent per router) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite every Firestore query in the 13 remaining routers to use SQLAlchemy 2.0 against Azure Postgres. After this phase the `firebase_admin.firestore` import is removed from every router, and every API endpoint operates against Postgres models defined in Phase 2.

**Architecture:** A shared translation module (`db/fs_compat.py`) offers small helpers that cover the 80% case (`model_to_dict`, `apply_server_timestamps`, `increment_column`). Each router gets a self-contained rewrite task — all 13 routers are independent of each other at the translation level, so subagents can safely parallelize them. Response shapes are preserved byte-for-byte so frontends don't break.

**Tech Stack:** SQLAlchemy 2.0 (ORM `Session.query` + `.filter` + `.one_or_none` / `.all`), Postgres JSONB, FastAPI dependency injection.

**Spec reference:** `docs/superpowers/specs/2026-04-22-postgres-migration-design.md` — Section 8, Phase 4.

---

## Router Translation Cookbook (read first, applies to every task)

This cookbook defines the mechanical translation rules. Apply them uniformly when rewriting each router. **Preserve the endpoint's response shape exactly** — keep the same dict keys, the same types (str vs UUID), the same ordering.

### 1. DB dependency

Old:
```python
from firebase_config import get_db

@router.get("/items")
def list_items():
    db = get_db()
    docs = db.collection("items").stream()
    ...
```

New:
```python
from sqlalchemy.orm import Session
from db.session import get_session
from db.models import SomeModel

@router.get("/items")
def list_items(db: Session = Depends(get_session)):
    rows = db.query(SomeModel).all()
    ...
```

### 2. Document get

Old:
```python
doc = db.collection("users").document(uid).get()
if not doc.exists:
    raise HTTPException(404, "not found")
data = doc.to_dict()
```

New:
```python
from db.fs_compat import model_to_dict
from db.models import User

user = db.query(User).filter(User.id == uid).one_or_none()
if user is None:
    raise HTTPException(404, "not found")
data = model_to_dict(user)
```

### 3. Document set (upsert)

Old:
```python
db.collection("users").document(uid).set({"email": email, "role": role})
```

New (insert):
```python
user = User(id=uid, email=email, role=role)
db.add(user)
db.commit()
```

New (upsert — use when we genuinely don't know if the row exists):
```python
existing = db.query(User).filter(User.id == uid).one_or_none()
if existing is None:
    db.add(User(id=uid, email=email, role=role))
else:
    existing.email = email
    existing.role = role
db.commit()
```

### 4. Document update

Old:
```python
db.collection("users").document(uid).update({"role": "hr", "updated_at": SERVER_TIMESTAMP})
```

New:
```python
db.query(User).filter(User.id == uid).update({"role": "hr"})
db.commit()
# updated_at is auto-maintained by onupdate=func.now() — don't set it manually.
```

### 5. Document delete

Old:
```python
db.collection("users").document(uid).delete()
```

New:
```python
db.query(User).filter(User.id == uid).delete()
db.commit()
```

### 6. Collection where / filter

Old:
```python
docs = (
    db.collection("users")
    .where("company_id", "==", cid)
    .where("is_active", "==", True)
    .stream()
)
for doc in docs:
    row = doc.to_dict()
```

New:
```python
rows = (
    db.query(User)
    .filter(User.company_id == cid, User.is_active == True)  # noqa: E712
    .all()
)
for row in rows:
    data = model_to_dict(row)
```

### 7. `where ... in`

Old:
```python
db.collection("users").where("role", "in", ["hr", "admin"]).limit(1).stream()
```

New:
```python
db.query(User).filter(User.role.in_(["hr", "admin"])).limit(1).all()
```

### 8. Order + limit

Old:
```python
db.collection("check_ins").where("user_id", "==", uid).order_by("created_at", direction="DESCENDING").limit(10).stream()
```

New:
```python
db.query(CheckIn).filter(CheckIn.user_id == uid).order_by(CheckIn.created_at.desc()).limit(10).all()
```

### 9. `SERVER_TIMESTAMP`

Old:
```python
from google.cloud.firestore_v1 import SERVER_TIMESTAMP
{"created_at": SERVER_TIMESTAMP, "updated_at": SERVER_TIMESTAMP}
```

New:
Remove the timestamp keys — `created_at` / `updated_at` columns already have `server_default=func.now()` and `onupdate=func.now()` in the models.

### 10. `Increment(n)`

Old:
```python
from google.cloud.firestore_v1 import Increment
db.collection("posts").document(pid).update({"likes": Increment(1)})
```

New:
```python
from sqlalchemy import func
db.query(CommunityPost).filter(CommunityPost.id == pid).update(
    {"likes": CommunityPost.likes + 1}
)
db.commit()
```

### 11. `.add()` (Firestore auto-id)

Old:
```python
_, ref = db.collection("calls").add({"caller_id": a, "callee_id": b})
call_id = ref.id
```

New:
```python
import uuid
call = Call(id=uuid.uuid4(), caller_id=a, callee_id=b)
db.add(call)
db.commit()
call_id = str(call.id)
```

### 12. UUID response serialization

SQLAlchemy returns `uuid.UUID` for UUID columns. Most frontends expect JSON strings, matching the Firestore era. Use `str(value)` when assembling response dicts:

```python
return {
    "id": str(call.id),
    "caller_id": call.caller_id,  # TEXT column — already a str
    ...
}
```

`model_to_dict` in `db/fs_compat.py` does this automatically.

### 13. JSONB columns

JSONB columns (like `User.profile`, `CheckIn.data`) come back as Python dicts automatically. To merge without replacing:

```python
user = db.query(User).filter(User.id == uid).one_or_none()
user.profile = {**(user.profile or {}), "phone": new_phone}
db.commit()
```

Don't assign to a key of the existing dict — SQLAlchemy won't detect the mutation (use `copy.deepcopy` or replace the whole dict as above).

### 14. Self-referential FKs (manager_id, direct_reports)

The spec drops the denormalized `direct_reports[]` array. To get a manager's reports:

Old:
```python
doc = db.collection("users").document(mgr_id).get()
report_ids = doc.to_dict().get("direct_reports", [])
reports = [db.collection("users").document(rid).get().to_dict() for rid in report_ids]
```

New:
```python
reports = db.query(User).filter(User.manager_id == mgr_id, User.is_active == True).all()  # noqa: E712
```

To update a manager's reports, just update each user's `manager_id` — no array writeback.

---

## Task 0: `db/fs_compat.py` Helpers

**Files:**
- Create: `db/fs_compat.py`, `tests/test_fs_compat.py`

- [ ] **Step 1: Write failing test**

Create `d:/bai/humasql/tests/test_fs_compat.py`:

```python
"""Unit tests for db.fs_compat helpers."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from db.fs_compat import model_to_dict
from db.models import Company


def test_model_to_dict_stringifies_uuid():
    cid = uuid.uuid4()
    co = Company(id=cid, name="Acme", settings={"tier": "gold"}, employee_count=5)
    d = model_to_dict(co)
    assert d["id"] == str(cid)
    assert d["name"] == "Acme"
    assert d["settings"] == {"tier": "gold"}
    assert d["employee_count"] == 5


def test_model_to_dict_passes_through_datetimes():
    # ISO strings are friendlier for JSON serialization than datetime objects,
    # but Pydantic response_model will serialize either way. We keep datetimes
    # as-is and let the response layer handle them.
    cid = uuid.uuid4()
    now = datetime.now(timezone.utc)
    co = Company(id=cid, name="x", created_at=now, updated_at=now)
    d = model_to_dict(co)
    assert d["created_at"] == now


def test_model_to_dict_handles_none_fields():
    cid = uuid.uuid4()
    co = Company(id=cid, name="x")
    d = model_to_dict(co)
    assert d["owner_id"] is None
```

- [ ] **Step 2: Run test — fails**

Run:

```bash
cd d:/bai/humasql && source venv/Scripts/activate && pytest tests/test_fs_compat.py -v 2>&1 | tail -10
```

Expected: `ModuleNotFoundError: No module named 'db.fs_compat'`.

- [ ] **Step 3: Implement `db/fs_compat.py`**

Create `d:/bai/humasql/db/fs_compat.py`:

```python
"""Helpers for translating Firestore idioms to SQLAlchemy.

Kept small on purpose — most Firestore patterns have a direct SQLAlchemy
equivalent that reads fine inline. These helpers cover only the repetitive
bits (model-to-dict serialization with UUID stringification).
"""

from __future__ import annotations

import uuid
from typing import Any


def model_to_dict(obj: Any) -> dict:
    """Return a dict of column name -> value for a SQLAlchemy model instance.

    UUID values are stringified so the result is JSON-friendly. datetime
    values pass through unchanged (FastAPI / Pydantic handles them).
    """
    result: dict = {}
    for col in obj.__table__.columns:
        value = getattr(obj, col.name)
        if isinstance(value, uuid.UUID):
            value = str(value)
        result[col.name] = value
    return result
```

- [ ] **Step 4: Run test — passes**

Run:

```bash
cd d:/bai/humasql && source venv/Scripts/activate && pytest tests/test_fs_compat.py -v 2>&1 | tail -10
```

Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
cd d:/bai/humasql && git add db/fs_compat.py tests/test_fs_compat.py && git commit -m "Add model_to_dict helper for Firestore-to-SQLAlchemy translation"
```

---

## Tasks 1-13: Per-Router Rewrites

Each router task follows the same template. **Subagents working on these tasks must read the Cookbook above first.**

### Shared per-router procedure

For every router task:

1. Read the entire current router file to understand every endpoint and the Firestore calls it makes.
2. Produce a replacement file applying the Cookbook rules mechanically.
3. Keep endpoint paths, methods, and response shapes IDENTICAL.
4. Preserve all Pydantic request/response schemas.
5. Preserve imports from `routers.auth` (`get_current_user`, `get_employer_user`, `get_super_admin_user`) — they already work against Postgres after Phase 3.
6. Replace `from firebase_config import get_db` with `from db.session import get_session` + `db: Session = Depends(get_session)`.
7. Remove imports of `firebase_admin` / `firestore_v1` (except `fb_auth` — Firebase Auth is still referenced in some routers for legacy user-creation flows that we migrated; replace those with registration against Postgres directly).
8. Verify `from routers.<name> import router` still works.
9. Verify `python -c "from main import app; print(len(app.routes))"` succeeds.
10. Commit with message `Migrate routers/<name>.py to SQLAlchemy`.

### Router-specific notes

Each task below lists the *distinctive* Firestore usage in that router — the shape of the work beyond the generic cookbook.

---

### Task 1: `routers/voice_calls.py` (167 lines, smallest)

**Files:**
- Modify: `routers/voice_calls.py`

**Distinctive usage:**
- `db.collection('calls').add(...)` → `Call(...)` + `db.add()` + `db.commit()`.
- `db.collection('callSessions').document(ref.id).set(...)` → `CallSession(call_id=call.id, ...)` (1:1 with call).
- `SERVER_TIMESTAMP` on `status='active'/'rejected'/'ended'` updates → remove; `updated_at` auto-maintained.
- The old code uses camelCase keys (`answeredAt`, `endTime`) in Firestore. **Response shape preservation required** — keep the camelCase keys in the response even though the Postgres columns are snake_case. Map in the response dict.

- [ ] **Step 1: Read the current file end-to-end**

Run:

```bash
cat d:/bai/humasql/routers/voice_calls.py
```

Take note of every endpoint, response shape, and Firestore call.

- [ ] **Step 2: Rewrite the file**

Replace `d:/bai/humasql/routers/voice_calls.py` using the Cookbook rules. Import `Call`, `CallSession` from `db.models`. Use `Depends(get_session)` for `db`. Map Postgres snake_case fields to camelCase keys in the response dict.

- [ ] **Step 3: Verify imports and app boot**

Run:

```bash
cd d:/bai/humasql && source venv/Scripts/activate && python -c "from routers.voice_calls import router; from main import app; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
cd d:/bai/humasql && git add routers/voice_calls.py && git commit -m "Migrate routers/voice_calls.py to SQLAlchemy"
```

---

### Task 2: `routers/reports_escalation.py` (256 lines)

**Files:**
- Modify: `routers/reports_escalation.py`

**Distinctive usage:**
- Aggregates `mental_health_reports` per company.
- `where('role', 'in', ['hr', 'admin'])` → use `.in_(...)`.
- Creates `escalation_tickets` docs with `add()` → use `EscalationTicket(...)`.
- Returns company-scoped metrics; no subcollections.

- [ ] **Step 1: Read the current file**

```bash
cat d:/bai/humasql/routers/reports_escalation.py
```

- [ ] **Step 2: Rewrite using the Cookbook**

Import `EscalationTicket`, `MentalHealthReport`, `User` from `db.models`.

- [ ] **Step 3: Verify**

```bash
cd d:/bai/humasql && source venv/Scripts/activate && python -c "from routers.reports_escalation import router; from main import app; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
cd d:/bai/humasql && git add routers/reports_escalation.py && git commit -m "Migrate routers/reports_escalation.py to SQLAlchemy"
```

---

### Task 3: `routers/chat_wrapper.py` (305 lines)

**Files:**
- Modify: `routers/chat_wrapper.py`

**Distinctive usage:**
- Calls the existing in-process chat agent (main.py `graph.invoke`), then persists `mental_health_reports` via `.add()`.
- Reads `chat_sessions` to find recent session, appends message, persists back.

- [ ] **Step 1: Read the current file**

```bash
cat d:/bai/humasql/routers/chat_wrapper.py
```

- [ ] **Step 2: Rewrite**

Import `ChatSession`, `MentalHealthReport` from `db.models`. Use UUID for chat session IDs (generated via `uuid.uuid4()`). Message array lives in `ChatSession.messages` JSONB — to append, re-assign a new list (see Cookbook rule 13).

- [ ] **Step 3: Verify**

```bash
cd d:/bai/humasql && source venv/Scripts/activate && python -c "from routers.chat_wrapper import router; from main import app; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
cd d:/bai/humasql && git add routers/chat_wrapper.py && git commit -m "Migrate routers/chat_wrapper.py to SQLAlchemy"
```

---

### Task 4: `routers/recommendations.py` (352 lines)

**Files:**
- Modify: `routers/recommendations.py`

**Distinctive usage:**
- Reads `chat_sessions` to build LLM context.
- Writes `ai_recommendations` via `.add()`.
- No subcollections, no batch writes.

- [ ] **Step 1: Read the current file**

```bash
cat d:/bai/humasql/routers/recommendations.py
```

- [ ] **Step 2: Rewrite**

Import `AIRecommendation`, `ChatSession`, `User` from `db.models`.

- [ ] **Step 3: Verify**

```bash
cd d:/bai/humasql && source venv/Scripts/activate && python -c "from routers.recommendations import router; from main import app; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
cd d:/bai/humasql && git add routers/recommendations.py && git commit -m "Migrate routers/recommendations.py to SQLAlchemy"
```

---

### Task 5: `routers/community_gamification.py` (377 lines)

**Files:**
- Modify: `routers/community_gamification.py`

**Distinctive usage:**
- Heavy use of `Increment(1)` for `likes` and `replies` counters → use Cookbook rule 10.
- `where(..., 'in', [...])` for status filters.
- Badge array updates on `user_gamification.badges` — that's a Postgres `ARRAY(String)` column, so append via `badges + ['new_badge']` in the model attribute, then commit. SQLAlchemy's default ARRAY mutability means re-assign to trigger dirty state: `ug.badges = [*ug.badges, "badge"]`.

- [ ] **Step 1: Read the current file**

```bash
cat d:/bai/humasql/routers/community_gamification.py
```

- [ ] **Step 2: Rewrite**

Import `AnonymousProfile`, `CommunityPost`, `CommunityReply`, `UserGamification`, `WellnessChallenge` from `db.models`. Replace every `Increment(n)` with the UPDATE-with-expression pattern.

- [ ] **Step 3: Verify**

```bash
cd d:/bai/humasql && source venv/Scripts/activate && python -c "from routers.community_gamification import router; from main import app; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
cd d:/bai/humasql && git add routers/community_gamification.py && git commit -m "Migrate routers/community_gamification.py to SQLAlchemy"
```

---

### Task 6: `routers/employer.py` (561 lines)

**Files:**
- Modify: `routers/employer.py`

**Distinctive usage:**
- Employer/HR CRUD for users within a company.
- Uses `fb_auth.create_user`, `fb_auth.delete_user` — remove; user creation happens via `/api/auth/register` only. For admin-created users, insert directly into Postgres with `password_hash=None` and flag `is_active=True` — they reset password on first login just like migrated users.
- Cascading deletes of company → users.

- [ ] **Step 1: Read the current file**

```bash
cat d:/bai/humasql/routers/employer.py
```

- [ ] **Step 2: Rewrite**

Import `Company`, `User` from `db.models`. Replace every `fb_auth.create_user(...)` with a direct `User(...)` insert. Remove the `firebase_auth:` error-reporting branches. Cascading deletes can rely on the `ON DELETE CASCADE` FKs defined in Phase 2 schema.

- [ ] **Step 3: Verify**

```bash
cd d:/bai/humasql && source venv/Scripts/activate && python -c "from routers.employer import router; from main import app; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
cd d:/bai/humasql && git add routers/employer.py && git commit -m "Migrate routers/employer.py to SQLAlchemy; drop fb_auth.create_user"
```

---

### Task 7: `routers/employee_import.py` (678 lines)

**Files:**
- Modify: `routers/employee_import.py`

**Distinctive usage:**
- Reads CSV/Excel, validates rows, creates users in bulk.
- Firebase Storage upload for the original file — **keep in place** for now (Phase 5 swaps it to Azure Blob).
- `import_jobs` progress tracking — updates happen throughout the job.
- Uses `fb_auth.create_user` per row — replace with direct `User(...)` inserts.
- `denormalized_manager_id` lookups — replace with FK `User.manager_id`.

- [ ] **Step 1: Read the current file**

```bash
cat d:/bai/humasql/routers/employee_import.py
```

- [ ] **Step 2: Rewrite**

Import `Company`, `ImportJob`, `User` from `db.models`. Keep the `firebase_admin.storage` import and file-upload code unchanged — mark those lines with `# TODO: replaced in Phase 5 (Azure Blob)`. Replace every `fb_auth.create_user` with `User(...)` insert. Replace `import_jobs` Firestore updates with `db.query(ImportJob).filter(...).update(...)` calls.

- [ ] **Step 3: Verify**

```bash
cd d:/bai/humasql && source venv/Scripts/activate && python -c "from routers.employee_import import router; from main import app; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
cd d:/bai/humasql && git add routers/employee_import.py && git commit -m "Migrate routers/employee_import.py to SQLAlchemy (storage swap deferred to Phase 5)"
```

---

### Task 8: `routers/employer_org.py` (687 lines)

**Files:**
- Modify: `routers/employer_org.py`

**Distinctive usage:**
- Org-chart view: company → managers → direct-reports tree.
- `where("company_id", "==", cid).stream()` → `.filter(User.company_id == cid).all()`.
- Interventions CRUD — straightforward table.
- No complex aggregations.

- [ ] **Step 1: Read the current file**

```bash
cat d:/bai/humasql/routers/employer_org.py
```

- [ ] **Step 2: Rewrite**

Import `Company`, `Intervention`, `User` from `db.models`. Build org tree by querying users and grouping by `manager_id` in Python (acceptable at this scale).

- [ ] **Step 3: Verify**

```bash
cd d:/bai/humasql && source venv/Scripts/activate && python -c "from routers.employer_org import router; from main import app; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
cd d:/bai/humasql && git add routers/employer_org.py && git commit -m "Migrate routers/employer_org.py to SQLAlchemy"
```

---

### Task 9: `routers/employer_insights.py` (856 lines)

**Files:**
- Modify: `routers/employer_insights.py`

**Distinctive usage:**
- Aggregations across `check_ins`, `sessions`, `mental_health_reports`.
- Several compute functions (`_compute_team_size`, etc.) take a `stream` iterable — refactor to take a Query and call `.all()`/`.count()` inline.
- Time-range filters (`where("created_at", ">=", ...)`) → `filter(CheckIn.created_at >= ...)`.

- [ ] **Step 1: Read the current file**

```bash
cat d:/bai/humasql/routers/employer_insights.py
```

- [ ] **Step 2: Rewrite**

Import every mental-health model. Replace the stream-based compute helpers with Query-based ones.

- [ ] **Step 3: Verify**

```bash
cd d:/bai/humasql && source venv/Scripts/activate && python -c "from routers.employer_insights import router, actions_router; from main import app; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
cd d:/bai/humasql && git add routers/employer_insights.py && git commit -m "Migrate routers/employer_insights.py to SQLAlchemy"
```

---

### Task 10: `routers/super_admin.py` (879 lines)

**Files:**
- Modify: `routers/super_admin.py`

**Distinctive usage:**
- Platform-wide aggregations (total companies, total users).
- Full CRUD on any user or company.
- Uses `fb_auth.create_user` for admin-created super admins — replace with direct User insert + `password_hash=None`.
- Already imports `RegisterRequest`/`RegisterResponse` from `routers.auth` — keep (they're aliased to `TokenPair`).

- [ ] **Step 1: Read the current file**

```bash
cat d:/bai/humasql/routers/super_admin.py
```

- [ ] **Step 2: Rewrite**

Import `Company`, `User` from `db.models`. Drop `firebase_admin` imports. Replace `fb_auth.create_user` with direct inserts. Use `.count()` for platform metrics.

- [ ] **Step 3: Verify**

```bash
cd d:/bai/humasql && source venv/Scripts/activate && python -c "from routers.super_admin import router; from main import app; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
cd d:/bai/humasql && git add routers/super_admin.py && git commit -m "Migrate routers/super_admin.py to SQLAlchemy; drop fb_auth usage"
```

---

### Task 11: `routers/employer_dashboard.py` (948 lines)

**Files:**
- Modify: `routers/employer_dashboard.py`

**Distinctive usage:**
- Many TTL-cached endpoints (see `cbfd65a` commit). Cache layer is in-memory, unrelated to DB — keep it.
- Dashboard aggregations (company-scoped) across `check_ins`, `sessions`, `mental_health_reports`, `users`.

- [ ] **Step 1: Read the current file**

```bash
cat d:/bai/humasql/routers/employer_dashboard.py
```

- [ ] **Step 2: Rewrite**

Preserve any in-memory TTL cache decorators. Replace every `.collection(...).stream()` with a Query.

- [ ] **Step 3: Verify**

```bash
cd d:/bai/humasql && source venv/Scripts/activate && python -c "from routers.employer_dashboard import router; from main import app; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
cd d:/bai/humasql && git add routers/employer_dashboard.py && git commit -m "Migrate routers/employer_dashboard.py to SQLAlchemy"
```

---

### Task 12: `routers/physical_health.py` (1055 lines)

**Files:**
- Modify: `routers/physical_health.py`

**Distinctive usage:**
- Medical-document uploads to Firebase Storage — **keep for Phase 5**; add `# TODO: Phase 5` comments where Firebase Storage is called.
- `physical_health_checkins`, `physical_health_reports`, `medical_documents`, `wellness_events` CRUD.
- Report generation agent (imported from `physical_health_agent.py`) — unchanged, but its internal Firestore writes need migration too if present.

- [ ] **Step 1: Read the current file AND physical_health_agent.py**

```bash
cat d:/bai/humasql/routers/physical_health.py
cat d:/bai/humasql/physical_health_agent.py
```

- [ ] **Step 2: Rewrite `routers/physical_health.py`**

Import `MedicalDocument`, `PhysicalHealthCheckin`, `PhysicalHealthReport`, `WellnessEvent`, `User` from `db.models`. Keep Firebase Storage calls verbatim with `# TODO: Phase 5 Azure Blob` comments.

- [ ] **Step 3: Rewrite `physical_health_agent.py`**

This module writes `medical_documents`, `wellness_events`, `physical_health_reports`, and reads `physical_health_checkins`. Migrate each call using the Cookbook. Its functions will need a Session argument or they'll need to open their own session (use `get_session_factory()()`).

- [ ] **Step 4: Verify**

```bash
cd d:/bai/humasql && source venv/Scripts/activate && python -c "from routers.physical_health import router; from main import app; print('ok')"
```

Expected: `ok`.

- [ ] **Step 5: Commit**

```bash
cd d:/bai/humasql && git add routers/physical_health.py physical_health_agent.py && git commit -m "Migrate physical_health router + agent to SQLAlchemy (storage deferred to Phase 5)"
```

---

### Task 13: `routers/users.py` (1111 lines, largest)

**Files:**
- Modify: `routers/users.py`

**Distinctive usage:**
- Most Firestore-heavy file. 40+ distinct queries.
- Manager hierarchy operations (reassign direct reports).
- Bulk user updates per company.
- `fb_auth.create_user` / `fb_auth.delete_user` throughout — replace per Task 6 pattern.
- Cross-collection cleanup on user delete (`check_ins`, `sessions`).

- [ ] **Step 1: Read the current file**

```bash
cat d:/bai/humasql/routers/users.py
```

- [ ] **Step 2: Rewrite**

Import `CheckIn`, `Company`, `MHSession as Session` (or alias), `User` from `db.models`. Replace every `fb_auth.*` with direct model operations. Cross-collection cleanup becomes `db.query(CheckIn).filter(CheckIn.user_id == uid).delete()` — or rely on `ON DELETE CASCADE` FKs.

- [ ] **Step 3: Verify**

```bash
cd d:/bai/humasql && source venv/Scripts/activate && python -c "from routers.users import router; from main import app; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
cd d:/bai/humasql && git add routers/users.py && git commit -m "Migrate routers/users.py to SQLAlchemy (drop fb_auth usage)"
```

---

## Task 14: Full App Smoke

**Files:**
- None (runtime check only)

- [ ] **Step 1: Run all tests**

```bash
cd d:/bai/humasql && source venv/Scripts/activate && pytest 2>&1 | tail -5
```

Expected: all 19+ tests pass (16 from Phase 3 + 3 from Task 0).

- [ ] **Step 2: Start the app and hit /docs**

```bash
cd d:/bai/humasql && source venv/Scripts/activate && uvicorn main:app --host 127.0.0.1 --port 8765 --log-level warning &
sleep 15
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://127.0.0.1:8765/health
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://127.0.0.1:8765/docs
curl -s http://127.0.0.1:8765/openapi.json | python -c "import sys, json; d = json.load(sys.stdin); print(len(d['paths']), 'paths registered')"
```

Expected: both `HTTP 200` lines, and path count >= 40 (every endpoint registered).

- [ ] **Step 3: Stop the server**

Kill the background uvicorn process.

- [ ] **Step 4: Grep for remaining Firestore usage (excluding storage)**

```bash
cd d:/bai/humasql && grep -rn "db.collection\|firestore_v1\|firebase_admin.firestore\|from firebase_config" routers/ --include="*.py" 2>/dev/null; echo "exit=$?"
```

Expected: no matches printed (`exit=1` from grep means "no matches"). If any router still references Firestore, report which file and line — finish that router before closing Phase 4.

- [ ] **Step 5: No commit** — regression check only.

---

## Phase 4 Exit Criteria

- [ ] `db/fs_compat.py` exists with `model_to_dict` and passing tests.
- [ ] All 13 router files have been rewritten, one commit per router.
- [ ] `grep -rn "db.collection" routers/` returns no matches.
- [ ] `grep -rn "firebase_admin.firestore\|from firebase_config" routers/` returns no matches.
- [ ] `uvicorn main:app` starts cleanly; `/openapi.json` lists all expected endpoints.
- [ ] Existing pytest suite (`pytest`) passes green.
- [ ] `physical_health_agent.py` and `routers/physical_health.py` still contain Firebase Storage calls — **expected**, Phase 5 swaps those.

When all boxes are checked, report back and I'll write the Phase 5 plan (Azure Blob swap).
