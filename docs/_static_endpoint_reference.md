# Diltak / Uma — Static Endpoint Reference

Generated from source code via static analysis. Trust this document over `API_SCHEMA.md`.

**Auth model recap:** JWT (HS256), 15 min access + 30 day rotating refresh. `Authorization: Bearer <access_token>`.

**Roles:** `super_admin`, `employer`, `hr`, `manager`, `employee`.

**Mounting model (from `main.py`):**
- `report_router` mounted at root → effective path `/report/...`
- All other routers mounted with prefix `/api`
- `/chat`, `/sessions/{id}`, `/health`, `/rag/*`, `/rag/ingest-docx` are defined directly in `main.py` and have **no `/api` prefix**

**Auth labels used below:**
- `Public` — no auth
- `Optional auth` — `/chat` accepts anon; richer behaviour if Bearer present
- `Any authenticated` — `Depends(get_current_user)`
- `employer or hr` — `Depends(get_employer_user)` (role in `("employer","hr")`)
- `employer only` — `Depends(get_employer_user)` plus an internal `_require_owner` check that 403s if role != `employer`
- `super_admin` — `Depends(get_super_admin_user)`
- `employee` — internal label; the codebase only enforces "any authenticated user" for endpoints that conceptually target employees

---

## Table of Contents
1. [main.py (top-level)](#mainpy-top-level)
2. [report_api.py](#reportapi)
3. [routers/auth.py](#routersauthpy)
4. [routers/admin_metrics.py](#routersadmin_metricspy)
5. [routers/chat_wrapper.py](#routerschat_wrapperpy)
6. [routers/community_gamification.py](#routerscommunity_gamificationpy)
7. [routers/employee_import.py](#routersemployee_importpy)
8. [routers/employer.py](#routersemployerpy)
9. [routers/employer_dashboard.py](#routersemployer_dashboardpy)
10. [routers/employer_insights.py](#routersemployer_insightspy)
11. [routers/employer_org.py](#routersemployer_orgpy)
12. [routers/physical_health.py](#routersphysical_healthpy)
13. [routers/recommendations.py](#routersrecommendationspy)
14. [routers/reports_escalation.py](#routersreports_escalationpy)
15. [routers/super_admin.py](#routerssuper_adminpy)
16. [routers/users.py](#routersuserspy)
17. [routers/voice_calls.py](#routersvoice_callspy)

---

## main.py (top-level)

These endpoints are mounted with NO `/api` prefix.

### `POST /chat` — Send a chat message to Uma
**Auth:** Optional auth (anonymous allowed; identity used only to persist `chat_sessions` if a valid Bearer is presented)
**Source:** `d:\bai\humasql\main.py:455`

**Path params:** none
**Query params:** none
**Request body (`ChatRequest`):**
- `message` (string, required)
- `session_id` (string, optional)

**Response 200 (`ChatResponse`):**
```json
{
  "session_id": "string (uuid; new or echoed)",
  "reply": "string — Uma's reply text",
  "peek": {
    "language": "string",
    "emotion": "string (Happy|Sad|Angry|Anxious|Tired|Excited|Lonely|Neutral|Confused|Grateful)",
    "emotion_intensity": "float 0-1",
    "tone_shift": "string (escalating|calming|stable|flip)",
    "subtext": "string",
    "deep_need": "string (Validation|Distraction|Tough Love|Advice|...)",
    "conversation_phase": "string (opening|venting|seeking|closing|playful|deep_talk|crisis)"
  },
  "mesh": {
    "new_memories": ["string"],
    "recalled_memories": ["string"]
  },
  "strategy": "string",
  "expression_style": "string (warm|playful|raw|gentle|hype|chill|chaotic)",
  "retrieved_context": ["string"],
  "total_memories": "int"
}
```

**Errors:**
- 500 — `OPENAI_API_KEY not configured.`

**Side effects:** in-process `sessions` dict appended; if Bearer token is valid AND uid != "anonymous", an async fire-and-forget call to `persist_chat_session` writes a row into `chat_sessions`.

**Quirks:**
- Session store is **in-memory only** — sessions are lost on server restart.
- `peek.emotion_intensity` and similar use snake_case keys per Pydantic field names.
- Anonymous chat is allowed; auth failures fall through silently.

---

### `GET /sessions/{session_id}` — Look up an in-memory chat session
**Auth:** Public
**Source:** `d:\bai\humasql\main.py:550`

**Path params:** `session_id` (string)
**Response 200 (`SessionInfo`):**
```json
{
  "session_id": "string",
  "message_count": "int",
  "memories": ["string"],
  "memory_categories": ["string"]
}
```

**Errors:**
- 404 — `Session not found.`

**Side effects:** none

**Quirks:** No auth check whatsoever — anyone with a session_id can read it.

---

### `DELETE /sessions/{session_id}` — Delete an in-memory chat session
**Auth:** Public
**Source:** `d:\bai\humasql\main.py:563`

**Path params:** `session_id` (string)
**Response 200:** `{ "detail": "Session deleted." }`
**Errors:** 404 — `Session not found.`
**Quirks:** No auth check.

---

### `GET /health` — Health probe
**Auth:** Public
**Source:** `d:\bai\humasql\main.py:571`

**Response 200:**
```json
{
  "status": "ok",
  "api_key_set": "bool",
  "rag_chunks": "int"
}
```

---

### `POST /rag/documents` — Add documents to RAG store
**Auth:** Public
**Source:** `d:\bai\humasql\main.py:596`

**Request body (`AddDocumentsRequest`):**
- `texts` (List[string], required, must be non-empty)
- `metadata` (List[dict], optional)
- `auto_chunk` (bool, default true)

**Response 200 (`AddDocumentsResponse`):**
```json
{ "chunk_ids": ["string"], "message": "Added N chunk(s)." }
```

**Errors:**
- 400 — `texts cannot be empty`
- 500 — `OPENAI_API_KEY required.`

**Side effects:** writes vectors to RAG store (`get_rag_store()`).

---

### `GET /rag/documents` — List all chunks
**Auth:** Public
**Source:** `d:\bai\humasql\main.py:607`

**Response 200:**
```json
{ "chunks": [...], "total": "int" }
```
`chunks` shape is implementation-defined by `RAGStore.list_chunks()`.

---

### `DELETE /rag/documents/{chunk_id}` — Delete one chunk
**Auth:** Public
**Source:** `d:\bai\humasql\main.py:612`

**Path params:** `chunk_id` (string)
**Response 200:** `{ "detail": "Deleted." }`
**Errors:** 404 — `Chunk not found.`

---

### `POST /rag/ingest-docx` — Ingest .docx folder into RAG
**Auth:** Public
**Source:** `d:\bai\humasql\main.py:635`

**Request body (`IngestDocxRequest`):**
- `folder_path` (string, required)
- `pattern` (string, default `"*.docx"`)

**Response 200 (`IngestDocxResponse`):**
```json
{
  "files_processed": "int",
  "chunks_added": "int",
  "chunk_ids": ["string"],
  "errors": ["string"]
}
```
**Errors:** 500 — `OPENAI_API_KEY required.`

**Quirks:** server reads from a server-local filesystem path — implies a deployment-level filesystem.

---

## report_api.py

Router prefix: `/report` (NOT under `/api`). Tag: `Report Analysis`.

### `POST /report/analyze` — Analyse chat conversation
**Auth:** Public
**Source:** `d:\bai\humasql\report_api.py:25`

**Request body (`ReportRequest`):**
- `user_id` (string, required)
- `messages` (List[`ChatMessage`], required, must be non-empty)
  - `ChatMessage`: `{ role: str ('user'|'assistant'), content: str }`

**Response 200 (`ReportResponse`):**
```json
{
  "meta": {
    "report_id": "string (uuid)",
    "user_id": "string",
    "generated_at": "ISO datetime",
    "version": "1.0"
  },
  "mental_health": {
    "score": "float 0-10",
    "level": "low|medium|high",
    "confidence": "float 0-1",
    "trend": "improving|stable|declining",
    "summary": "string",
    "metrics": {
      "emotional_regulation": { "score": float, "level": str, "reason": str, "weight": float },
      "stress_anxiety": {...},
      "motivation_engagement": {...},
      "social_connectedness": {...},
      "self_esteem": {...},
      "cognitive_functioning": {...},
      "emotional_tone": {...},
      "assertiveness": {...},
      "work_life_balance": {...},
      "substance_use": {...}
    }
  },
  "physical_health": {
    "score": "float", "level": "low|medium|high",
    "confidence": "float", "trend": "improving|stable|declining",
    "summary": "string",
    "metrics": {
      "activity": {...}, "nutrition": {...}, "pain": {...},
      "lifestyle": {...}, "absenteeism": {...}
    }
  },
  "overall": {
    "score": "float", "level": "low|medium|high",
    "confidence": "float", "trend": "improving|stable|declining",
    "priority": "low|medium|high",
    "summary": "string", "full_report": "string",
    "key_insights": ["string"], "strengths": ["string"],
    "risks": ["string"], "recommendations": ["string"]
  }
}
```

**Errors:**
- 400 — `messages list cannot be empty.`
- 500 — `OPENAI_API_KEY not configured.`

---

## routers/auth.py

Router prefix: `/api/auth`. Tag: `auth`.

### `POST /api/auth/register` — Employer self-signup
**Auth:** Public
**Source:** `d:\bai\humasql\routers\auth.py:126`

**Request body (`RegisterRequest`):**
- `email` (EmailStr, required)
- `password` (string, required, min 8 chars)
- `company_name` (string, required, min 1 char)
- `full_name` (string, optional)

**Response 201 (`TokenPair`):**
```json
{ "access_token": "JWT", "refresh_token": "opaque-string", "token_type": "bearer" }
```

**Errors:**
- 409 — `A user with this email already exists.`
- 422 — Pydantic validation (e.g., password too short)

**Side effects:** creates `companies` row, `users` row (role=`employer`), `refresh_tokens` row.

---

### `POST /api/auth/login` — Login
**Auth:** Public
**Source:** `d:\bai\humasql\routers\auth.py:154`

**Request body (`LoginRequest`):** `email` (EmailStr), `password` (string)

**Response 200:** `TokenPair` (see register).

**Errors:**
- 401 — `Invalid email or password.` (also if password_hash null)
- 403 — `Your account has been deactivated.`

**Side effects:** writes new `refresh_tokens` row.

---

### `POST /api/auth/refresh` — Rotate refresh token
**Auth:** Public (presents refresh_token in body)
**Source:** `d:\bai\humasql\routers\auth.py:166`

**Request body:** `{ "refresh_token": "string" }`
**Response 200:** `TokenPair`

**Errors:**
- 401 — invalid/expired refresh token, or `User is no longer active.`

**Side effects:** revokes old refresh_token row, creates new one.

**Quirks:** server iterates all non-revoked tokens to find a hash match — not indexed lookup, but bounded.

---

### `POST /api/auth/logout` — Revoke refresh token
**Auth:** Public (presents refresh_token in body)
**Source:** `d:\bai\humasql\routers\auth.py:201`

**Request body:** `{ "refresh_token": "string" }`
**Response 204:** no body
**Side effects:** marks matching `refresh_tokens.revoked = true`. No-op (still 204) if token unknown.

---

### `GET /api/auth/me` — Caller claims
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\auth.py:216`

**Response 200 (`MeResponse`):**
```json
{
  "uid": "string|null",
  "email": "string|null",
  "role": "string|null",
  "company_id": "string|null"
}
```

---

### `GET /api/auth/profile` — Full profile + company
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\auth.py:221`

**Response 200 (raw dict):**
```json
{
  "id": "string", "email": "string", "role": "string",
  "company_id": "string|null", "manager_id": "string|null",
  "department": "string|null", "is_active": "bool",
  "profile": { ...user.profile JSONB... },
  "company": {
    "id": "string", "name": "string", "owner_id": "string|null",
    "settings": {...}, "employee_count": "int"
  } | null
}
```
**Errors:** 404 — `User not found.`

---

### `POST /api/auth/refresh-profile` — Re-fetch profile
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\auth.py:253`

Identical payload to `GET /api/auth/profile`.

---

## routers/admin_metrics.py

Router prefix: `/api/admin`. Tag: `Admin Metrics`. **All endpoints are super_admin only.**

### `GET /api/admin/overview` — Platform KPIs
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\admin_metrics.py:226`

**Response 200 (`PlatformOverview`):**
```json
{
  "totalCompanies": "int", "totalEmployers": "int", "totalEmployees": "int",
  "activeUsers": "int", "inactiveUsers": "int", "newUsersLast30d": "int",
  "totalCreditsConsumed": "float (MTD)", "totalLifetimeSpend": "float",
  "companiesAtWarning": "int", "companiesAtCritical": "int",
  "computedAt": "ISO datetime"
}
```

**Errors:** 500 — `users query failed: ...`

---

### `GET /api/admin/companies` — List companies + credit summary
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\admin_metrics.py:301`

**Query params:**
- `search` (string, optional)
- `alert_status` (string, optional)
- `plan_tier` (string, optional)
- `page` (int, default 1, ge 1)
- `limit` (int, default 20, ge 1, le 100)

**Response 200:**
```json
{
  "companies": [CompanySummary],
  "total": "int", "page": "int", "limit": "int",
  "totalPages": "int", "hasNext": "bool", "hasPrev": "bool"
}
```
`CompanySummary`: `id, name, industry?, planTier?, employeeCount, creditLimitUsd, creditsConsumedMtd, creditsRemaining, alertStatus, totalLifetimeSpend, ownerId?, createdAt?`

**Errors:** 500 on DB failure.

**Quirks:** filtering and pagination happen in Python after fetching all companies — not scalable.

---

### `GET /api/admin/companies/{company_id}` — Company detail
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\admin_metrics.py:378`

**Path params:** `company_id` (UUID string)

**Response 200 (`CompanyDetail`):** id, name, industry?, size?, website?, planTier?, employeeCount, creditLimitUsd, creditsConsumedMtd, creditsRemaining, warningThresholdPct, alertStatus, totalLifetimeSpend, lastResetAt?, ownerId?, createdAt?, tokensIn30d, tokensOut30d, costUsd30d, employees: List[{uid,email,firstName,lastName,role,department,isActive}].

**Errors:** 400 invalid UUID; 404 not found.

---

### `GET /api/admin/users/{uid}` — User detail
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\admin_metrics.py:463`

**Path params:** `uid` (string)

**Response 200 (`UserDetail`):** uid, email, firstName, lastName, role, companyId?, companyName?, department?, isActive, lastActiveAt?, createdAt?, totalTokensIn, totalTokensOut, totalCostUsd, totalCalls, featureBreakdown: `Dict[str, {calls, tokensIn, tokensOut, costUsd}]`.

**Errors:** 404 — `User not found.`

---

### `GET /api/admin/usage` — Usage logs (paginated)
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\admin_metrics.py:541`

**Query params:**
- `company_id` (string, optional UUID)
- `user_id` (string, optional)
- `feature` (string, optional)
- `days` (int, default 30, ge 1, le 365)
- `page` (int, default 1, ge 1)
- `limit` (int, default 50, ge 1, le 200)

**Response 200:**
```json
{
  "records": [UsageRecord],
  "total": "int", "page": "int", "limit": "int",
  "totalPages": "int", "hasNext": "bool", "hasPrev": "bool",
  "summary": {
    "totalTokensIn": "int", "totalTokensOut": "int",
    "totalCostUsd": "float", "totalCalls": "int"
  }
}
```
`UsageRecord`: id, userId, companyId, feature, model, provider, tokensIn, tokensOut, totalTokens, estimatedCostUsd, latencyMs, success, error?, timestamp?.

**Errors:** 500 on DB failure; 400 if company_id is malformed UUID.

---

### `GET /api/admin/credits` — Credit balances
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\admin_metrics.py:621`

**Query params:** `alert_status?`, `plan_tier?`, `page` (default 1), `limit` (default 50, max 200).

**Response 200:**
```json
{ "balances": [CreditBalance], "total":..., "page":..., "limit":..., "totalPages":..., "hasNext":..., "hasPrev":... }
```
`CreditBalance`: companyId, companyName, planTier, creditLimitUsd, creditsConsumedMtd, creditsRemaining, alertStatus, totalLifetimeSpend, lastResetAt?.

---

### `GET /api/admin/audit-log` — Audit trail
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\admin_metrics.py:679`

**Query params:** `company_id?`, `actor_uid?`, `action?`, `days` (default 30, 1–365), `page`, `limit` (default 50, max 200).

**Response 200:**
```json
{ "entries": [AuditEntry], "total":..., "page":..., "limit":..., "totalPages":..., "hasNext":..., "hasPrev":... }
```
`AuditEntry`: id, actorUid, actorRole, action, targetUid?, targetType, companyId, metadata: dict, timestamp?, success.

---

### `GET /api/admin/gamification/overview` — Platform gamification health
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\admin_metrics.py:783`

**Response 200 (raw dict):** totalActivePlayers, avgLevelPlatform, totalPointsAllTime, topBadge?, badgeCounts: `Dict[str,int]`, totalChallenges, activeChallenges, totalChallengeCompletions, pointsByEventType30d: `Dict[str,int]`, mostEngagedCompany?: `{companyId,totalPoints}`, computedAt.

---

### `GET /api/admin/gamification/companies/{company_id}` — Per-company gamification
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\admin_metrics.py:877`

**Path params:** `company_id` (UUID)
**Response 200 (raw dict):** companyId, totalPlayers, activePlayers7d, avgPoints, avgLevel, avgStreak, badgeDistribution: dict, totalBadgesEarned, pointsTrend7d: List[int] (length 7).

When no rows: returns same shape with zero/empty values (no pointsTrend7d field in that branch).

**Errors:** 400 invalid UUID, 500 query failure.

---

### `POST /api/admin/challenges` — Create challenge
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\admin_metrics.py:964`

**Request body (`CreateChallengeRequest`):**
- `title` (string, required)
- `description` (string, required)
- `type` (string, required: `"daily_checkin"|"conversation"|"physical_health"|"streak"|"custom"`)
- `target` (int, required)
- `pointsReward` (int, required)
- `companyId` (string, optional — null = platform-wide)
- `startsAt` (string ISO datetime, optional)
- `endsAt` (string ISO datetime, optional)

**Response 201:** `{ "success": true, "challengeId": "uuid", "message": "Challenge created." }`

**Errors:** 400 if companyId malformed; 500 on save failure.

**Side effects:** writes `wellness_challenges` row.

---

### `GET /api/admin/challenges` — List challenges
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\admin_metrics.py:1011`

**Query params:** `company_id?`, `is_active?` (bool), `page`, `limit` (default 20, max 100).

**Response 200:** paginated. Each entry: id, title, description, type, target, pointsReward, companyId?, isActive, startsAt?, endsAt?, createdBy?, createdAt?.

---

### `PATCH /api/admin/challenges/{challenge_id}` — Update challenge
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\admin_metrics.py:1071`

**Path params:** `challenge_id` (UUID)

**Request body (`UpdateChallengeRequest`):** all optional — `title?, description?, target?, pointsReward?, isActive?, startsAt?, endsAt?`

**Response 200:** `{ "success": true, "challengeId": "uuid", "updatedFields": ["..."] }`

**Errors:** 400 invalid UUID / no fields; 404 not found; 500 save fail.

---

### `GET /api/admin/challenges/{challenge_id}/stats` — Challenge participation
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\admin_metrics.py:1136`

**Path params:** `challenge_id` (UUID)
**Response 200 (raw dict):** challengeId, title, type, target, pointsReward, isActive, participants, completions, completionRatePct, companyBreakdown: `Dict[str,{participants,completions}]`.

**Errors:** 400 invalid UUID; 404 not found.

---

## routers/chat_wrapper.py

Router prefix: `/api/chat_wrapper`. Tag: `Chat Wrapper`. **No router-level auth dependency** — endpoints below have no explicit auth check at all.

### `POST /api/chat_wrapper` — Multi-modal chat handler
**Auth:** Public (no Depends)
**Source:** `d:\bai\humasql\routers\chat_wrapper.py:214`

**Request body:** `application/json` OR `multipart/form-data` with field `data` (JSON string) and `files` (one or more files).

JSON shape (when not assessment / not endSession):
```json
{
  "messages": [ {"sender":"user|ai","content":"string"} ],
  "umaSessionId": "string|null",
  "assessmentType": "get_questions|null",
  "endSession": "bool",
  "sessionType": "text",
  "sessionDuration": "int (minutes)",
  "userId": "string",
  "companyId": "string"
}
```

**Response 200 (discriminated by `type`):**
- When `assessmentType == "get_questions"` and a recognised test name appears in last user message:
  `{ "type": "assessment_questions", "data": { "content":"string","sender":"ai","testName":"personality_profiler|self_efficacy_scale" } }`
- When `endSession: true`: returns `{ "type":"report", "data": <full report JSON merged with employee_id, company_id, session_type, session_duration_minutes> }` (or `{type:"report", data:{error:"..."}}` on failure).
- Otherwise: `{ "type": "message", "data": { content, sender:"ai", umaSessionId, emotion, avatarEmotion (HAPPY|SAD|ANGRY|THINKING|IDLE), emotionIntensity, expressionStyle, conversationPhase } }`

**Errors:**
- 400 — Invalid/missing JSON body, or `Messages array is required`
- 500 — Failed to reach Uma AI agent

**Side effects:** When `endSession`, writes a `mental_health_reports` row.

**Quirks:**
- Mixes camelCase (`umaSessionId`,`endSession`) and snake_case fields.
- `userId`/`companyId` are read from request body — **trusted**, not auth-derived.
- File uploads only stub the filename into the prompt — file contents are not actually parsed.
- Recognised test names are extracted via substring search on the last user message: keywords `personality`/`profiler` → `personality_profiler`; `efficacy` → `self_efficacy_scale`.

---

### `POST /api/chat_wrapper/ai-chat` — Simple LLM chat
**Auth:** Public (no Depends)
**Source:** `d:\bai\humasql\routers\chat_wrapper.py:281`

**Request body (`AiChatReq`):**
- `message` (string, required, must be truthy)
- `user_id` (string, optional)
- `session_id` (string, optional)
- `user_role` (string, optional)
- `context` (string, optional — special value `"personal_wellness"` swaps system prompt)

**Response 200 (`AiChatResponse`):**
```json
{ "response": "string", "session_id": "string|null", "user_id": "string|null" }
```

**Errors:** 400 — `Message is required`; 500 — OpenAI / config errors.

**Side effects:** logs `usage_logs` row via `track_usage` (feature="chat", model="gpt-4").

---

### `POST /api/chat_wrapper/analyze` — Standalone wellness analysis
**Auth:** Public (no Depends)
**Source:** `d:\bai\humasql\routers\chat_wrapper.py:325`

Same `ReportRequest` / `ReportResponse` contract as `/report/analyze`. Errors: 400 (empty messages), 500 (no OPENAI_API_KEY).

---

## routers/community_gamification.py

Router prefix: **none** (endpoints are `/api/community` and `/api/gamification`). Tag: `Community & Gamification`. **Router-level auth: `Depends(get_current_user)`** — all endpoints require any authenticated user.

### `POST /api/community` — Action-dispatched community endpoint
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\community_gamification.py:160`

**Request body (`CommunityRequest`):**
- `action` (string, required: `get_posts`|`get_anonymous_profile`|`create_post`|`get_replies`|`create_reply`|`like_post`)
- `employee_id` (string, optional — required for some actions)
- `company_id` (string, required — UUID)
- `data` (any, optional — action-specific payload)

**Response 200:** discriminated union by `action` field. Examples:
- `get_posts`: `{ "action":"get_posts", "success":true, "posts":[{...post...}] }` — supports `data.category` filter and `data.limit_count` (default 20).
- `get_anonymous_profile`: `{ "action":"get_anonymous_profile", "success":true, "profile":{...} }`.
- `create_post`: `{ "action":"create_post", "success":true, "post_id":"uuid", "post":{...} }`. `data` may have `content, title, category, tags`.
- `get_replies`: `{ "action":"get_replies", "success":true, "replies":[...] }`. `data.post_id` required.
- `create_reply`: `{ "action":"create_reply", "success":true, "reply_id":"uuid", "reply":{...} }`. `data.post_id`, `data.content` expected.
- `like_post`: `{ "action":"like_post", "success":true, "message":"Post liked successfully" }`.

**Errors:**
- 400 — `Invalid action`, `employee_id required`, `post_id required`, or invalid UUID strings.

**Side effects:** writes/updates `community_posts`, `community_replies`, `anonymous_profiles`. Like-post increments `likes` column. Reply creation increments parent post's `replies` counter.

---

### `POST /api/gamification` — Action-dispatched gamification endpoint
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\community_gamification.py:380`

**Request body (`GamificationRequest`):**
- `action` (string, required: `get_user_stats`|`check_in`|`conversation_complete`|`get_available_challenges`|`join_challenge`)
- `employee_id` (string, required)
- `company_id` (string, required — UUID for `get_available_challenges`)
- `data` (any, optional)

**Responses (discriminated union by action):**
- `get_user_stats`: `{ "action":"get_user_stats", "success":true, "user_stats":{ id,user_id,company_id,points,total_points,level,badges,streak,current_streak,updated_at } }`
- `check_in`: `{ "action":"check_in", "success":bool, "user_stats"?, "new_badges"?:[...], "points_earned"?:int, "message":string }` — fails with `success:false` if checked in within 24h.
- `conversation_complete`: same shape; awards 15 points, or 50 if `data.type=="challenge_complete"`.
- `get_available_challenges`: `{ "action":"get_available_challenges","success":true,"challenges":[...] }`
- `join_challenge`: `{ "action":"join_challenge","success":true,"message":"Challenge joined successfully" }` — **stub, does not persist anything**.

**Errors:** 400 — `Invalid action`, `Invalid company_id`.

**Side effects:** Writes/updates `user_gamification` rows. `check_in` and `conversation_complete` mutate points/level/streak/badges.

**Quirks:**
- `employee_id` and `company_id` are taken from the body and **not validated against the JWT claims** — a caller can submit any IDs.

---

## routers/employee_import.py

Router prefix: `/api/employees`. Tag: `Employee Import`.

### `GET /api/employees/import/template` — Download CSV template
**Auth:** employer or hr
**Source:** `d:\bai\humasql\routers\employee_import.py:155`

**Response 200:** `text/csv` (StreamingResponse, attachment filename `employee_import_template.csv`). Headers: `email, first_name, last_name, role, department, position, phone, manager_email, hierarchy_level`. 3 example rows.

---

### `POST /api/employees/import` — Upload + start import
**Auth:** employer or hr
**Source:** `d:\bai\humasql\routers\employee_import.py:176`

**Form fields (`multipart/form-data`):**
- `file` (file, required, .csv or .xlsx)
- `dry_run` (bool, default false)

**Response 200 (validation failures, `ImportValidationResponse`):** when errors are detected:
```json
{
  "valid": false, "total_rows": int, "valid_rows": int, "error_count": int,
  "errors": [{ "row_number": int, "column": str, "value": str, "message": str }],
  "duplicate_emails": ["..."], "preview": null,
  "message": "Found N error(s). Fix them and re-upload."
}
```

**Response 200 (dry_run success, `ImportValidationResponse`):**
```json
{ "valid": true, "total_rows":..., "valid_rows":..., "error_count":0, "errors":[], "duplicate_emails":[],
  "preview":[{"row":int,"email":str,"name":str,"role":str,"department":str}], "message":"..." }
```

**Response 200 (started, `ImportStartResponse`):**
```json
{ "job_id": "uuid", "status": "pending", "total_rows": int, "message": "...", "poll_url": "/api/employees/import/{job_id}" }
```

**Side effects:** parses file, optionally creates `import_jobs` row + kicks background task. Background worker creates `users` rows, sends welcome emails (Resend), uploads results CSV to Azure Blob (signed URL valid 7 days), updates `companies.employee_count`.

**Quirks:** Default password for all bulk-imported users is hard-coded `"11111111"`.

---

### `GET /api/employees/import/{job_id}` — Poll job status
**Auth:** employer or hr
**Source:** `d:\bai\humasql\routers\employee_import.py:506`

**Path params:** `job_id` (string)
**Response 200 (`ImportStatusResponse`):** job_id, status, total_rows, processed, created_count, failed_count, skipped_count, progress_pct, results_csv_url?, created_at?, updated_at?.

**Errors:** 404 not found; 403 if job's company_id doesn't match caller's company.

---

### `POST /api/employees/import/{job_id}/resend-invites` — Resend failed invites
**Auth:** employer or hr
**Source:** `d:\bai\humasql\routers\employee_import.py:553`

**Path params:** `job_id` (string)

**Request body (`ResendInvitesRequest`):**
- `emails` (List[string], optional — null = resend all that failed)

**Response 200 (`ResendInvitesResponse`):**
```json
{ "resent": int, "failed": int, "details": [{ "email": "...", "success": bool, "error?": "..." }] }
```

**Errors:** 404 not found; 403 wrong company; 400 if job still running.

**Side effects:** sends welcome emails via Resend.

---

## routers/employer.py

Router prefix: `/api/employer`. Tag: `Employer CRUD`.

### `GET /api/employer/profile` — Own employer profile + company
**Auth:** employer or hr
**Source:** `d:\bai\humasql\routers\employer.py:190`

**Response 200 (`EmployerProfileResponse`):** uid, email, firstName, lastName, displayName, role, jobTitle?, phone?, companyId, companyName, isActive, hierarchyLevel, permissions: `Dict[str,bool]` (keys: `can_view_team_reports, can_manage_employees, can_approve_leaves, can_view_analytics, can_create_programs, skip_level_access`), registeredAt?, updatedAt?, company?: full company dict.

---

### `PATCH /api/employer/profile` — Update own profile
**Auth:** employer only (HR blocked by `_require_owner`)
**Source:** `d:\bai\humasql\routers\employer.py:256`

**Request body (`UpdateEmployerProfileRequest`):** all optional — `firstName?, lastName?, phone?, jobTitle?`

**Response 200 (`MutationResponse`):** `{ success: true, message: "...", updatedFields: [...] }`

**Errors:** 400 — no fields / not owner; 403 — not owner; 404 — User not found.

---

### `GET /api/employer/company` — Company doc
**Auth:** employer or hr
**Source:** `d:\bai\humasql\routers\employer.py:313`

**Response 200 (`CompanyResponse`):** id, name, industry?, size?, ownerId, employeeCount, website?, address?, phone?, description?, logoUrl?, createdAt?, updatedAt?.

**Errors:** 400 invalid UUID / no company; 404 not found.

---

### `PATCH /api/employer/company` — Update company
**Auth:** employer only (HR blocked)
**Source:** `d:\bai\humasql\routers\employer.py:342`

**Request body (`UpdateCompanyRequest`):** all optional — `name?, industry?, size?, website?, address?, phone?, description?, logoUrl?`

**Response 200 (`MutationResponse`).**

**Errors:** 400 invalid input / no fields; 403 not owner; 404 not found.

---

### `GET /api/employer/company/stats` — Company stats
**Auth:** employer or hr
**Source:** `d:\bai\humasql\routers\employer.py:410`

**Response 200 (`CompanyStatsResponse`):** companyId, totalEmployees, activeEmployees, inactiveEmployees, roleBreakdown, departmentBreakdown, recentJoins, computedAt.

**Errors:** 400 invalid UUID; 500 DB.

---

### `POST /api/employer/change-password` — Change password
**Auth:** employer only (HR blocked)
**Source:** `d:\bai\humasql\routers\employer.py:486`

**Request body (`ChangePasswordRequest`):** `current_password` (string), `new_password` (string, min 8 chars).

**Response 200 (`MutationResponse`).**

**Errors:** 400 (length / same); 401 wrong current; 404 user.

---

### `DELETE /api/employer/account` — Delete employer + company
**Auth:** employer only (HR blocked)
**Source:** `d:\bai\humasql\routers\employer.py:528`

**Request body (`DeleteAccountRequest`):**
- `confirmation_phrase` (string, must == `"DELETE MY ACCOUNT"`)
- `password` (string)

**Response 200 (`MutationResponse`).**

**Errors:** 400 wrong phrase; 401 wrong password; 404 user.

**Side effects:** deletes Company row; deletes User row (employees' company_id set NULL via FK cascade).

---

### `GET /api/employer/team-usage` — Per-employee engagement summary
**Auth:** employer or hr
**Source:** `d:\bai\humasql\routers\employer.py:582`

**Query params:**
- `days` (int, default 30, ge 1, le 365)
- `department` (string, optional)
- `status` (string, optional: `active|dormant|churned`)
- `sort_by` (string, default `engagementScore`: `engagementScore|lastActive|sessions|checkIns|streak`)
- `page` (int, default 1, ge 1)
- `limit` (int, default 20, ge 1, le 100)

**Response 200 (raw dict):** companyId, windowDays, employees[{uid, firstName, lastName, department, position, role, activityStatus, lastActiveDaysAgo, sessionsLast30d, checkInsLast30d, physicalCheckInsLast30d, featuresUsed, gamificationLevel, currentStreak, engagementScore}], total, page, limit, totalPages, hasNext, hasPrev, summary{totalEmployees, activeCount, dormantCount, churnedCount, avgEngagementScore, participationRatePct}.

**Errors:** 400 invalid UUID; 500 DB.

---

### `GET /api/employer/gamification` — Anonymous leaderboard + challenges
**Auth:** employer or hr
**Source:** `d:\bai\humasql\routers\employer.py:862`

**Response 200 (raw dict):** companyId, totalPlayers, activePlayers7d, avgPoints, avgLevel, avgStreak, badgeDistribution, leaderboard[{rank, displayName, level, totalPoints, currentStreak, badges}] (top 20), activeChallenges[{id, title, description, type, target, pointsReward, endsAt?}].

When no players: returns same shape with zero/empty values, `leaderboard:[]`, `activeChallenges:[]`.

**Errors:** 400 invalid UUID; 500.

---

## routers/employer_dashboard.py

Router prefix: `/api/employer`. Tag: `Employer — Team Dashboard`. **Router-level auth: `Depends(get_current_user)`**, but each endpoint additionally calls `_require_employer` which 403s unless role is `employer|manager|hr`.

All endpoints require `company_id` query param to match caller's `company_id`. K-anonymity: cohorts < 1 are suppressed (effectively non-blocking).

### `GET /api/employer/wellness-index` — Composite wellness score
**Auth:** employer or hr (or manager) — caller must own the company_id
**Source:** `d:\bai\humasql\routers\employer_dashboard.py:386`

**Query params:** `company_id` (string, required), `period_days` (int, default 30, ge 7, le 90).

**Response 200 (`WellnessIndexResponse`):** company_id, team_size_band (`<5 (suppressed)`, `5–10`, `10–25`, `25–50`, `50–100`, `100+`), wellness_index (0–100), stress_score, engagement_score, check_in_participation_pct, period_days, trend_vs_prior_period?, data_quality (`high|medium|low|insufficient`), computed_at.

**Errors:**
- 403 — not employer/manager/hr; or company mismatch
- 422 — `{ "error":"insufficient_cohort","message":"...","suppressed":true }` (used as detail)

---

### `GET /api/employer/burnout-trend` — Weekly burnout distribution
**Auth:** employer or hr (or manager)
**Source:** `d:\bai\humasql\routers\employer_dashboard.py:475`

**Query params:** `company_id`, `weeks` (int, default 8, ge 1, le 12).

**Response 200 (`BurnoutTrendResponse`):** company_id, period_weeks, buckets[{label:low|medium|high, percentage, trend:rising|falling|stable}], weekly_distribution[{week, low_pct, medium_pct, high_pct, sample_quality}], alert_level (`green|amber|red`), computed_at.

---

### `GET /api/employer/engagement-signals` — DAU/WAU + check-in completion
**Auth:** employer or hr (or manager)
**Source:** `d:\bai\humasql\routers\employer_dashboard.py:567`

**Query params:** `company_id`, `period_days` (default 30, 7–90).

**Response 200 (`EngagementSignalsResponse`):** company_id, dau_pct, wau_pct, check_in_completion_pct, avg_session_depth_score (0–10), period_days, computed_at.

---

### `GET /api/employer/workload-friction` — Late-night + sentiment shifts
**Auth:** employer or hr (or manager)
**Source:** `d:\bai\humasql\routers\employer_dashboard.py:617`

**Query params:** `company_id`, `period_days` (default 30, 7–90).

**Response 200 (`WorkloadFrictionResponse`):** company_id, late_night_activity_pct, sentiment_shift_events (bucketed to nearest 5), overload_pattern_score (0–10), risk_level, period_days, computed_at.

---

### `GET /api/employer/productivity-proxy` — Engagement trend
**Auth:** employer or hr (or manager)
**Source:** `d:\bai\humasql\routers\employer_dashboard.py:667`

**Query params:** `company_id`, `weeks` (default 8, 1–12).

**Response 200 (`ProductivityProxyResponse`):** company_id, engagement_trend: List[float], period_label: List[str], correlation_note, data_quality, computed_at.

---

### `GET /api/employer/early-warnings` — Alerts on stress/mood/engagement
**Auth:** employer or hr (or manager)
**Source:** `d:\bai\humasql\routers\employer_dashboard.py:719`

**Query params:** `company_id`, `period_days` (default 14, 7–30).

**Response 200 (`EarlyWarningsResponse`):** company_id, alerts[{signal, description, confidence, period, attribution:"none"}], overall_risk (`green|amber|red`), computed_at.

---

### `GET /api/employer/suggested-actions` — Playbooks based on signals
**Auth:** employer or hr (or manager)
**Source:** `d:\bai\humasql\routers\employer_dashboard.py:864`

**Query params:** `company_id`.

**Response 200 (`SuggestedActionsResponse`):** company_id, actions[{trigger, category, action, expected_impact, playbook_steps:[...], priority}], generated_at.

---

## routers/employer_insights.py

Two routers in this file:
- `router` with prefix `/api/employer/insights`
- `actions_router` with prefix `/api/employer/actions`

Both have router-level dependency `Depends(get_current_user)`. Each endpoint additionally calls `_require_employer` (403 unless role in `employer|manager|hr`).

### `GET /api/employer/insights/predictive-trends`
**Auth:** employer or hr (or manager)
**Source:** `d:\bai\humasql\routers\employer_insights.py:586`

**Query params:** `company_id`, `forecast_weeks` (int, default 4, ge 1, le 8).

**Response 200 (`PredictiveTrendsResponse`):** company_id, forecast_weeks, historical[{week, burnout_risk_pct, attrition_risk_pct, confidence}], forecast[same shape], model_note, computed_at.

**Errors:** 400 invalid UUID; 403 mismatch; 422 insufficient cohort.

---

### `GET /api/employer/insights/benchmarks`
**Auth:** employer or hr (or manager)
**Source:** `d:\bai\humasql\routers\employer_insights.py:685`

**Query params:** `company_id`, `period_days` (default 30, 7–90), `industry?` (`tech|finance|healthcare|retail|education`).

**Response 200 (`BenchmarksResponse`):** company_id, industry, comparisons[{metric, your_value, benchmark_value, delta, direction:`above|below|at_par`, benchmark_source}], summary, period_days, computed_at.

---

### `GET /api/employer/insights/cohorts`
**Auth:** employer or hr (or manager)
**Source:** `d:\bai\humasql\routers\employer_insights.py:777`

**Query params:** `company_id`, `period_days` (default 30, 7–90).

**Response 200 (`CohortsResponse`):** company_id, cohorts[{label:`0–6 months|6–12 months|1–3 years|3+ years`, size_band, wellness_index, burnout_risk:`low|medium|high`, engagement_pct, suppressed?}], period_days, privacy_note, computed_at.

---

### `POST /api/employer/actions/manager-playbook`
**Auth:** employer or hr (or manager)
**Source:** `d:\bai\humasql\routers\employer_insights.py:891`

**Request body (`ManagerPlaybookRequest`):**
- `company_id` (string, required)
- `signal` (string, required: `stress_rising|engagement_drop|mood_declining|late_night_spikes|burnout_high`)

**Response 200 (`ManagerPlaybookResponse`):** company_id, signal, insight, recommendation, expected_impact, confidence, steps[{step, owner, timeline:`immediate|this_week|this_month`, expected_outcome}], guardrails[string], generated_at.

**Errors:** 400 unknown signal; 403 mismatch.

---

### `POST /api/employer/actions/hr-playbook`
**Auth:** employer or hr (or manager)
**Source:** `d:\bai\humasql\routers\employer_insights.py:926`

**Request body (`HRPlaybookRequest`):**
- `company_id` (string, required)
- `signals` (List[string], required)
- `department_label` (string, optional)

**Response 200 (`HRPlaybookResponse`):** company_id, active_signals, programs[{program_name, target_signal, delivery:`async|live|digital`, duration_weeks, expected_lift, priority:`immediate|next_cycle|optional`}], policy_adjustments[string], manager_enablement[string], format_note, generated_at.

**Errors:** 403 mismatch.

---

## routers/employer_org.py

Router prefix: `/api/employer/org`. Router-level: `Depends(get_current_user)`. Each endpoint calls `_require_employer_sa` (403 unless role in `employer|manager|hr`).

### `GET /api/employer/org/wellness-trend`
**Auth:** employer or hr (or manager)
**Source:** `d:\bai\humasql\routers\employer_org.py:348`

**Query params:** `company_id`, `weeks` (default 12, 4–26).

**Response 200 (`OrgWellnessTrendResponse`):** company_id, trend[{week, wellness_index, sample_size_band}], period_weeks, overall_index, direction (`improving|declining|stable`), computed_at.

---

### `GET /api/employer/org/department-comparison`
**Auth:** employer or hr (or manager)
**Source:** `d:\bai\humasql\routers\employer_org.py:419`

**Query params:** `company_id`, `period_days` (default 30, 7–90), `mask_labels` (bool, default true).

**Response 200 (`DeptComparisonResponse`):** company_id, departments[{label, wellness_index, burnout_risk, engagement_pct, size_band, suppressed?}], hotspot_label?, label_masking, period_days, computed_at.

---

### `GET /api/employer/org/retention-risk`
**Auth:** employer or hr (or manager)
**Source:** `d:\bai\humasql\routers\employer_org.py:511`

**Query params:** `company_id`, `period_days` (default 60, 14–180).

**Response 200 (`RetentionRiskResponse`):** company_id, risk_bands[{band:low|medium|high, percentage, trend:rising|falling|stable}], overall_risk (`green|amber|red`), period_days, note, computed_at.

---

### `GET /api/employer/org/diltak-engagement`
**Auth:** employer or hr (or manager)
**Source:** `d:\bai\humasql\routers\employer_org.py:609`

**Query params:** `company_id`, `period_days` (default 30, 7–90).

**Response 200 (`DiltakEngagementResponse`):** company_id, adoption_pct, wau_pct, voice_sessions_pct, text_sessions_pct, completion_rate_pct, avg_sessions_per_active_user, period_days, computed_at.

---

### `GET /api/employer/org/roi-impact`
**Auth:** employer or hr (or manager)
**Source:** `d:\bai\humasql\routers\employer_org.py:671`

**Query params:** `company_id`, `weeks` (default 8, 4–24).

**Response 200 (`ROIImpactResponse`):** company_id, correlations[{period, wellbeing_index, proxy_metric, proxy_value, correlation_direction:`positive|negative|neutral`}], summary, data_quality, computed_at.

---

### `POST /api/employer/org/program-effectiveness/log`
**Auth:** employer or hr (or manager)
**Source:** `d:\bai\humasql\routers\employer_org.py:755`

**Request body (`LogInterventionRequest`):**
- `company_id` (string, required)
- `label` (string, 1–120 chars, required)
- `start_date` (datetime ISO, required)
- `end_date` (datetime ISO, required, must be > start_date)

**Response 200 (`LogInterventionResponse`):** id, company_id, label, start_date, end_date, created_at.

**Errors:** 403 mismatch; 422 end <= start; 500 save fail.

**Side effects:** writes `interventions` row + `audit_logs` row (action=`intervention.log`).

---

### `GET /api/employer/org/program-effectiveness`
**Auth:** employer or hr (or manager)
**Source:** `d:\bai\humasql\routers\employer_org.py:823`

**Query params:** `company_id`.

**Response 200 (`ProgramEffectivenessResponse`):** company_id, cohorts[{label, before_index, after_index, delta, size_band, suppressed?}], overall_lift?, recommendation, computed_at.

---

## routers/physical_health.py

Router prefix: `/api/physical-health`. Tag: `Physical Health`. Each endpoint depends on `Depends(get_current_user)` (any authenticated). Documents are user-scoped — never company-filtered.

### `POST /api/physical-health/check-in` — Submit daily check-in
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\physical_health.py:100`

**Request body (`PhysicalCheckInRequest`):**
- `energy_level` (int 1–10, required)
- `sleep_quality` (int 1–10, required)
- `sleep_hours` (float 0–24, required)
- `exercise_done` (bool, required)
- `exercise_minutes` (int, default 0, ge 0)
- `exercise_type` (string, default `"none"`)
- `nutrition_quality` (int 1–10, required)
- `pain_level` (int 1–10, required) — note: 1 = severe, 10 = no pain
- `hydration` (int 1–10, required)
- `notes` (string, optional)

**Response 201 (`PhysicalCheckInResponse`):** `{ success, checkin_id, nudge? }`

**Errors:** 404 user profile; 500 save fail.

**Side effects:** writes `physical_health_checkins` row.

---

### `GET /api/physical-health/check-ins` — History
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\physical_health.py:148`

**Query params:** `page` (default 1, ge 1), `limit` (default 20, ge 1, le 100), `days` (default 90, ge 1, le 365).

**Response 200 (`CheckInHistoryResponse`):** success, checkins[`CheckInHistoryItem`], total, page, limit, totalPages, hasNext, hasPrev. `CheckInHistoryItem` has every check-in field plus `checkin_id`, `created_at`, `notes?`.

---

### `GET /api/physical-health/score` — Composite score (last 30d)
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\physical_health.py:215`

**Response 200 (`PhysicalHealthScoreResponse`):** score (float), level (`low|medium|high`), last_checkin_date?, days_since_checkin?, streak_days, highlights[string] (max 3), concerns[string] (max 3).

---

### `GET /api/physical-health/trends` — Time-series
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\physical_health.py:295`

**Query params:** `period` (string, default `30d`: `7d|14d|30d|90d`).

**Response 200 (`HealthTrendsResponse`):** period, data_points[`TrendPoint`], averages: dict (energy_level, sleep_quality, sleep_hours, nutrition_quality, pain_level, hydration, exercise_days_per_week), trend_direction: `Dict[str,str]`, total_checkins.

---

### `POST /api/physical-health/medical/upload` — Upload medical doc
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\physical_health.py:378`

**Form field:** `file` (file, required, .pdf/.docx/.doc, max 10 MB).
**Query params:** `report_type` (string, default `other`: `lab_work|blood_test|xray_mri|prescription|general_checkup|specialist|other`), `report_date?` (YYYY-MM-DD), `issuing_facility?`. `report_date` and `issuing_facility` are accepted but **discarded** (Phase 5 TODO).

**Response 202 (`MedicalDocumentUploadResponse`):** `{ success, doc_id, status:"processing", message }`.

**Errors:** 400 unsupported type / empty file / too large; 422 text extract fail; 404 user; 500 storage / save errors.

**Side effects:** writes `medical_documents` row, uploads bytes to Azure Blob (`MEDICAL_DOCUMENTS_CONTAINER`), schedules async LLM analysis.

---

### `GET /api/physical-health/medical` — List medical docs
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\physical_health.py:488`

**Query params:** `page` (default 1, ge 1), `limit` (default 20, ge 1, le 50).

**Response 200 (`MedicalDocumentListResponse`):** success, documents[`MedicalDocumentDetail`], total. Note: only returns total — no `page/limit/totalPages/hasNext/hasPrev` fields.

---

### `GET /api/physical-health/medical/{doc_id}` — Doc detail
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\physical_health.py:522`

**Path params:** `doc_id` (UUID).
**Response 200 (`MedicalDocumentDetail`):** doc_id, filename, report_type, report_date?, issuing_facility?, status, uploaded_at, analyzed_at?, summary?, key_findings?, flagged_values? (List[`FlaggedValue`]), recommendations?, follow_up_needed?, urgency_level (`routine|follow_up|urgent|emergency`).

**Errors:** 400 invalid UUID; 404 not found; 403 not owner.

**Quirks:** Analysis fields (summary, key_findings, flagged_values, recommendations, follow_up_needed) are not persisted to DB in current schema — always returned as null.

---

### `GET /api/physical-health/medical/{doc_id}/status` — Poll status
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\physical_health.py:548`

**Response 200 (`MedicalDocumentStatusResponse`):** `{ doc_id, status:"uploaded", analyzed_at:null, urgency_level:"routine" }` — **always returns these fixed values** (status not persisted).

**Errors:** 400/404/403.

---

### `DELETE /api/physical-health/medical/{doc_id}` — Delete medical doc
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\physical_health.py:581`

**Response 200 (raw dict):** `{ "success": true, "doc_id": "...", "message": "Document deleted." (+ warnings) }`

**Errors:** 400 invalid UUID; 404 not found; 403 not owner.

**Side effects:** deletes Azure Blob (if URL is https), deletes DB row. RAG chunk cleanup is TODO.

---

### `POST /api/physical-health/reports/generate` — On-demand report
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\physical_health.py:643`

**Request body (`PeriodicReportRequest`):**
- `report_type` (string, default `"on_demand"`: `weekly|monthly|on_demand`)
- `days` (int, default 30, ge 7, le 365)

**Response 201 (`PeriodicReportResponse`):** report_id, period_start, period_end, report_type, overall_score, overall_level, trend, avg_energy, avg_sleep_quality, avg_sleep_hours, avg_exercise_minutes_daily, avg_nutrition_quality, avg_pain_level, exercise_days, summary, strengths[], concerns[], recommendations[], follow_up_suggested, generated_at.

**Errors:** 422 — fewer than 3 check-ins in period; 500 — generation fail.

---

### `GET /api/physical-health/reports` — List reports
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\physical_health.py:717`

**Query params:** `page` (default 1, ge 1), `limit` (default 10, ge 1, le 50).

**Response 200 (raw dict):** success, reports[{report_id, report_type, overall_score, overall_level, trend, period_start, period_end, generated_at, follow_up_suggested}], total, page, limit, totalPages, hasNext, hasPrev.

---

### `GET /api/physical-health/reports/{report_id}` — Single report
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\physical_health.py:769`

**Path params:** `report_id` (UUID).
**Response 200 (`PeriodicReportResponse`).**
**Errors:** 400 invalid UUID; 404 not found; 403 not owner.

---

### `POST /api/physical-health/ask` — RAG Q&A on own medical history
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\physical_health.py:821`

**Request body (`AskRequest`):** `question` (string, min 5 chars).

**Response 200 (`AskResponse`):** answer, source_doc_ids[string], confidence (0–1), disclaimer (always present).

**Errors:** 500 — retrieval / answer generation failures.

---

## routers/recommendations.py

Router prefix: `/api/recommendations`. Tag: `Recommendations`. **Router-level: `Depends(get_current_user)`** — any authenticated user.

### `POST /api/recommendations/generate` — AI wellness recommendations
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\recommendations.py:222`

**Request body (`RecommendationRequest`):**
- `employee_id` (string, required)
- `company_id` (string, required)
- `current_mood` (int 1–10, required)
- `current_stress` (int 1–10, required)
- `current_energy` (int 1–10, required)
- `time_available` (int, required) — minutes

**Response 200 (`RecommendationResponse`):** success, recommendations: List[`AIRecommendation`], generated_at, context: `RecommendationContext`. Each `AIRecommendation`: id, recommendation_type (`meditation|journaling|breathing|exercise|sleep|nutrition|social|work_life_balance`), title, description, instructions: List[str], duration_minutes, difficulty_level (`beginner|intermediate|advanced`), mood_targets: List[str], wellness_metrics_affected: List[str], ai_generated, personalized_for_user, created_at.

**Errors:** 400 — values out of 1–10. (LLM/JSON parse failure silently falls back to canned recommendations.)

**Side effects:** logs `usage_logs` row (feature=`recommendation`, model=`gpt-4`); writes `ai_recommendations` row.

**Quirks:** `employee_id`/`company_id` come from request body, not auth. No cross-validation against the JWT.

---

## routers/reports_escalation.py

Router prefix: **none** (endpoints are at `/api/reports/...`, `/api/escalation/...`, `/api/employer/...`, `/api/export/...`). Tag: `Reports & Escalation`. **Router-level: `Depends(get_current_user)`**.

### `GET /api/reports/recent` — Recent reports + analytics
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\reports_escalation.py:154`

**Query params:**
- `companyId` (string, **required** UUID — note camelCase)
- `userId` (string, optional)
- `days` (int, default 7)

**Response 200 (`ReportsRecentResponse`):**
```json
{
  "success": true,
  "data": {
    "companyReports": {
      "count": int,
      "analytics": {
        "totalReports": int, "avgWellness": float, "avgStress": float, "avgMood": float,
        "avgEnergy": float, "highRiskCount": int, "mediumRiskCount": int, "lowRiskCount": int,
        "departmentBreakdown": { "<dept>": { "count": int, "avgWellness": float } },
        "dailyTrends": []
      },
      "aiContext": "string"
    },
    "personalHistory": null | { "history": {...}, "aiContext": "..." }
  }
}
```

**Errors:** 400 — Company ID required / Invalid UUID.

**Quirks:** `companyId` is camelCase; the body of the recent reports response is wrapped in `{success, data}` — different from most other endpoints. Reports' `employee.last_name` is replaced with `#XXXX` (first 4 chars of the user's id) — never the real surname.

---

### `POST /api/escalation/create-ticket` — File a ticket
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\reports_escalation.py:191`

**Request body (`TicketRequest`):**
- `employee_id` (string, required)
- `company_id` (string, required UUID)
- `ticket_type` (string, required)
- `priority` (string, required) — `urgent` triggers auto-assign
- `subject`, `description`, `category` (string, required)
- `is_anonymous` (bool, default false)
- `confidential` (bool, default false)
- `attachments` (List[str], default [])

**Response 200 (`CreateTicketResponse`):** `{ success: true, ticket_id: "uuid", message: "Ticket created successfully" }`.

**Errors:** 400 invalid company_id UUID.

**Side effects:** writes `escalation_tickets` row; auto-assigns to first HR/admin user when conditions met.

---

### `POST /api/employer/export-reports` — CSV export
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\reports_escalation.py:240`

**Request body (`ExportRequest`):** `company_id?` (string), `time_range?` (`7d|30d|90d`, default treated as 90d when not 7d/30d), `userId?`, `reportType?` (default `company`), `dateRange?` (default `30d`), `department?` (default `all`), `riskLevel?` (default `all`).

**Response 200:** `Content-Type: text/csv`, attachment filename `wellness-reports-{time_range}.csv`. CSV columns: Report ID, Employee ID (last 8 chars), Session Type, Mood Rating, Stress Level, Energy Level, Work Satisfaction, Work Life Balance, Anxiety Level, Confidence Level, Sleep Quality, Overall Wellness, Risk Level.

**Errors:** 400 — `Company ID required`.

---

### `POST /api/export/pdf` — PDF export (stub)
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\reports_escalation.py:279`

**Request body:** `ExportRequest` (same as CSV).
**Response 200:** `application/pdf` with **dummy 24-byte PDF** body. Filename `wellness-report.pdf`.

**Quirks:** Returns a hard-coded `b"%PDF-1.4\n% Dummy PDF\n"` — no real rendering. The function does not even read any data from DB.

---

## routers/super_admin.py

Router prefix: `/api/admin`. Tag: `Super Admin`. **All endpoints super_admin only.**

> **Path collision warning:** This router and `routers/admin_metrics.py` both mount under `/api/admin`. Some paths are shared (e.g. `/companies`, `/companies/{id}`). The order of `app.include_router(...)` calls in `main.py` controls which wins. As of `main.py:447–449`, `employer_actions_router` and `employer_crud_router` precede `super_admin_router`, and `admin_metrics_router` is last. **The newer admin_metrics_router endpoints override super_admin_router for shared paths** — verify with running server.

### `GET /api/admin/me` — Super admin profile
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\super_admin.py:161`

**Response 200 (`AdminMeResponse`):** uid, email, role, displayName, isActive, createdAt?.

---

### `GET /api/admin/stats` — Platform-wide stats
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\super_admin.py:175`

**Response 200 (`PlatformStatsResponse`):** totalEmployers, totalEmployees, totalCompanies, totalUsers, activeUsers, inactiveUsers, roleBreakdown: `Dict[str,int]`, recentJoins, computedAt.

---

### `POST /api/admin/employers` — Create employer
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\super_admin.py:237`

**Request body:** `RegisterRequest` (same as `/api/auth/register`).
**Response 201:** `RegisterResponse` (alias for `TokenPair`).
**Side effects:** delegates to `routers.auth.register`. Errors: 409 duplicate email.

---

### `GET /api/admin/employers` — List all employers
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\super_admin.py:258`

**Query params:** `include_inactive` (bool, default false), `search?`, `page` (default 1), `limit` (default 20, max 100).

**Response 200 (raw dict):** `{ employers: [profile], total, page, limit, totalPages, hasNext, hasPrev }`. profile shape: uid, email, firstName, lastName, displayName, role, companyId?, companyName?, department?, position?, phone?, jobTitle?, hierarchyLevel, isActive, createdAt?, updatedAt?, createdBy?.

---

### `GET /api/admin/employers/{uid}` — Get one employer
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\super_admin.py:310`

**Response 200:** profile dict (see above).
**Errors:** 404 not found; 400 not an employer.

---

### `PATCH /api/admin/employers/{uid}` — Update employer
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\super_admin.py:326`

**Request body (`UpdateUserRequest`):** firstName?, lastName?, phone?, department?, position?, jobTitle?, hierarchyLevel?, isActive?, role?.

**Response 200 (`MutationResponse`):** success, message, updatedFields[].
**Errors:** 404 not found; 400 wrong role / no fields / invalid role; 403 super_admin role lock.

---

### `POST /api/admin/employers/{uid}/deactivate` — Deactivate employer
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\super_admin.py:342`

**Response 200 (`MutationResponse`).** Errors: 404 / 400.

**Side effects:** writes `audit_logs` row (`employer.deactivate`).

---

### `POST /api/admin/employers/{uid}/reactivate` — Reactivate
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\super_admin.py:361`

Mirrors deactivate. Audit action `employer.reactivate`.

---

### `DELETE /api/admin/employers/{uid}` — Hard-delete employer + company
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\super_admin.py:382`

**Response 200 (`MutationResponse`).**
**Errors:** 404 / 400 wrong role.

**Side effects:** deletes user row, deletes company row, audit row (`employer.delete`).

---

### `GET /api/admin/employees` — List all employees
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\super_admin.py:427`

**Query params:** `company_id?` (UUID), `role` (alias for `role_filter`, optional), `include_inactive` (bool, default false), `search?`, `page` (default 1), `limit` (default 20, max 100).

**Response 200:** `{ employees: [profile], total, page, limit, totalPages, hasNext, hasPrev }`.
**Errors:** 400 invalid company_id; 500 query.

---

### `GET /api/admin/employees/{uid}` — Get one employee
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\super_admin.py:496`

**Response 200:** profile dict.
**Errors:** 404 / 400 if user is employer or super_admin.

---

### `PATCH /api/admin/employees/{uid}` — Update employee
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\super_admin.py:512`

`UpdateUserRequest` body. `MutationResponse`. Errors: 404, 400, 403 super_admin lock.

---

### `DELETE /api/admin/employees/{uid}` — Hard-delete employee
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\super_admin.py:528`

**Response 200 (`MutationResponse`).**
**Errors:** 404 / 400.

**Side effects:** deletes user row, decrements `companies.employee_count`, audit row (`user.delete`).

---

### `GET /api/admin/companies` — List companies
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\super_admin.py:575`

**Query params:** `search?`, `page` (default 1), `limit` (default 20, max 100).

**Response 200 (raw dict):** companies[{id,name,industry?,size?,ownerId,employeeCount,website?,description?,createdAt?,updatedAt?}], total, page, limit, totalPages, hasNext, hasPrev.

> **Collision:** also defined in `admin_metrics.py` with extra fields. Last router registered wins.

---

### `GET /api/admin/companies/{company_id}` — Get company
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\super_admin.py:629`

**Response 200 (raw dict):** id, name, industry?, size?, ownerId, employeeCount, website?, address?, phone?, description?, logoUrl?, createdAt?, updatedAt?.

> **Collision:** also defined in `admin_metrics.py` returning richer `CompanyDetail`.

---

### `PATCH /api/admin/companies/{company_id}` — Update company
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\super_admin.py:664`

`UpdateCompanyRequest` body (same fields as employer's). `MutationResponse`. Side effect: when name changes, syncs `users.profile.company_name` for all employees.

---

### `POST /api/admin/users/{uid}/reset-password` — Force password
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\super_admin.py:735`

**Request body (`ResetPasswordRequest`):** `new_password` (string, min 8 chars).
**Response 200 (`MutationResponse`).**
**Errors:** 400 length; 404 user.

---

### `POST /api/admin/change-password` — Change own password
**Auth:** super_admin
**Source:** `d:\bai\humasql\routers\super_admin.py:768`

**Request body (`AdminChangePasswordRequest`):** current_password, new_password.
**Response 200 (`MutationResponse`).**
**Errors:** 400 length / same; 401 wrong current; 404 user.

---

## routers/users.py

Router prefix: **none** (paths begin `/api/employees/...` or `/api/hierarchy/test`). Tag: `Employees`.

> **Note:** `routers/employee_import.py` also mounts under `/api/employees`. Path conflicts are resolved by registration order in `main.py` (employee_import comes after users in main.py, so import-specific paths from employee_import.py win for those exact routes).

### `POST /api/employees/create` — Create employee
**Auth:** employer or hr
**Source:** `d:\bai\humasql\routers\users.py:191`

**Request body (`CreateEmployeeRequest`):**
- `email` (EmailStr, required)
- `password` (string, required, min 6 chars)
- `firstName` (string, required)
- `lastName` (string, required)
- `role` (string, default `employee`: `employee|manager|hr`)
- `department` (string, default `""`)
- `position` (string, default `""`)
- `phone` (string, optional)
- `managerId` (string, optional — `"none"` is treated as null)
- `hierarchyLevel` (int, default 1)
- `permissions` (Dict[str,bool], default `{}`)
- `sendWelcomeEmail` (bool, default true) — accepted but **not used by the function**

**Response 201 (`CreateEmployeeResponse`):** `{ success, uid, message }`.

**Errors:** 400 (password length / role / missing company_id / managerId not in company); 409 duplicate email; 500 save fail.

**Side effects:** writes `users` row; increments `companies.employee_count`; writes `audit_logs` row (`user.create`).

---

### `GET /api/employees` — List employees in caller's company
**Auth:** employer or hr
**Source:** `d:\bai\humasql\routers\users.py:305`

**Query params:** `include_inactive` (bool, default false), `department?`, `role` (alias for `role_filter`, optional), `search?`, `page` (default 1, ge 1), `limit` (default 20, ge 1, le 100).

**Response 200 (`ListEmployeesResponse`):** success, employees[`EmployeeProfile`], total, companyId, page?, limit?, totalPages?, hasNext?, hasPrev?.

`EmployeeProfile`: uid, email, firstName, lastName, role, department?, position?, phone?, companyId, managerId?, hierarchyLevel, isActive, permissions, createdAt?, createdBy?.

**Errors:** 500 DB.

---

### `GET /api/employees/{uid}` — Get one employee
**Auth:** employer or hr
**Source:** `d:\bai\humasql\routers\users.py:379`

**Response 200 (`EmployeeProfile`).**
**Errors:** 404 not found; 403 wrong company / employer profile.

---

### `PATCH /api/employees/{uid}` — Update employee
**Auth:** employer or hr
**Source:** `d:\bai\humasql\routers\users.py:408`

**Request body (`UpdateEmployeeRequest`):** all optional — firstName?, lastName?, department?, position?, phone?, managerId?, hierarchyLevel?, role?, permissions?.

**Response 200 (`UpdateEmployeeResponse`):** success, uid, message, updatedFields.
**Errors:** 404; 403 wrong company / employer; 400 invalid role / managerId / no fields.

**Side effects:** audit row (`user.update`).

---

### `POST /api/employees/{uid}/deactivate` — Soft-deactivate
**Auth:** employer or hr
**Source:** `d:\bai\humasql\routers\users.py:510`

**Response 200 (`DeactivateResponse`):** `{ success, uid, message }`.
**Errors:** 404 / 403.

**Side effects:** audit row (`user.deactivate`).

---

### `POST /api/employees/{uid}/reactivate` — Reactivate
**Auth:** employer or hr
**Source:** `d:\bai\humasql\routers\users.py:524`

Mirrors deactivate. Audit `user.reactivate`.

---

### `DELETE /api/employees/{uid}` — Permanently delete employee
**Auth:** employer only (HR blocked — explicit role check inside)
**Source:** `d:\bai\humasql\routers\users.py:579`

**Response 200 (`DeleteEmployeeResponse`):** `{ success, uid, message }`.
**Errors:** 403 not owner / wrong company / target is employer; 404 not found.

**Side effects:** reassigns direct reports' `manager_id` to deleted user's manager; deletes `check_ins`, `mh_sessions` rows; deletes user row; decrements `companies.employee_count`; writes audit (`user.delete`).

---

### `POST /api/employees/bulk-create` — Bulk create up to 50
**Auth:** employer or hr
**Source:** `d:\bai\humasql\routers\users.py:683`

**Request body:** `List[BulkCreateItem]` (raw JSON array, NOT wrapped object). Each item: email, password (min 6), firstName, lastName, role (default `employee`: `employee|manager|hr`), department?, position?, phone?, managerId?, hierarchyLevel? (default 1).

**Response 201 (`BulkCreateResponse`):** success, created, failed, results[`BulkCreateResult` (email, success, uid?, error?, warnings?)], companyId.

**Errors:** 400 — empty list or > 50.

**Side effects:** writes `users` rows; increments `companies.employee_count` once at end; no audit rows.

**Quirks:** Body is a raw array — clients must POST `[{...}, {...}]`, NOT `{employees:[...]}`.

---

### `PUT /api/employees/{uid}/transfer` — Reassign manager / dept
**Auth:** employer or hr
**Source:** `d:\bai\humasql\routers\users.py:834`

**Request body (`TransferEmployeeRequest`):** newManagerId? (string or `"none"`), newDepartment?, newPosition?, newHierarchyLevel?.

**Response 200 (`TransferEmployeeResponse`):** `{ success, uid, message, changes: { field: {from, to} } }`.

**Errors:** 404; 403 wrong company / employer; 400 invalid managerId / no fields.

---

### `GET /api/employees/{uid}/activity` — Activity summary
**Auth:** employer or hr
**Source:** `d:\bai\humasql\routers\users.py:920`

**Response 200 (`ActivitySummaryResponse`):** uid, companyId, totalCheckIns, totalSessions, lastActiveAt?, avgMoodScore?, avgStressLevel?, riskLevel?, sessionModalities: `Dict[str,int]`, computedAt.

**Errors:** 404 / 403 wrong company / employer.

---

### `GET /api/hierarchy/test` — Hierarchy test stub
**Auth:** Any authenticated (route has `dependencies=[Depends(get_current_user)]`)
**Source:** `d:\bai\humasql\routers\users.py:1046`

**Query params:** `userId` (required), `companyId` (required), `testType` (default `"all"`).
**Response 200 (`HierarchyTestGetResponse`):** `{ success: true, userId, companyId, testType, results: { message: "Hierarchy tests migrated to Python stub." } }`.

**Quirks:** Endpoint is a fixed stub — does not actually test hierarchy.

---

### `POST /api/hierarchy/test` — Hierarchy access check stub
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\users.py:1061`

**Request body (`HierarchyTestPost`):** userId, targetUserId, companyId.
**Response 200 (`HierarchyTestPostResponse`):** `{ success:true, canAccess:true, userId, targetUserId, message:"User has access to target employee data (mocked)." }`.

**Quirks:** Always returns `canAccess: true`. Pure mock.

---

## routers/voice_calls.py

Router prefix: **none** (paths are `/api/call`, `/api/text-to-speech`, `/api/transcribe`). Tag: `Voice Calls`. **Router-level: `Depends(get_current_user)`** — all endpoints require any authenticated user.

### `POST /api/call` — Action-dispatched call lifecycle
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\voice_calls.py:46`

**Request body (`CallRequest`):**
- `action` (string, required: `initiate|accept|reject|end|update_status`)
- `callData` (dict, required, action-specific)

Action shapes (read from `callData`):
- `initiate`: requires `callerId`, `receiverId`. Optional `callType` (default `"voice"`), `metadata`. Returns `{ success, callId, message }`.
- `accept`: requires `callId`. Returns `{ success, message }`.
- `reject`: requires `callId`. Optional `reason` (default `"rejected"`). Returns `{ success, message }`.
- `end`: requires `callId`, `userId`. Optional `reason`. Returns `{ success, message }`.
- `update_status`: requires `callId`, `status`. Optional `metadata` (merged). Returns `{ success, message }`.

**Errors:** 400 — invalid action, missing IDs, malformed UUID.

**Side effects:** writes/updates `calls` and `call_sessions` rows.

---

### `POST /api/text-to-speech` — TTS via ElevenLabs
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\voice_calls.py:152`

**Request body (`TTSRequest`):**
- `text` (string, required)
- `voice` (string, default `"pNInz6obpgDQGcFmaJgB"`)
- `addEmotion` (bool, default true) — accepted but **ignored**

**Response 200:** `audio/mpeg` bytes (with `Cache-Control: public, max-age=3600`).
**Errors:** 500 — `ElevenLabs API key not configured.`. Upstream errors propagate as that status code with raw text body.

---

### `POST /api/transcribe` — STT via OpenAI Whisper
**Auth:** Any authenticated
**Source:** `d:\bai\humasql\routers\voice_calls.py:187`

**Form field:** `audio` (file, required).
**Response 200 (`TranscribeResponse`):** `{ "text": "string" }`.
**Errors:** 500 — `OPENAI_API_KEY required`. Upstream Whisper errors propagate.
