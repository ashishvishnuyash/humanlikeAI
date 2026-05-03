# Physical Health API — Test Report

**Date:** 2026-05-02
**Branch:** `main` @ `431f03e`
**Scope:** Validates the Physical-Health-related changes pulled from `origin/main` in commits `3536223..431f03e` (PRs #13 `feat/physical_health` and #14 `feat/chat_report`).
**Method:** In-process FastAPI `TestClient` smoke run against the real Azure Postgres DB + real Pinecone + real OpenAI.
**Test script:** [`scripts/physical_health_smoke.py`](../scripts/physical_health_smoke.py)

---

## 1. TL;DR

| Result | Count |
|---|---:|
| **Test cases run** | 16 |
| **Passed** | **16 / 16** ✅ |
| **Behavioral change verified** | `/reports/generate` now accepts ≥1 check-in (was ≥3) |
| **Migration applied** | `a1f3e8b92c47_add_rag_chunk_ids_to_medical_documents` |
| **Upload → RAG → Delete cycle** | Verified end-to-end (Pinecone chunk persisted + cleaned up) |
| **Pinecone integration** | Working (`diltak` index online) |
| **Bugs found** | 1 critical (merge regression), 1 pre-existing (Decimal/float) |

---

## 2. What changed (pulled commits)

```
431f03e Merge pull request #14 from ashishvishnuyash/feat/chat_report
2154f72 Merge pull request #13 from ashishvishnuyash/feat/physical_health
7568c6d fixed min msg for report, and made it less concerning
3536223 fixed bugs
```

Files touched:

| File | Change |
|---|---|
| `routers/physical_health.py` | Upload limit 10 MB → 100 MB; report threshold 3 → 1 check-ins; delete now cleans Pinecone vectors |
| `physical_health_agent.py` | Persists `chunk_ids` to DB on upload; key rename `avg_exercise_minutes` → `avg_exercise_minutes_daily`; dropped stale `db=` kwarg from one call |
| `db/models/physical_health.py` | New column `MedicalDocument.rag_chunk_ids` (`ARRAY(String)`, default `{}`) |
| `alembic/versions/a1f3e8b92c47_…py` | Migration that adds the column |
| `routers/chat_wrapper.py` | `endSession` now requires ≥6 user turns; trims history to current Uma session window; cleans up Uma session after report |
| `report_prompts.py` | Softened tone of report copy and minimum-conversation message |

---

## 3. Test results

```
[PASS] POST /check-in #1                                   -> HTTP 201
[PASS] POST /check-in #2                                   -> HTTP 201
[PASS] GET  /check-ins?days=7                              -> HTTP 200  (total=2)
[PASS] GET  /score                                          -> HTTP 200  (score=7.63 level=high streak=1)
[PASS] GET  /trends?period=7d                               -> HTTP 200
[PASS] POST /reports/generate (NEW: <3 check-ins allowed)  -> HTTP 201  (score=6.55 trend=stable)
[PASS] GET  /reports                                        -> HTTP 200  (total=1)
[PASS] GET  /reports/{report_id}                            -> HTTP 200
[PASS] GET  /medical (pre-upload)                           -> HTTP 200  (empty)
[PASS] POST /medical/upload (DOCX, blood_test)              -> HTTP 202  (doc_id returned)
[PASS] GET  /medical/{id}/status                            -> HTTP 200
[PASS] GET  /medical/{id} detail                            -> HTTP 200
[PASS] rag_chunk_ids persisted after upload                -> 1 chunk   ← Pinecone OK
[PASS] DELETE /medical/{id}                                 -> HTTP 200  (Pinecone vector cleanup ran)
[PASS] GET  /medical (post-delete)                          -> HTTP 200
[PASS] document removed after DELETE                        -> doc gone

SUMMARY: 16/16 passed
teardown: deleted 3 users, 1 company, 3 refresh_tokens
```

### Behavioral change verified

| Endpoint | Before | After | Verified |
|---|---|---|---|
| `POST /api/physical-health/reports/generate` | 422 if `len(rows) < 3` | 422 only if `len(rows) < 1` | ✅ — succeeded with 2 check-ins |

The smoke run created exactly **2** check-ins and called `/reports/generate` with `days=7`. Old code path would have returned `422 Not enough check-in data … Please complete at least 3 check-ins first.` The new code returned `201 Created` with a valid report.

### Endpoints **not** exercised

The following physical-health endpoints still need coverage:

- `POST /ask` — RAG Q&A on own medical history (depends on Pinecone, currently broken; see §4.2)

The medical-document upload/status/detail/delete cycle was exercised in this run with a synthesized DOCX. RAG ingestion was attempted but failed against Pinecone — see §4.2.

---

## 4. Bugs found

### 4.1 Critical — `NameError` in `routers/users.py` (regression from merge)

**Severity:** breaks `POST /api/employees/create` entirely (every employee-creation request → HTTP 500).

**Cause:** The merge commit `1c0ea98 Merge branch 'tetso'` (resolved with `-X theirs` strategy) left a duplicate `log_audit` block in `create_employee`. The duplicate references the non-existent variable `employer_company_id` and passes `db=db`. Both blocks log the same event; the second one is dead-on-arrival.

**Stack trace observed during seed:**
```
File "D:\bai\humasql\routers\users.py", line 300, in create_employee
    company_id=employer_company_id,
               ^^^^^^^^^^^^^^^^^^^
NameError: name 'employer_company_id' is not defined. Did you mean: 'employer_company_uuid'?
```

**Fix:** Removed the duplicate block. The remaining (correct) call uses `company_id=str(employer_company_uuid) if employer_company_uuid else ""`.

**Status:** Fixed locally; **not yet committed/pushed**.

### 4.2 Pre-existing — `credit_manager` Decimal/float type mismatch

**Severity:** non-fatal, but pollutes logs and may silently fail credit decrement.

**Observed log line during `/reports/generate` and `/medical/upload`:**
```
[credit_manager] update error for 64095ff3-…: unsupported operand type(s) for -: 'float' and 'decimal.Decimal'
```

The endpoints still returned 201/202, so no user-facing breakage, but credit decrements are being swallowed. Likely a column read returning `Decimal` while the in-code constant is `float`. Worth filing as its own ticket — **not in scope of the PRs tested here.**

---

## 5. How to reproduce

```bash
# 1. Apply migration (one-time)
python -m alembic upgrade head

# 2. Run the smoke
PYTHONIOENCODING=utf-8 python -m scripts.physical_health_smoke
```

The script self-cleans by calling `api_test_seed.teardown()` even on failure — no orphaned smoketest rows are left in Postgres.

Required environment variables (already in `.env`):
- `DATABASE_URL` — Azure Postgres
- `JWT_SECRET`, `JWT_ACCESS_MINUTES`, `JWT_REFRESH_DAYS`
- `OPENAI_API_KEY`
- `PINECONE_API_KEY`, `PINECONE_INDEX_NAME`, `PINECONE_ENV`
- `AZURE_STORAGE_CONNECTION_STRING`

---

## 6. Recommendations

1. **Push the `users.py` fix immediately.** Employee creation is broken on `main` until this is committed.
2. **File a separate ticket for the `credit_manager` Decimal bug** — silent but real, hitting on every `/reports/generate` and `/medical/upload`.
3. **Consider raising the `chat_wrapper` `≥6 user turns` guard's UX surface** — currently it returns a normal-looking AI message. Frontend may need to know "report was skipped" vs "report was generated" to update its UI state correctly.
4. **Add an integration test for `POST /ask`** — it's the user-facing payoff of the RAG ingestion pipeline (now confirmed working) and currently has zero coverage in this smoke run.

---

*Generated by Claude during a live test session — all results above are from a single run on 2026-05-02.*
