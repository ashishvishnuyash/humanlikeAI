# Bug Fixes Plan

**Diagnosed on:** 2026-04-15

---

## Priority Order

| # | Bug | Severity | Effort |
|---|---|---|---|
| 1 | Deactivated users can still log in | **CRITICAL — Security** | Low |
| 2 | Bulk creation silent failures | High — Data corruption | Medium |
| 3 | Deactivated users shown in employee list | Medium — Wrong data | Low |
| 4 | Pagination missing from list APIs | Medium — Scalability | Medium |
| 5 | Search missing from APIs | Low — UX | Medium |

---

## Bug 1 — Deactivated Users Can Still Log In

**File:** `routers/auth.py:297-353` (`login` function)

**Root Cause:** After Firebase Auth validates credentials (line 310–315), the code fetches the Firestore user profile to get `role` and `company_id` but **never checks `is_active`**. A user with `is_active: false` gets a full valid session.

**Current broken code (auth.py ~line 323–340):**
```python
doc = db.collection("users").document(uid).get()
if doc.exists:
    p = doc.to_dict()
    role = p.get("role", "unknown")
    # ← No is_active check here
    return LoginResponse(...)
```

**Fix — add `is_active` check immediately after fetching the Firestore profile:**
```python
doc = db.collection("users").document(uid).get()
if doc.exists:
    p = doc.to_dict()

    # Block deactivated users before issuing any token
    if not p.get("is_active", True):
        raise HTTPException(
            status_code=403,
            detail="Your account has been deactivated. Please contact your administrator."
        )

    role = p.get("role", "unknown")
    return LoginResponse(...)
```

**Also add to token verification middleware** (wherever Bearer tokens are validated on protected endpoints): if the token is valid but the user's Firestore doc has `is_active: false`, return `403`. This blocks deactivated users even if they kept an existing token from before deactivation.

```python
# In your token verification dependency:
user_doc = db.collection("users").document(uid).get()
if user_doc.exists and not user_doc.to_dict().get("is_active", True):
    raise HTTPException(status_code=403, detail="Account deactivated.")
```

**Impact:** Zero breaking changes. Existing active users unaffected.

---

## Bug 2 — Bulk Employee Creation Silent Failures

**File:** `routers/employee_import.py` (background worker `run_import_job`, lines 263–495)

Four distinct failure points:

---

### Fix 2a — Missing Manager Not Recorded as Error

**Location:** Lines 346–362

**Current code:**
```python
if row.manager_email:
    mgr_uid = email_to_uid.get(row.manager_email) or created_in_job.get(row.manager_email)
    if mgr_uid:
        manager_id = mgr_uid
    else:
        print(f"manager_email not found — skipping")  # ← Silent, no result entry error
```

**Fix:**
```python
if row.manager_email:
    mgr_uid = email_to_uid.get(row.manager_email) or created_in_job.get(row.manager_email)
    if mgr_uid:
        manager_id = mgr_uid
    else:
        result_entry["warnings"] = result_entry.get("warnings", [])
        result_entry["warnings"].append(
            f"Manager '{row.manager_email}' not found in system or this batch — manager link skipped."
        )
```

Surface warnings separately from errors in the job result so the employer can see partial issues without the row being marked as a full failure.

---

### Fix 2b — Auth User Created but Firestore Write Fails (No Cleanup)

**Location:** Lines 395–437

**Current problem:** If `db.collection("users").document(uid).set(doc_data)` at line 398 fails, the Firebase Auth account exists but has no Firestore document. The user is in a broken half-created state. The existing rollback logic (lines 433–437) only fires if `result_entry.get("uid")` is set — but the uid IS set before the Firestore write, so rollback should work. However, the rollback only deletes the Auth account, not any partially-written state.

**Fix — wrap Firestore write in explicit try/except with guaranteed Auth cleanup:**
```python
# After creating Firebase Auth user, set uid immediately
result_entry["uid"] = uid

try:
    db.collection("users").document(uid).set(doc_data)
except Exception as fs_err:
    # Firestore write failed — delete the Auth account to avoid orphaned auth user
    try:
        fb_auth.delete_user(uid)
    except Exception:
        pass
    result_entry["status"] = "failed"
    result_entry["error"] = f"Profile write failed: {str(fs_err)}. Auth account cleaned up."
    continue  # Skip to next row
```

---

### Fix 2c — Manager's `direct_reports` Update Fails Silently

**Location:** Lines 403–410

**Current code:**
```python
try:
    db.collection("users").document(manager_id).update({
        "direct_reports": ArrayUnion([uid]),
    })
except Exception as e:
    print(f"direct_reports update error...")  # ← Silent, no result entry error
```

**Fix:**
```python
try:
    db.collection("users").document(manager_id).update({
        "direct_reports": ArrayUnion([uid]),
        "updated_at": firestore.SERVER_TIMESTAMP,
    })
except Exception as e:
    result_entry["warnings"] = result_entry.get("warnings", [])
    result_entry["warnings"].append(
        f"Employee created but manager's direct_reports list could not be updated: {str(e)}"
    )
```

---

### Fix 2d — Hierarchy Level Miscalculated When Manager Missing

**Location:** Lines 365–367

**Current code:**
```python
hierarchy_level = row.hierarchy_level
if hierarchy_level is None:
    hierarchy_level = (manager_level + 1) if manager_level else 1
```

**Problem:** When manager isn't found, `manager_level` is `None`, so `hierarchy_level` falls back to `1` — same as a top-level manager. Employees with a (missing) manager end up at the wrong level.

**Fix:**
```python
hierarchy_level = row.hierarchy_level
if hierarchy_level is None:
    if manager_level is not None:
        hierarchy_level = manager_level + 1
    elif manager_id:
        # Manager was found in system but level unknown — fetch it
        try:
            mgr_doc = db.collection("users").document(manager_id).get()
            fetched_level = mgr_doc.to_dict().get("hierarchy_level", 1) if mgr_doc.exists else 1
            hierarchy_level = fetched_level + 1
        except Exception:
            hierarchy_level = 2  # Safe default: below top level
    else:
        hierarchy_level = 1  # No manager — top level
```

---

### Fix 2e — Password Reset Email Failure Leaves Unusable Account

**Location:** Lines 335–345 (invite email + password reset link generation)

**Problem:** Firebase Auth account is created with no password. If the password reset email send fails, the user exists in Firebase Auth but can never log in.

**Fix — mark row as warning (not full failure) and expose the reset link in the job result:**
```python
try:
    reset_link = fb_auth.generate_password_reset_link(row.email)
    # Send invite email
    send_invite_email(row.email, reset_link, ...)
    result_entry["invite_sent"] = True
except Exception as email_err:
    result_entry["warnings"] = result_entry.get("warnings", [])
    result_entry["warnings"].append(
        f"Account created but invite email failed: {str(email_err)}. "
        f"Manually send a password reset to {row.email}."
    )
    result_entry["invite_sent"] = False
    # Do NOT fail the row — account was created successfully
```

Also expose `invite_sent: false` rows in the job summary so the employer knows which users need manual invites.

---

## Bug 3 — Deactivated Users Shown in Employee List

**File:** `routers/users.py:294-336` (`list_employees` / `GET /api/employees`)

**Root Cause:** The Firestore query at line 306 fetches ALL users for the company without filtering by `is_active`. The in-memory filter at line 320 is correct but the total `count` at line 334 is computed from the pre-filter list, making pagination counts wrong too.

**Current code:**
```python
query = db.collection("users").where("company_id", "==", company_id)
docs = query.stream()
employees = []
for doc in docs:
    data = doc.to_dict()
    if not include_inactive and not data.get("is_active", True):
        continue  # ← filters in memory AFTER fetching everything
    employees.append(...)
```

**Fix — push the filter to Firestore:**
```python
query = db.collection("users").where("company_id", "==", company_id)

if not include_inactive:
    query = query.where("is_active", "==", True)   # ← Server-side filter

if department:
    query = query.where("department", "==", department)

docs = query.stream()
employees = []
for doc in docs:
    data = doc.to_dict()
    # role_filter still done in-memory (Firestore multi-where limitation)
    if role_filter and data.get("role") != role_filter:
        continue
    employees.append(...)
```

**Note on Firestore composite index:** Adding `.where("company_id", "==", ...).where("is_active", "==", True)` requires a composite index on `(company_id, is_active)`. Add this to your Firestore indexes configuration.

---

## Bug 4 — Pagination Missing from List APIs

**Affected endpoints:**

| Endpoint | File | Lines |
|---|---|---|
| `GET /api/employees` | `routers/users.py` | 288–336 |
| `GET /api/admin/employers` | `routers/super_admin.py` | 251–276 |
| `GET /api/admin/employees` | `routers/super_admin.py` | 375–410 |
| `GET /api/admin/companies` | `routers/super_admin.py` | 519–548 |

**Pagination strategy:** Use **offset-based pagination** (page + limit) for simplicity. For very large datasets in the future, switch to Firestore cursor-based pagination using `.start_after(last_doc)`.

### Standard Pagination Query Parameters (add to all list endpoints)

```python
@router.get("/employees")
async def list_employees(
    # Existing params
    include_inactive: bool = Query(False),
    department: Optional[str] = Query(None),
    role_filter: Optional[str] = Query(None),

    # New pagination params
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    limit: int = Query(20, ge=1, le=100, description="Records per page"),
    ...
):
```

### Standard Pagination Response Wrapper (add to response models)

```python
class PaginatedResponse(BaseModel, Generic[T]):
    data: List[T]
    total: int              # Total matching records (before pagination)
    page: int
    limit: int
    total_pages: int
    has_next: bool
    has_prev: bool
```

### Implementation Pattern for Each Endpoint

```python
# Fetch all matching docs (with server-side filters applied)
all_docs = list(query.stream())
total = len(all_docs)

# Apply in-memory role/search filter if needed
filtered = [d for d in all_docs if passes_filter(d)]
total = len(filtered)

# Slice for current page
offset = (page - 1) * limit
page_docs = filtered[offset : offset + limit]

return {
    "data": page_docs,
    "total": total,
    "page": page,
    "limit": limit,
    "total_pages": math.ceil(total / limit),
    "has_next": offset + limit < total,
    "has_prev": page > 1,
}
```

**Note:** For endpoints with large datasets (`/api/admin/employees`), fetch only the paginated slice from Firestore using `.limit(limit).offset(offset)` — but Firestore's `.offset()` reads and discards skipped docs, so switch to cursor pagination once data exceeds ~10k rows.

---

## Bug 5 — Search Missing from APIs

**Affected endpoints:** Same as pagination (users.py + super_admin.py list endpoints)

**Firestore limitation:** Firestore does not support native substring/full-text search. Options ranked by effort:

| Approach | Effort | Quality | When to use |
|---|---|---|---|
| **Prefix match on indexed field** | Low | Basic | Names/emails with known prefix |
| **In-memory filter after Firestore fetch** | Low | Good (for small datasets) | < 5,000 records per company |
| **Firebase + Algolia sync** | High | Excellent | Large scale full-text search |

**Recommended now:** In-memory filter (fast to build, works for current scale). Add Algolia/Typesense later if needed.

### Add `search` Parameter to List Endpoints

```python
@router.get("/employees")
async def list_employees(
    search: Optional[str] = Query(None, description="Search by name or email"),
    ...
):
```

### Search Filter Implementation

```python
def matches_search(data: dict, search: Optional[str]) -> bool:
    if not search:
        return True
    term = search.lower().strip()
    full_name = f"{data.get('first_name', '')} {data.get('last_name', '')}".lower()
    email = data.get("email", "").lower()
    department = data.get("department", "").lower()
    job_title = data.get("job_title", "").lower()
    return (
        term in full_name
        or term in email
        or term in department
        or term in job_title
    )
```

Apply before pagination slice:

```python
all_docs = list(query.stream())
filtered = [
    d for d in all_docs
    if matches_search(d.to_dict(), search)
    and passes_role_filter(d.to_dict(), role_filter)
]
total = len(filtered)
page_docs = filtered[offset : offset + limit]
```

### Search Fields to Support per Endpoint

| Endpoint | Searchable Fields |
|---|---|
| `GET /api/employees` | first_name, last_name, email, department, job_title |
| `GET /api/admin/employees` | first_name, last_name, email, company_name, role |
| `GET /api/admin/employers` | first_name, last_name, email, company_name |
| `GET /api/admin/companies` | company name, industry |

---

## Summary of File Changes

| File | Changes |
|---|---|
| `routers/auth.py` | Add `is_active` check after Firestore profile fetch; add check to token verification middleware |
| `routers/employee_import.py` | Fix 4 failure points in `run_import_job`: manager warning, Firestore cleanup, direct_reports warning, hierarchy fallback, invite failure handling |
| `routers/users.py` | Push `is_active` filter to Firestore query; add pagination + search params |
| `routers/super_admin.py` | Add pagination + search to 3 list endpoints |
| `firestore.indexes.json` | Add composite index: `(company_id, is_active)` on `users` collection |

## Composite Firestore Indexes Needed

```json
{
  "indexes": [
    {
      "collectionGroup": "users",
      "queryScope": "COLLECTION",
      "fields": [
        { "fieldPath": "company_id", "order": "ASCENDING" },
        { "fieldPath": "is_active", "order": "ASCENDING" }
      ]
    },
    {
      "collectionGroup": "users",
      "queryScope": "COLLECTION",
      "fields": [
        { "fieldPath": "company_id", "order": "ASCENDING" },
        { "fieldPath": "is_active", "order": "ASCENDING" },
        { "fieldPath": "department", "order": "ASCENDING" }
      ]
    }
  ]
}
```
