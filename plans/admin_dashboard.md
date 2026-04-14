# Admin Dashboard Plan

**Goal:** Build a super-admin dashboard showing per-user/per-company engagement metrics, model usage tracking, credit limits, KPIs, and audit trail.
**Approach:** Build data layer first (tracking/collections), then API endpoints, then frontend UI.

---

## What Already Exists (Build On These)

- `routers/super_admin.py` — basic platform stats (user/company counts)
- `routers/employer_dashboard.py` — per-company wellness metrics (mood, stress, burnout, engagement %)
- `routers/employer_insights.py` — attrition risk, overload patterns, cohort breakdowns
- Firestore collections: `users`, `sessions`, `check_ins`, `wellness_events`, `user_gamification`
- Privacy/anonymization layer (K-anon, department masking) already in place

## Critical Gaps (Nothing Exists for These)

- **No token/model usage tracking** — zero token counting, no cost records anywhere
- **No credit/billing tracking** — no company credit balances or limits
- **No `last_active_at`** on user documents
- **Uma chat sessions are in-memory only** — lost on restart, no historical data
- **No audit log** — no record of who changed what
- **No cohort retention** — no week-over-week retention curves

---

## Phase 1 — Data Layer (No UI; Foundational)

### 1a. Usage Tracking Middleware

New file: `middleware/usage_tracker.py`

New Firestore collection: **`usage_logs`**

```
usage_logs/{auto_id} = {
    user_id, company_id, timestamp,
    feature,           # "chat" | "report" | "recommendation" | "voice_transcribe" | "embedding"
    model,             # "gpt-4o-mini" | "gpt-4" | "text-embedding-3-large"
    provider,          # "openai" | "azure"
    tokens_in,
    tokens_out,
    total_tokens,
    estimated_cost_usd,
    latency_ms,
    success,
    error              # optional
}
```

**Where to instrument** (all OpenAI-compatible APIs return `usage.prompt_tokens` + `usage.completion_tokens` in the response):

| File | Location | What to Track |
|---|---|---|
| `main.py` | All 7 pipeline nodes | tokens per node + total per request |
| `report_agent.py` | 3 analysis functions (lines 69, 79, 88) | tokens per report |
| `routers/chat_wrapper.py` | Lines 269–277 | tokens per chat call |
| `routers/recommendations.py` | Lines 298–306 | tokens per recommendation |
| `rag.py` | Lines 69–71 | tokens per embedding call |

**Implementation:** Create `track_usage(user_id, company_id, feature, model, provider, response_obj)` utility that reads `response.usage` and async-writes to Firestore.

### 1b. Company Credit Ledger

New Firestore collection: **`company_credits`**

```
company_credits/{company_id} = {
    company_id, company_name,
    plan_tier,                   # "free" | "starter" | "pro" | "enterprise"
    credit_limit_usd,            # Monthly spend cap
    credits_consumed_mtd,        # Month-to-date spend (aggregated from usage_logs)
    credits_remaining,           # Derived: limit - consumed
    warning_threshold_pct,       # Default: 80.0
    last_reset_at,               # Monthly reset date
    total_lifetime_spend_usd,
    updated_at
}
```

Aggregated from `usage_logs` on each write (or background task every 5 min).

### 1c. User Engagement Fields

Add to existing `users/{uid}` documents:

```
last_active_at         # Updated on every authenticated request (async middleware)
first_login_at         # Set once on first login
total_sessions
total_check_ins
total_messages_sent
total_tokens_consumed
total_cost_usd
feature_flags_used[]   # e.g. ["chat", "voice", "report", "recommendation"]
```

Add FastAPI middleware to update `last_active_at` on every authenticated request (async, non-blocking).

### 1d. Persist Chat Sessions to Firestore

Uma chat sessions are currently in-memory only. Add to `main.py` `/chat` endpoint:

New Firestore collection: **`chat_sessions`**

```
chat_sessions/{session_id} = {
    session_id, user_id, company_id,
    started_at, last_message_at,
    message_count, total_tokens,
    is_active
}
```

---

## Phase 2 — Admin API Endpoints

New file: `routers/admin_metrics.py`
Mounted at `/api/admin`, guarded by `super_admin` role.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/admin/overview` | Platform-wide KPI snapshot |
| GET | `/api/admin/companies` | All companies with health + credit summary |
| GET | `/api/admin/companies/{id}` | Detailed single-company drilldown |
| GET | `/api/admin/companies/{id}/users` | Per-user metrics within a company |
| GET | `/api/admin/users/{uid}` | Full single-user KPI card |
| GET | `/api/admin/usage` | Model usage over time (filterable by company/feature/model) |
| GET | `/api/admin/credits` | Credit status per company, alert flags |
| GET | `/api/admin/audit-log` | Paginated admin action history |
| GET | `/api/admin/retention` | Cohort retention curves by company |

### `/api/admin/overview` Response Shape

```json
{
  "total_users": 1240,
  "active_users_today": 87,
  "active_users_7d": 432,
  "total_companies": 34,
  "total_sessions_today": 203,
  "total_tokens_today": 1500000,
  "total_cost_today_usd": 4.20,
  "companies_near_credit_limit": [
    { "company_id": "...", "company_name": "Acme", "usage_pct": 87.3 }
  ],
  "top_features_today": { "chat": 150, "report": 32, "recommendation": 21 },
  "model_breakdown_today": { "gpt-4o-mini": 180, "gpt-4": 23 }
}
```

### `/api/admin/users/{uid}` Response Shape

```json
{
  "user_id": "...",
  "display_name": "...",
  "company_id": "...",
  "role": "employee",
  "last_active_at": "2026-04-14T10:23:00Z",
  "days_since_last_active": 0,
  "engagement_score": 78.4,
  "activity_status": "active",
  "total_sessions": 42,
  "sessions_last_30d": 12,
  "total_check_ins": 67,
  "check_ins_last_30d": 18,
  "total_messages_sent": 390,
  "total_tokens_consumed": 285000,
  "total_cost_usd": 0.84,
  "streak_days": 5,
  "features_used": ["chat", "voice", "report"],
  "mood_trend_7d": [6.2, 6.8, 7.1, 5.9, 6.5, 7.0, 7.3],
  "risk_level": "low"
}
```

---

## Phase 3 — Credit Alert System

New file: `utils/credit_alerts.py`

On each `usage_log` write, check `credits_consumed_mtd / credit_limit_usd`:

| Usage % | Status | Action |
|---|---|---|
| 0–60% | Normal | — |
| 60–80% | Caution | Yellow badge in admin dashboard |
| 80–95% | Warning | Email alert to company admin (via existing Resend) |
| 95–100% | Critical | Email + in-app banner for company admin |
| 100%+ | Limit Reached | Optional: throttle or degrade to cheaper model |

---

## Phase 4 — KPI Definitions

### User-Level KPIs

| KPI | Formula | Source |
|---|---|---|
| Engagement Score | `(sessions_30d/30 × 40%) + (check_ins_30d/30 × 40%) + (streak_days/30 × 20%)` | Computed |
| Activity Status | last_active_at age → Active (<7d), Dormant (7–30d), Churned (>30d) | `users` |
| Session Frequency | `sessions_30d / 30` | `chat_sessions` |
| Token Velocity | `total_tokens_consumed / days_since_join` | `usage_logs` |
| Credit Contribution | `user.total_cost_usd / company.credits_consumed_mtd` | `usage_logs` |
| Feature Breadth | `count(features_used) / 4` (4 = max features) | `users` |

### Company-Level KPIs

| KPI | Formula | Source |
|---|---|---|
| Adoption Rate | `active_users_30d / total_employees` | `users` |
| Retention Rate | % users active this week who were active last week | Computed weekly |
| Credit Burn Rate | `credits_consumed_mtd / days_elapsed_this_month` | `company_credits` |
| Days Until Limit | `credits_remaining / daily_burn_rate` | Derived |
| Cost per Active User | `company_cost_mtd / active_users_30d` | `usage_logs` |
| Feature Utilization | % of available features actually used | `usage_logs` |

### Platform-Level KPIs (Super Admin Only)

| KPI | Description |
|---|---|
| MAU | Users with any activity in last 30 days |
| DAU/MAU Ratio | Stickiness score |
| MoM User Growth | % new users vs prior month |
| Revenue at Risk | Companies with engagement_score < 30 |
| Top Cost Features | Which features drive most token spend |
| Model Cost Trends | Daily/weekly/monthly cost by model |

---

## Phase 5 — Audit Log

New Firestore collection: **`audit_logs`**

```
audit_logs/{auto_id} = {
    actor_uid, actor_role,
    action,       # "user.create" | "user.delete" | "role.change" | "company.update" etc.
    target_type,  # "user" | "company" | "session" | "report"
    target_id,
    changes,      # { "field": { "old": ..., "new": ... } }
    timestamp,
    ip_address,
    user_agent
}
```

**Where to instrument:**

| File | Actions to Log |
|---|---|
| `routers/users.py` | create, update, delete, role change, bulk import |
| `routers/super_admin.py` | any write operations |
| `routers/employer_dashboard.py` | any write operations |

---

## Phase 6 — Frontend (Admin UI)

Separate React/Next.js admin SPA or admin routes added to existing frontend.

### Key Pages

| Page | Content |
|---|---|
| **Overview Dashboard** | Platform KPIs, DAU/cost trend charts, credit-limit alert list |
| **Companies List** | Sortable table: name, plan tier, active users, adoption %, credit %, wellness index |
| **Company Detail** | User table, usage over time charts, wellness metrics, credit ledger |
| **User Detail** | Full KPI card, session history, token breakdown, mood trend chart |
| **Usage & Costs** | Model cost over time, breakdown by feature/model/company, cost projection |
| **Credit Management** | Set/adjust limits per company, configure alert thresholds |
| **Audit Log** | Searchable/filterable admin action history |

---

## Files to Change Summary

| File | Type | Purpose |
|---|---|---|
| `middleware/usage_tracker.py` | **New** | Token + cost tracking wrapper |
| `routers/admin_metrics.py` | **New** | Admin API endpoints |
| `utils/credit_alerts.py` | **New** | Credit threshold alert logic |
| `main.py` | **Modify** | Session persistence + last_active middleware + LLM instrumentation |
| `report_agent.py` | **Modify** | Instrument LLM calls with usage tracker |
| `routers/chat_wrapper.py` | **Modify** | Instrument LLM calls + audit log |
| `routers/recommendations.py` | **Modify** | Instrument LLM calls + audit log |
| `routers/users.py` | **Modify** | Audit log calls + new user engagement fields |
| `rag.py` | **Modify** | Instrument embedding calls |

## New Firestore Collections Summary

| Collection | Purpose | Written By |
|---|---|---|
| `usage_logs` | Per-call LLM token + cost records | `usage_tracker` middleware |
| `company_credits` | Per-company credit balance + limits | Aggregated from `usage_logs` |
| `audit_logs` | Admin action history | Routers that mutate data |
| `chat_sessions` | Persisted Uma chat session metadata | `main.py` `/chat` endpoint |
