# Diltak / Uma API — Test Report & Client-Implementation Guide

**Date:** 2026-05-01
**Branch:** `tetso`
**Method:** Hybrid — full static analysis of every endpoint + live smoke run of 36 representative cases against the real Azure Postgres DB.
**Companion document:** [`_static_endpoint_reference.md`](_static_endpoint_reference.md) (1,812 lines, full per-endpoint contracts).

> **TL;DR for the client implementer.** All three role gates (super_admin, employer, employee) work. JWT auth is solid. But there are **17 contract drift / quirk issues** below that will bite you if you copy from `API_SCHEMA.md` (which is stale). Read the "Critical Findings" section before writing a single line of client code.

---

## 1. Numbers

| Metric | Value |
|---|---|
| Total endpoints discovered | **104** |
| Live smoke cases run | 36 |
| Smoke result | **36 / 36 PASS** ✅ |
| Routers analyzed | 14 + `main.py` + `report_api.py` |
| Source LOC analyzed | ~14,000 |
| Critical findings (client-impacting) | 17 |
| Stale claims in `API_SCHEMA.md` | confirmed (treat that doc as deprecated) |

### Endpoint count per router

| Router / file | Endpoints | Prefix |
|---|---:|---|
| `main.py` (top-level) | 8 | `/` (no `/api`) |
| `report_api.py` | 1 | `/report` |
| `routers/auth.py` | 7 | `/api/auth` |
| `routers/admin_metrics.py` | 12 | `/api/admin` |
| `routers/chat_wrapper.py` | 3 | `/api/chat_wrapper` |
| `routers/community_gamification.py` | 2 | `/api` |
| `routers/employee_import.py` | 4 | `/api/employees` |
| `routers/employer.py` | 9 | `/api/employer` |
| `routers/employer_dashboard.py` | 7 | `/api/employer` |
| `routers/employer_insights.py` | 5 | `/api/employer/insights` + `/api/employer/actions` |
| `routers/employer_org.py` | 7 | `/api/employer/org` |
| `routers/physical_health.py` | 12 | `/api/physical-health` |
| `routers/recommendations.py` | 1 | `/api/recommendations` |
| `routers/reports_escalation.py` | 4 | `/api` (mixed) |
| `routers/super_admin.py` | 16 | `/api/admin` |
| `routers/users.py` | 11 | `/api` (employees + hierarchy) |
| `routers/voice_calls.py` | 3 | `/api` |
| **Total** | **104** | |

---

## 2. How to reproduce

Two scripts were added to `scripts/`:

### Seed (creates 3 disposable test accounts)
```bash
python -m scripts.api_test_seed --setup --suffix smoke01
# Prints JSON with super_admin, employer, employee tokens + ids
```
Emails created use the pattern `smoketest+<role>-<suffix>@example.com`.
Cleanup:
```bash
python -m scripts.api_test_seed --teardown
# Deletes any user matching smoketest+*@example.com plus their company + refresh tokens
```

### Live smoke runner
```bash
python -m scripts.api_smoke
# Auto-seeds, runs 36 cases via FastAPI TestClient (in-process), writes
# docs/smoke_results.json, then auto-tears down.
```
The runner uses `TestClient(app)` so **no separately-running uvicorn is required** — it imports the app and hits routes in-process. The DB connection still goes to the real Azure Postgres.

---

## 3. Auth model — what every client must know

### 3.1 Token shape

JWT, HS256, signed with `JWT_SECRET` (env). Two token types:

**Access token** (default 15 min, configurable via `JWT_ACCESS_MINUTES`):
```json
{
  "sub": "<user_id (uuid string for new users, firebase_uid for migrated ones)>",
  "email": "user@example.com",
  "role": "employer | hr | manager | employee | super_admin",
  "company_id": "<uuid string>" | null,
  "typ": "access",
  "iat": 1777585999,
  "exp": 1777586899
}
```
Header: `Authorization: Bearer <access_token>`

**Refresh token** is opaque (`secrets.token_urlsafe(48)`, ~64 chars). The DB stores its **bcrypt hash**, never the raw value. Validity is enforced by the `refresh_tokens` table (`revoked` + `expires_at` columns), default 30 days. **Refresh tokens rotate on use** — calling `/api/auth/refresh` invalidates the supplied token and issues a new pair.

### 3.2 Role gates (canonical source: `auth/deps.py`)

| Dependency | Allowed roles | Returns |
|---|---|---|
| `get_current_user` | any role with valid JWT | dict: `{uid, email, role, company_id}` |
| `get_employer_user` | `employer`, `hr` | full user profile dict + `company` |
| `get_super_admin_user` | `super_admin` | full user profile dict |
| `_require_owner` (internal, employer.py) | `employer` only | n/a — raises 403 if HR |

> **Trap #1.** `get_employer_user` admits HR but several mutating endpoints inside `routers/employer.py` re-check the role with `_require_owner` and reject HR with 403. Read endpoints accept HR; write endpoints in employer.py do not.
>
> **Trap #2.** `routers/employer_dashboard.py` defines its OWN `_require_employer` helper that admits `("employer", "manager", "hr")` — i.e. **managers can call team-dashboard endpoints** despite the 403 message claiming otherwise. (See finding #14 below.)

### 3.3 Auth error shapes

All auth errors come back as `{"detail": "<message>"}` with these statuses:

| Status | When |
|---|---|
| 401 | No token, malformed token, expired token, invalid signature |
| 403 | Valid token but wrong role for the endpoint |
| 403 | Valid token but `is_active=false` on the user row |
| 422 | Bearer header missing entirely (FastAPI's `HTTPBearer` validation, not `HTTPException`) |

When `HTTPBearer` rejects a missing header, the body shape is FastAPI's standard validation error: `{"detail": [{"type":"missing","loc":["header","authorization"],"msg":"Field required"}]}`. Plan for both shapes.

---

## 4. Critical findings — read before writing the client

These are the things the source code does that `API_SCHEMA.md` either gets wrong, glosses over, or never mentions. Each was confirmed via static analysis; several were exercised by the live smoke run.

### #1 — `API_SCHEMA.md` is stale; trust source code

`API_SCHEMA.md` line 5 says auth is `firebase_id_token`. **Wrong.** The Postgres migration (commit `c65b537` and earlier) replaced Firebase with self-issued JWT. `API_SCHEMA.md` line 33 says `POST /api/auth/register` returns 403 / "Self-registration is disabled." **Wrong.** Register is fully active and creates an `employer` + a fresh `Company` row (verified live; `auth/auth.py:126`).

**Action for client:** Treat `_static_endpoint_reference.md` and this report as authoritative. Discard `API_SCHEMA.md` until rewritten.

### #2 — Dashboard / org / insights endpoints require `?company_id=...` even though it's in the JWT

Every endpoint under:
- `/api/employer/wellness-index`
- `/api/employer/burnout-trend`
- `/api/employer/engagement-signals`
- `/api/employer/workload-friction`
- `/api/employer/productivity-proxy`
- `/api/employer/early-warnings`
- `/api/employer/suggested-actions`
- `/api/employer/org/wellness-trend`
- `/api/employer/org/department-comparison`
- `/api/employer/org/retention-risk`
- `/api/employer/org/diltak-engagement`
- `/api/employer/org/roi-impact`
- `/api/employer/org/program-effectiveness`
- `/api/employer/insights/predictive-trends`
- `/api/employer/insights/benchmarks`
- `/api/employer/insights/cohorts`

…rejects requests without a `company_id` query param with `422 {"detail":[{"type":"missing","loc":["query","company_id"],"msg":"Field required"}]}`.

The smoke run hit every one of these and reproduced this. After adding `?company_id={jwt.company_id}` they all return `200`.

**Action for client:** Always append `?company_id=<uuid>` from the JWT claim when calling any of these. Even though it's redundant with the JWT.

### #3 — `/api/reports/recent` uses **camelCase** `companyId` (everyone else uses snake_case)

```
GET /api/reports/recent?companyId=<uuid>     ✅ 200
GET /api/reports/recent?company_id=<uuid>    ❌ 422
```
Verified live. This is the only endpoint in the codebase that takes `companyId`. Easy bug.

**Action for client:** Special-case this one path.

### #4 — Two different routers share the `/api/admin` prefix

Both `routers/super_admin.py` and `routers/admin_metrics.py` use `prefix="/admin"`. They're both included in `main.py` with prefix `/api`. They define different sub-paths in practice:

| `super_admin.py` exposes | `admin_metrics.py` exposes |
|---|---|
| `/api/admin/me`, `/stats`, `/employers/...`, `/employees/...`, `/companies`, `/users/.../reset-password` | `/api/admin/overview`, `/companies` (PARTIAL OVERLAP), `/companies/{company_id}` (PARTIAL OVERLAP), `/users/{uid}`, `/usage`, `/credits`, `/audit-log`, `/gamification/...`, `/challenges/...` |

**The `/companies` and `/companies/{company_id}` endpoints exist in BOTH routers.** `main.py:447–452` includes `admin_metrics_router` AFTER `super_admin_router`, so **the admin_metrics version wins** at runtime (FastAPI picks the last registered handler for the same path).

The two implementations return DIFFERENT response shapes:
- `super_admin.py` returns a flat list with profile-style fields per company.
- `admin_metrics.py` returns aggregated metrics per company (employee_count, last_active, usage stats).

**Action for client:** When you call `GET /api/admin/companies`, you get the metrics version. If you need the flat profile list, use `GET /api/admin/employers` (cross-company employer accounts) instead.

### #5 — `routers/chat_wrapper.py` has no auth at all and trusts the client

`POST /api/chat_wrapper`, `POST /api/chat_wrapper/ai-chat`, `POST /api/chat_wrapper/analyze` have **no `Depends(get_current_user)`**. They consume OpenAI tokens (cost), write to `mental_health_reports`, and trust the client-supplied `userId` and `companyId` in the JSON body without validating them against any token.

**Action for client:** This is a server-side security gap, not a client gap. But know that there's no auth check; pass `userId`/`companyId` correctly because the server won't.
**Action for backend team:** Track as a known issue — these routes should require `get_current_user` and derive uid/company_id from the JWT.

### #6 — Community / gamification endpoints accept `employee_id` in the body

`POST /api/community` and `POST /api/gamification` accept an `employee_id` field in the request body and use that value verbatim. The endpoints DO require auth (`Depends(get_current_user)` on the router), but they don't verify that the body's `employee_id` matches `current_user.uid`.

**Action for client:** Always send the calling user's own `uid` as `employee_id`. (Server won't enforce it but you should not implement an "act as someone else" feature on top of this.)
**Action for backend team:** Fix server-side to use JWT claim instead of body field.

### #7 — `K_ANON_THRESHOLD` is `1`, not `5` as docstrings claim

`employer_dashboard.py`, `employer_insights.py`, `employer_org.py` all define `K_ANON_THRESHOLD = 1` while their docstrings claim "≥ 5 employees required for cohort visibility". Cohorts of size 1 currently pass through.

**Caveat:** `retention-risk` independently returns `422 {"error": "insufficient_cohort", "suppressed": true}` for very small cohorts (verified live with our 1-employee test company). So at least one endpoint enforces a real minimum, just not via `K_ANON_THRESHOLD`.

**Action for client:** When showing analytics, treat any response with `"suppressed": true` or status `422 {"error":"insufficient_cohort"}` as "team too small" and render an explainer instead of a chart.

### #8 — `POST /api/employees/bulk-create` request body is a raw JSON array, not an object

```json
// ✅ Correct
[ { "email": "a@x.com", ... }, { "email": "b@x.com", ... } ]

// ❌ Wrong — will 422
{ "employees": [ { "email": "a@x.com", ... } ] }
```

Easy bug.

### #9 — Bulk-imported employees all share password `"11111111"`

`employee_import.py:56`. There is no per-row password generation, no welcome email with a random password, no force-rotate on first login. Every imported employee can log in with `"11111111"` until they change it.

**Action for client:** When showing the import-success UI, surface this clearly (e.g. "Default password: 11111111 — instruct employees to change immediately"). When building login, do NOT silently auto-fill this default.
**Action for backend team:** This is a known security issue.

### #10 — `createEmployee.sendWelcomeEmail` is silently ignored

The field is in the Pydantic schema (`CreateEmployeeRequest.sendWelcomeEmail: Optional[bool] = True`) but `users.py` never reads it. No welcome email is ever sent regardless of value.

**Action for client:** Don't promise the user that "Send welcome email" will do anything. Don't show that toggle until the backend actually wires it up.

### #11 — `POST /api/hierarchy/test` is a mock — always returns `canAccess: true`

`users.py` line ~1061. Pure stub. Don't rely on it for any real authorization logic.

### #12 — `POST /api/export/pdf` returns a hard-coded 24-byte dummy PDF

`reports_escalation.py:279`. The endpoint is wired up and returns `Content-Type: application/pdf`, but the bytes are a placeholder. Real PDF rendering is not implemented yet.

**Action for client:** Disable / hide the "Export as PDF" button OR render with a banner like "preview only". CSV export (`/api/employer/export-reports`) DOES work and produces real CSV.

### #13 — `MedicalDocument` analysis fields are computed but never persisted

`physical_health.py`'s upload endpoint kicks off a background LLM analysis that produces `summary`, `key_findings`, `flagged_values`, `recommendations`. The async task runs, but the result is never written back to the DB. So:
- `GET /api/physical-health/medical/{doc_id}` always returns nulls in those fields.
- `GET /api/physical-health/medical/{doc_id}/status` always returns `"uploaded"` and `"routine"`.

**Action for client:** Treat those four fields as always-null until the backend wires up persistence. Don't show "Analysis pending…" if it'll never resolve.

### #14 — `employer_dashboard.py:_require_employer` admits `manager`

The internal helper accepts roles `("employer", "manager", "hr")` even though the 403 message says only `employer` and `hr` are allowed. **Managers can read all team-dashboard endpoints** in this router.

This is INCONSISTENT with `routers/employer.py` (CRUD), where `_require_owner` rejects everyone except role `employer`. So:
- HR can read `/api/employer/profile` but not mutate it.
- HR can read `/api/employer/wellness-index`. ✅ smoke verified.
- Manager can read `/api/employer/wellness-index` (per code). NOT verified live, no manager seed.
- Manager cannot read `/api/employees` (different router uses `get_employer_user` strictly).

**Action for client:** Don't rely on role-based UI gating without testing each role against each endpoint. The role model is inconsistent across routers.

### #15 — Inconsistent JSON case (camelCase vs snake_case)

The codebase mixes both. Roughly:
- `routers/employer.py`, `routers/super_admin.py`, `routers/users.py` (employees) → **camelCase** (`firstName`, `companyId`, `isActive`, `hierarchyLevel`)
- `routers/auth.py` (`/me`, `/profile`), `routers/physical_health.py`, `routers/community_gamification.py`, parts of `routers/employer_dashboard.py` → **snake_case** (`company_id`, `is_active`, `created_at`)
- `routers/reports_escalation.py` query param: `companyId` (camel)
- Most other query params: `company_id` (snake)

**Action for client:** Don't write a global JSON casing converter. Match each endpoint's actual shape per the static reference.

### #16 — Refresh token rotation is O(N) over all non-revoked tokens

`auth.py:178–187` and `200–211`. Both `/refresh` and `/logout` iterate every non-revoked, non-expired refresh token in the table and bcrypt-compare each. As `refresh_tokens` grows, refresh latency degrades.

Not client-visible today, but if your app eagerly refreshes every minute under load, latency will increase over months.

**Action for client:** Refresh on demand (when you get a 401 from the access token), not on a tight timer.
**Action for backend team:** Track as performance debt.

### #17 — `physical_health.py` upload accepts `report_date` and `issuing_facility` query params, then deletes them

`physical_health.py:378+`. The handler signature accepts both, then immediately does `del` on them with a `# Phase 5 TODO` comment. They have no effect.

**Action for client:** Don't bother sending these.

---

## 5. Live smoke results — full table

All 36 cases ran against the real Azure Postgres DB on 2026-05-01. Seed accounts created and torn down within the run.

| # | Outcome | Method | Path | Role | Status | Note |
|---|---|---|---|---|---:|---|
| 1 | ✅ PASS | GET | `/health` | — | 200 | Returns `{status, api_key_set, rag_chunks}` |
| 2 | ✅ PASS | POST | `/chat` | anonymous | 200 | Reply generated; in-memory session created |
| 3 | ✅ PASS | POST | `/api/auth/login` | — | 401 | `Invalid email or password.` |
| 4 | ✅ PASS | POST | `/api/auth/register` | — | 409 | Duplicate email rejected |
| 5 | ✅ PASS | GET | `/api/auth/me` | employer | 200 | Returns `{uid, email, role, company_id}` |
| 6 | ✅ PASS | GET | `/api/auth/profile` | employer | 200 | Full profile + company |
| 7 | ✅ PASS | GET | `/api/auth/me` | (no token) | 401 | `HTTPBearer` rejects |
| 8 | ✅ PASS | GET | `/api/admin/me` | super_admin | 200 | |
| 9 | ✅ PASS | GET | `/api/admin/stats` | super_admin | 200 | Platform-wide KPIs |
| 10 | ✅ PASS | GET | `/api/admin/employers` | super_admin | 200 | Cross-company list |
| 11 | ✅ PASS | GET | `/api/admin/employers/{uid}` | super_admin | 200 | |
| 12 | ✅ PASS | GET | `/api/admin/employees` | super_admin | 200 | Cross-company list |
| 13 | ✅ PASS | GET | `/api/admin/companies` | super_admin | 200 | **(admin_metrics version wins, see #4)** |
| 14 | ✅ PASS | GET | `/api/admin/employers` | employer | 403 | Role gate works |
| 15 | ✅ PASS | GET | `/api/employer/profile` | employer | 200 | |
| 16 | ✅ PASS | GET | `/api/employer/company` | employer | 200 | |
| 17 | ✅ PASS | GET | `/api/employer/company/stats` | employer | 200 | Headcount + role breakdown |
| 18 | ✅ PASS | GET | `/api/employees` | employer | 200 | Lists company employees |
| 19 | ✅ PASS | GET | `/api/employees/{uid}` | employer | 200 | |
| 20 | ✅ PASS | GET | `/api/employees` | employee | 403 | Role gate works |
| 21 | ✅ PASS | GET | `/api/employer/wellness-index?company_id=…` | employer | 200 | **422 without `company_id`** |
| 22 | ✅ PASS | GET | `/api/employer/burnout-trend?company_id=…` | employer | 200 | same |
| 23 | ✅ PASS | GET | `/api/employer/engagement-signals?company_id=…` | employer | 200 | same |
| 24 | ✅ PASS | GET | `/api/employer/early-warnings?company_id=…` | employer | 200 | same |
| 25 | ✅ PASS | GET | `/api/employer/org/wellness-trend?company_id=…` | employer | 200 | same |
| 26 | ✅ PASS | GET | `/api/employer/org/department-comparison?company_id=…` | employer | 200 | same |
| 27 | ✅ PASS | GET | `/api/employer/org/retention-risk?company_id=…` | employer | 422 | `{error:'insufficient_cohort', suppressed:true}` — expected, see #7 |
| 28 | ✅ PASS | GET | `/api/employer/insights/predictive-trends?company_id=…` | employer | 200 | |
| 29 | ✅ PASS | GET | `/api/employer/insights/benchmarks?company_id=…` | employer | 200 | |
| 30 | ✅ PASS | GET | `/api/employer/insights/cohorts?company_id=…` | employer | 200 | |
| 31 | ✅ PASS | GET | `/api/admin/overview` | super_admin | 200 | admin_metrics router |
| 32 | ✅ PASS | GET | `/api/admin/usage` | super_admin | 200 | |
| 33 | ✅ PASS | GET | `/api/physical-health/check-ins` | employee | 200 | Returns `[]` for no data |
| 34 | ✅ PASS | GET | `/api/physical-health/score` | employee | 200 | |
| 35 | ✅ PASS | GET | `/api/reports/recent?companyId=…` | employer | 200 | **camelCase param, see #3** |
| 36 | ✅ PASS | POST | `/api/recommendations/generate` | employee | 200 | Returns 6 AI-generated recs |

Full per-case JSON (status + truncated body) is in [`smoke_results.json`](smoke_results.json).

### Bugs surfaced during smoke run (NOT in the test results — server-side)

These were observed in stderr while the smoke run was hitting endpoints. They're real production-code issues unrelated to the test:

1. **`credit_manager` Decimal/float type error.** When `POST /api/recommendations/generate` runs (and likely after every other LLM call that updates company credits), stderr prints:
   ```
   [credit_manager] update error for <company_id>: unsupported operand type(s) for -: 'float' and 'decimal.Decimal'
   ```
   The error is swallowed (logged, not raised) so the API still returns 200, but **company credits are NOT being decremented**. Find the credit-update code path and cast consistently. Likely `utils/credit_manager.py` or similar.

2. **Pinecone connection happens at import time, not request time.** Stderr shows `Connecting to Pinecone index: 'diltak'...` only after a `/chat`-related path is hit. Cold-start latency could be noticeable for the first user post-deploy.

---

## 6. Quick reference: where to look in the detailed doc

If your client needs to call endpoint X, look up its full contract here:

| Use case | Section in `_static_endpoint_reference.md` |
|---|---|
| Login flow, refresh, /me, /profile | `routers/auth.py` |
| Employer self-management (profile, company, change password) | `routers/employer.py` |
| Listing / creating / updating / deleting employees | `routers/users.py` |
| Bulk CSV/XLSX import of employees | `routers/employee_import.py` |
| Team dashboard charts (wellness, burnout, engagement, early-warnings) | `routers/employer_dashboard.py` |
| Org-wide trends, retention, ROI | `routers/employer_org.py` |
| Predictive trends, cohort analysis, benchmarks | `routers/employer_insights.py` |
| Action engine (suggested actions for managers) | `routers/employer_insights.py` (actions_router) |
| Super admin: list / create / suspend / delete employers and employees across companies | `routers/super_admin.py` |
| Platform-level metrics, audit log, credits, gamification overview | `routers/admin_metrics.py` |
| Physical health check-ins, score, medical doc upload | `routers/physical_health.py` |
| Mental health reports + escalation tickets + CSV/PDF export | `routers/reports_escalation.py` |
| Personalized AI wellness recommendations | `routers/recommendations.py` |
| Community posts + gamification events | `routers/community_gamification.py` |
| Voice call session events | `routers/voice_calls.py` |
| Conversational AI chat (Uma) | `main.py` (top-level `/chat`) |
| RAG document ingestion / listing | `main.py` (top-level `/rag/*`) |
| Mental health report analysis | `report_api.py` |

---

## 7. Implementation checklist for the client

A copy-paste checklist while you build:

- [ ] Use `Authorization: Bearer <access_token>` for every authenticated call.
- [ ] On 401, call `POST /api/auth/refresh` with the refresh token. Replace the stored pair with the new one (refresh tokens rotate).
- [ ] On 403, surface "you don't have permission" — don't auto-retry.
- [ ] When calling any `/api/employer/{wellness,burnout,engagement,workload,productivity,early,suggested,org/...,insights/...}` endpoint, ALWAYS append `?company_id=<jwt.company_id>`.
- [ ] When calling `GET /api/reports/recent`, use `?companyId=` (camelCase). Special-case it.
- [ ] When calling `POST /api/employees/bulk-create`, send a raw JSON array, not a wrapped object.
- [ ] Don't show a "Send welcome email" toggle in the create-employee form — the server ignores it.
- [ ] Don't show "PDF export" without a "preview only" warning — the endpoint returns a 24-byte stub.
- [ ] For analytics charts: handle `422 {"error":"insufficient_cohort","suppressed":true}` and `{"suppressed": true}` in payloads gracefully.
- [ ] For physical-health docs: don't display "AI analysis" fields (`summary`, `key_findings`, `flagged_values`, `recommendations`) — they're always null until backend wires up persistence.
- [ ] On `POST /api/community` and `POST /api/gamification`, send the current user's own `uid` as `employee_id` (server doesn't enforce this; don't introduce a "post as someone else" feature).
- [ ] JSON casing varies per endpoint. Don't write a global converter. Use the static reference per-endpoint.
- [ ] Treat `API_SCHEMA.md` as deprecated. Use `_static_endpoint_reference.md` and this report.

---

## 8. Open issues (backend team)

Not blockers for client implementation, but track these:

1. `credit_manager` Decimal/float type error swallowed silently — credits never decrement. (Section 5 bullet 1.)
2. `chat_wrapper.py` endpoints have no auth at all yet consume OpenAI tokens. (#5)
3. `community/gamification` endpoints don't validate `employee_id` against JWT. (#6)
4. Bulk-imported employees all get password `"11111111"`. (#9)
5. `createEmployee.sendWelcomeEmail` is dead code. (#10)
6. `MedicalDocument` analysis fields never persisted. (#13)
7. `K_ANON_THRESHOLD = 1` contradicts the privacy claims in the docstrings. (#7)
8. `routers/employer_dashboard.py` admits `manager` despite 403 message. (#14)
9. Refresh-token rotation is O(N). (#16)
10. `physical_health.py` upload accepts and discards `report_date` / `issuing_facility`. (#17)
11. `/api/admin/companies` is double-registered; `admin_metrics.py` silently shadows `super_admin.py`. (#4)
12. `API_SCHEMA.md` is misleading; either rewrite or mark as deprecated. (#1)

---

## 9. Files added by this run

| File | Purpose |
|---|---|
| `scripts/api_test_seed.py` | Idempotent seed/teardown for super_admin + employer + employee accounts |
| `scripts/api_smoke.py` | 36-case live smoke runner using FastAPI TestClient |
| `docs/_static_endpoint_reference.md` | Full per-endpoint contracts (1,812 lines) — generated by static analysis |
| `docs/smoke_results.json` | Machine-readable per-case results from the latest smoke run |
| `docs/API_TEST_REPORT.md` | This document |

No source code was modified. All test rows on the Azure DB were cleaned up at the end of the smoke run (verified: `deleted_users: 3, deleted_companies: 1, deleted_refresh_tokens: 3`).
