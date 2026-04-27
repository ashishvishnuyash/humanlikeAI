# Admin Panel Backend — Full Build Roadmap

> Single source of truth for building the admin backend.
> Merges: admin_dashboard.md + gamification_admin_plan.md + per-employee usage addition.
> Updated to reflect current codebase state.

---

## Current State (What Already Exists)

| What | File | Notes |
|---|---|---|
| `get_super_admin_user` dependency | `routers/auth.py` | Role check against Firestore |
| `GET /api/admin/stats` | `routers/super_admin.py` | Basic counts only — no KPIs |
| Employer/employee/company CRUD | `routers/super_admin.py` | List, get, update, deactivate |
| `get_employer_user` dependency | `routers/auth.py` | Employer/HR role guard |
| Employer aggregated dashboard | `routers/employer_dashboard.py` | Wellness only, no usage data |
| TTL cache layer | `routers/employer_dashboard.py` | 120s cache, reuse this pattern |
| `user_gamification` collection | Firestore | Points, level, badges, streaks |
| `wellness_challenges` collection | Firestore | Active challenges per company |
| `K_ANON_THRESHOLD` | `routers/employer_dashboard.py` | =1, suppress small cohorts |
| `middleware/` folder | — | **Does not exist** |
| `utils/credit_manager.py` | — | **Does not exist** |
| `utils/audit.py` | — | **Does not exist** |
| `routers/admin_metrics.py` | — | **Does not exist** |

### Critical Data Gaps (Nothing in Firestore Yet)
- No `usage_logs` — zero token/cost data
- No `chat_sessions` persisted — Uma chat is in-memory only
- No `last_active_at` on user documents
- No `audit_logs`
- No `company_credits`
- No `gamification_events`

---

## New Firestore Collections

| Collection | Purpose | Created In |
|---|---|---|
| `chat_sessions` | Persisted Uma chat session metadata | Step 1 |
| `usage_logs` | Per-call LLM token + cost records | Step 2 |
| `company_credits` | Per-company credit balance + limit | Step 3 |
| `audit_logs` | Admin action history | Step 4 |
| `gamification_events` | Point-earning event audit log | Step 6 |
| `challenges` | Admin-managed challenges (replaces `wellness_challenges`) | Step 6 |
| `user_challenge_progress` | Per-user per-challenge progress | Step 6 |

---

## New Files

| File | Purpose |
|---|---|
| `middleware/usage_tracker.py` | Token + cost logging per LLM call |
| `utils/credit_manager.py` | Company credit balance update |
| `utils/credit_alerts.py` | Threshold detection + Resend email |
| `utils/audit.py` | Audit log writer |
| `utils/gamification_utils.py` | Shared `award_points()` helper |
| `routers/admin_metrics.py` | All new admin API endpoints |

---

## Step 1 — Session Persistence

**Why first:** Every session-based KPI (DAU, retention, session frequency) is zero
until sessions are persisted. This is the most critical data gap.

**File:** `main.py`

**Decision required:** `/chat` has no auth today. Two options:
- Add optional auth to `/chat` — tag sessions by user when token present
- Only track sessions through `chat_wrapper.py` (already has auth)

Recommendation: add optional auth to `/chat` so the main pipeline is also tracked.

### `chat_sessions` Schema
```
{
    session_id:       str,
    user_id:          str | "anonymous",
    company_id:       str | null,
    started_at:       Timestamp,
    last_message_at:  Timestamp,
    message_count:    int,
    total_tokens:     int,
    is_active:        bool
}
```

**Add to `main.py` `/chat` handler:**
```python
# After graph.invoke() — non-blocking
asyncio.create_task(_persist_chat_session(sid, uid, company_id, message_count, tokens))
```

**Add middleware to update `last_active_at`:**
Every authenticated request updates `users/{uid}.last_active_at` asynchronously.
Add as a FastAPI middleware that fires after the response is sent (use `background_tasks`
or a starlette `BaseHTTPMiddleware` with a non-blocking Firestore write).

**Add to `users/{uid}` documents:**
```
last_active_at:       Timestamp   ← updated on every authenticated request
first_login_at:       Timestamp   ← set once on first login
total_sessions:       int         ← incremented per session
total_check_ins:      int         ← incremented per check-in
total_messages_sent:  int         ← incremented per message
```

---

## Step 2 — Usage Tracking Middleware

**Why second:** All cost/model KPIs feed from here. Build once, wire everywhere.

**New file:** `middleware/usage_tracker.py`

```python
async def track_usage(
    user_id:    str,
    company_id: str,
    feature:    str,    # "chat" | "report" | "recommendation"
                        # | "physical_health" | "embedding"
    model:      str,    # "gpt-4o-mini" | "gpt-4"
    provider:   str,    # "openai" | "azure"
    tokens_in:  int,
    tokens_out: int,
    db,
    metadata:   dict = None,
) -> None:
    """Async-writes one record to usage_logs. Non-blocking."""
```

### `usage_logs` Schema
```
{
    user_id:             str,
    company_id:          str,
    timestamp:           Timestamp,
    feature:             str,
    model:               str,
    provider:            str,
    tokens_in:           int,
    tokens_out:          int,
    total_tokens:        int,
    estimated_cost_usd:  float,
    latency_ms:          int,
    success:             bool,
    error:               str | null
}
```

### Token Capture — Important Caveat

**Direct `llm.invoke()` calls** — read `response.usage_metadata` directly:
```python
response = llm.invoke(...)
tokens_in  = response.usage_metadata.get("input_tokens", 0)
tokens_out = response.usage_metadata.get("output_tokens", 0)
```

**`with_structured_output()` calls** (used in physical_health_agent, report_agent) —
use LangChain callbacks because structured output doesn't expose `.usage_metadata`:
```python
from langchain_core.callbacks import UsageMetadataCallbackHandler
handler = UsageMetadataCallbackHandler()
result = (prompt | structured_llm).invoke({...}, config={"callbacks": [handler]})
tokens_in  = handler.usage_metadata.get("input_tokens", 0)
tokens_out = handler.usage_metadata.get("output_tokens", 0)
```

### Cost Calculation (Azure-Safe)
Don't hardcode OpenAI pricing. Use env vars so Azure migration doesn't break costs:
```env
COST_PER_1K_INPUT_TOKENS=0.00015
COST_PER_1K_OUTPUT_TOKENS=0.0006
```

### Wire Into (Priority Order)

| File | Where | Feature tag |
|---|---|---|
| `routers/chat_wrapper.py` | After LLM call | `"chat"` |
| `routers/recommendations.py` | After LLM call | `"recommendation"` |
| `report_agent.py` | After each LLM call (3 calls) | `"report"` |
| `physical_health_agent.py` | After each LLM call (3 calls) | `"physical_health"` |
| `rag.py` | After embed call | `"embedding"` |

---

## Step 3 — Company Credits

**New file:** `utils/credit_manager.py`

Create `company_credits/{company_id}` on company registration (or lazily on first usage).
After every `track_usage()` call, update credits atomically:

```python
async def update_company_credits(company_id: str, cost_usd: float, db) -> None:
    ref = db.collection("company_credits").document(company_id)
    ref.update({
        "credits_consumed_mtd":  Increment(cost_usd),
        "credits_remaining":     Increment(-cost_usd),
        "total_lifetime_spend":  Increment(cost_usd),
        "updated_at":            SERVER_TIMESTAMP,
    })
```

### `company_credits` Schema
```
{
    company_id:              str,
    company_name:            str,
    plan_tier:               str,    # "free" | "starter" | "pro" | "enterprise"
    credit_limit_usd:        float,
    credits_consumed_mtd:    float,
    credits_remaining:       float,
    warning_threshold_pct:   float,  # default 80.0
    last_reset_at:           Timestamp,
    total_lifetime_spend_usd: float,
    alert_status:            str,    # "normal" | "warning" | "critical"
    updated_at:              Timestamp
}
```

### Alert Thresholds (`utils/credit_alerts.py`)

| Usage % | Status | Action |
|---|---|---|
| 0–80% | `normal` | Nothing |
| 80–95% | `warning` | Email to company admin via Resend |
| 95–100% | `critical` | Email + in-app banner flag |
| 100%+ | `limit_reached` | Optional: throttle to cheaper model |

---

## Step 4 — Audit Log

**New file:** `utils/audit.py`

```python
async def log_audit(
    actor_uid:   str,
    actor_role:  str,
    action:      str,       # "user.create" | "user.deactivate" | "company.update" etc.
    target_type: str,       # "user" | "company" | "session"
    target_id:   str,
    changes:     dict,      # {"field": {"old": x, "new": y}}
    db,
    request=None,           # FastAPI Request — captures IP + user-agent
) -> None:
```

### `audit_logs` Schema
```
{
    actor_uid:   str,
    actor_role:  str,
    action:      str,
    target_type: str,
    target_id:   str,
    changes:     dict,
    timestamp:   Timestamp,
    ip_address:  str | null,
    user_agent:  str | null
}
```

### Wire Into

| File | Actions to Log |
|---|---|
| `routers/users.py` | `user.create`, `user.update`, `user.deactivate`, `user.reactivate`, `user.bulk_import` |
| `routers/super_admin.py` | `user.delete`, `company.update`, `employer.deactivate` |
| `routers/employer.py` | `company.create`, `company.update` |

---

## Step 5 — Admin Metrics Router

**New file:** `routers/admin_metrics.py`
**Mount:** `app.include_router(admin_metrics_router, prefix="/api")` in `main.py`
**Guard:** All endpoints `Depends(get_super_admin_user)`

### Phase A — Available Immediately (Uses Existing Collections)

#### `GET /api/admin/overview`
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

#### `GET /api/admin/companies`
Query params: `page`, `limit`, `search`

Returns per-company summary: name, plan tier, active users, adoption %, credit %, wellness index.

#### `GET /api/admin/companies/{id}`
Full company drilldown: user list + usage over time + wellness metrics + credit ledger.

#### `GET /api/admin/users/{uid}`
Full single-user KPI card:
```json
{
  "user_id": "...",
  "display_name": "...",
  "company_id": "...",
  "role": "employee",
  "last_active_at": "2026-04-24T10:23:00Z",
  "days_since_last_active": 1,
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
  "features_used": ["chat", "voice", "report", "physical_health"],
  "gamification_level": 4,
  "badge_count": 3,
  "mood_trend_7d": [6.2, 6.8, 7.1, 5.9, 6.5, 7.0, 7.3],
  "risk_level": "low"
}
```

### Phase B — Requires Step 1 (Session Persistence)

#### `GET /api/admin/retention`
Week-over-week cohort retention curves by company.
Query: `company_id` (optional), `weeks` (default 12).

#### `GET /api/admin/users/{uid}/sessions`
Paginated session history for a single user.

### Phase C — Requires Steps 2 + 3 (Usage + Credits)

#### `GET /api/admin/usage`
Model usage over time. Query params: `company_id`, `feature`, `model`, `from_date`, `to_date`.
Returns: daily token counts + cost breakdown by feature/model.

#### `GET /api/admin/credits`
Credit status per company — sortable by usage %, shows alert flags.

### Phase D — Requires Step 4 (Audit Log)

#### `GET /api/admin/audit-log`
Paginated, filterable by `actor_uid`, `action`, `target_type`, `from_date`.

---

## Step 6 — Per-Employee Usage (Employer View)

**Why this matters:** Employers need to know which employees are engaging with the platform,
not just aggregate numbers. Currently the employer dashboard only shows anonymised wellness
aggregates — no individual activity visibility.

**File:** `routers/employer_dashboard.py` — add new endpoint

### `GET /api/employer/team-usage`

Guard: `_require_employer()` (already exists in employer_dashboard.py)
Query params: `page` (default 1), `limit` (default 20), `department`, `sort_by` (`last_active | sessions | check_ins | streak`), `status` (`active | dormant | churned`)

**Response:**
```json
{
  "employees": [
    {
      "uid": "...",
      "display_name": "John D.",
      "department": "Engineering",
      "position": "Senior Engineer",
      "sessions_last_30d": 12,
      "check_ins_last_30d": 18,
      "physical_checkins_last_30d": 14,
      "last_active_days_ago": 1,
      "activity_status": "active",
      "features_used": ["chat", "physical_health"],
      "gamification_level": 4,
      "current_streak": 7,
      "engagement_score": 78.4
    }
  ],
  "summary": {
    "total_employees": 87,
    "active_count": 54,
    "dormant_count": 21,
    "churned_count": 12,
    "avg_sessions_30d": 8.2,
    "avg_check_ins_30d": 11.4,
    "participation_rate_pct": 62.1
  },
  "total": 87,
  "page": 1,
  "limit": 20,
  "totalPages": 5,
  "hasNext": true,
  "hasPrev": false
}
```

### Privacy Rules for This Endpoint

| Field | Shown to Employer | Reason |
|---|---|---|
| `display_name` | Yes | Basic identification |
| `department` / `position` | Yes | Org context |
| `sessions_last_30d` | Yes | Activity count only, no content |
| `check_ins_last_30d` | Yes | Opt-in behaviour |
| `last_active_days_ago` | Yes | Engagement signal |
| `features_used` | Yes | Feature breadth, not content |
| `gamification_level` / `streak` | Yes | Non-sensitive |
| `engagement_score` | Yes | Computed metric |
| `total_messages_sent` | **No** | Implies reading chat volume |
| `total_tokens_consumed` | **No** | Internal billing data |
| `total_cost_usd` | **No** | Internal billing data |
| Wellness scores (individual) | **No** | K-anon rule — aggregate only |
| Mood / stress data | **No** | Mental health privacy |

### Activity Status Definition

```
active:   last_active_at within last 7 days
dormant:  last_active_at between 7–30 days ago
churned:  last_active_at > 30 days ago, or never logged in
```

### Engagement Score Formula

```
engagement_score =
  (sessions_30d / 30 × 40%)
+ (check_ins_30d / 30 × 40%)
+ (streak_days / 30 × 20%)

Capped at 100. Rounded to 1 decimal.
```

---

## Step 7 — Gamification Admin Endpoints

Add to `routers/admin_metrics.py` — uses `user_gamification` + `wellness_challenges`
which already exist.

### New Endpoints

```
GET  /api/admin/gamification/overview          → Platform-wide gamification health
GET  /api/admin/gamification/companies/{id}    → Per-company gamification breakdown
POST /api/admin/challenges                     → Create challenge
GET  /api/admin/challenges                     → List all (platform + company-scoped)
PATCH /api/admin/challenges/{id}               → Edit / activate / deactivate
GET  /api/admin/challenges/{id}/stats          → Participants, completions, completion rate
```

### `GET /api/admin/gamification/overview`
```json
{
  "total_active_players": 843,
  "total_points_issued_mtd": 128400,
  "avg_level_platform": 3.2,
  "top_badge": "week_warrior",
  "challenge_completion_rate_pct": 41.2,
  "most_engaged_company": { "company_id": "...", "company_name": "Acme", "avg_points": 920 },
  "points_by_event_type": {
    "daily_checkin": 42000,
    "physical_checkin": 28000,
    "conversation": 38000,
    "challenge_complete": 20400
  },
  "new_badges_this_week": 124
}
```

### `GET /api/admin/gamification/companies/{id}`
```json
{
  "company_id": "...",
  "total_players": 87,
  "active_players_7d": 54,
  "avg_points": 740,
  "avg_level": 3.8,
  "avg_streak": 6.2,
  "badge_distribution": {
    "first_check_in": 72,
    "week_warrior": 34,
    "health_week": 18
  },
  "active_challenges": 2,
  "challenge_participation_pct": 38.5,
  "challenge_completion_pct": 22.1,
  "points_trend_7d": [920, 1040, 880, 1200, 960, 1100, 1380]
}
```

### Employer Gamification View

Also add to `routers/employer_dashboard.py`:

```
GET /api/employer/gamification
```

Returns anonymous leaderboard + challenge stats for the employer's company.
Uses `anonymous_profiles.display_name` — never real names.

---

## Step 8 — Firestore Indexes

Append to `firestore.indexes.json`:

```json
{ "collectionGroup": "chat_sessions", "fields": [
    { "fieldPath": "user_id",    "order": "ASCENDING" },
    { "fieldPath": "started_at", "order": "DESCENDING" }
]},
{ "collectionGroup": "chat_sessions", "fields": [
    { "fieldPath": "company_id", "order": "ASCENDING" },
    { "fieldPath": "started_at", "order": "DESCENDING" }
]},
{ "collectionGroup": "usage_logs", "fields": [
    { "fieldPath": "company_id", "order": "ASCENDING" },
    { "fieldPath": "timestamp",  "order": "DESCENDING" }
]},
{ "collectionGroup": "usage_logs", "fields": [
    { "fieldPath": "user_id",    "order": "ASCENDING" },
    { "fieldPath": "timestamp",  "order": "DESCENDING" }
]},
{ "collectionGroup": "usage_logs", "fields": [
    { "fieldPath": "company_id", "order": "ASCENDING" },
    { "fieldPath": "feature",    "order": "ASCENDING" },
    { "fieldPath": "timestamp",  "order": "DESCENDING" }
]},
{ "collectionGroup": "audit_logs", "fields": [
    { "fieldPath": "actor_uid",  "order": "ASCENDING" },
    { "fieldPath": "timestamp",  "order": "DESCENDING" }
]},
{ "collectionGroup": "gamification_events", "fields": [
    { "fieldPath": "company_id", "order": "ASCENDING" },
    { "fieldPath": "created_at", "order": "DESCENDING" }
]}
```

---

## KPI Definitions

### User-Level KPIs

| KPI | Formula | Source |
|---|---|---|
| Engagement Score | `(sessions_30d/30 × 40%) + (checkins_30d/30 × 40%) + (streak/30 × 20%)` | Computed |
| Activity Status | `last_active_at` age → active (<7d), dormant (7–30d), churned (>30d) | `users` |
| Session Frequency | `sessions_30d / 30` | `chat_sessions` |
| Token Velocity | `total_tokens / days_since_join` | `usage_logs` |
| Credit Contribution | `user.total_cost_usd / company.credits_consumed_mtd` | `usage_logs` |
| Feature Breadth | `count(features_used) / 5` (5 features) | `usage_logs` |
| Gamification Level | `user_gamification.level` | `user_gamification` |

### Company-Level KPIs

| KPI | Formula | Source |
|---|---|---|
| Adoption Rate | `active_users_30d / total_employees` | `users` |
| Retention Rate | `% users active this week who were active last week` | `chat_sessions` |
| Credit Burn Rate | `credits_consumed_mtd / days_elapsed_this_month` | `company_credits` |
| Days Until Limit | `credits_remaining / daily_burn_rate` | Derived |
| Cost per Active User | `company_cost_mtd / active_users_30d` | `usage_logs` |
| Gamification Participation | `% users with any points in last 30d` | `user_gamification` |

### Platform-Level KPIs (Super Admin Only)

| KPI | Description |
|---|---|
| MAU | Users with any activity in last 30 days |
| DAU/MAU | Stickiness ratio |
| MoM User Growth | % new users vs prior month |
| Revenue at Risk | Companies with engagement score < 30 |
| Top Cost Features | Which features drive most token spend |
| Model Cost Trends | Daily/weekly cost by model |

---

## Full Build Order

```
Day 1
  └── Step 1: Session persistence
        main.py → chat_sessions collection + last_active_at middleware

Day 2
  ├── Step 2a: middleware/usage_tracker.py (core utility)
  └── Step 2b: Wire into chat_wrapper.py + recommendations.py

Day 3
  ├── Step 2c: Wire into report_agent.py + physical_health_agent.py + rag.py
  └── Step 3: utils/credit_manager.py + utils/credit_alerts.py

Day 4
  ├── Step 4: utils/audit.py + wire into users.py + super_admin.py
  └── Step 5A: admin_metrics.py Phase A endpoints
        /overview, /companies, /companies/{id}, /users/{uid}

Day 5
  ├── Step 5B: retention + session history endpoints
  └── Step 5C: usage + credits endpoints

Day 6
  ├── Step 5D: audit-log endpoint
  └── Step 6: GET /api/employer/team-usage

Day 7
  ├── Step 7: Gamification admin endpoints
  │     /admin/gamification/overview
  │     /admin/gamification/companies/{id}
  │     Challenge CRUD
  └── Step 7b: GET /api/employer/gamification

Day 8
  └── Step 8: firestore.indexes.json updates
        Register admin_metrics router in main.py
        End-to-end smoke test all endpoints
```

---

## Known Caveats (Do Not Forget)

1. **`with_structured_output()` has no `.usage_metadata`** — use `UsageMetadataCallbackHandler`
2. **Azure migration changes cost calc** — use env vars for token prices, not hardcoded values
3. **Azure AI Search filter syntax differs from Pinecone** — update `rag.py` retrieve() when migrating
4. **Cold start** — usage_logs starts empty on deploy; no backfill possible for historical data
5. **`/chat` has no auth** — decide before Day 1 whether to add optional auth or skip tracking it
6. **Employer team-usage requires `last_active_at`** — Step 1 must be fully deployed before Step 6 has real data
