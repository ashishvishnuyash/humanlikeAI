# Gamification — Admin Dashboard Section Plan

> Extends the existing gamification system (`routers/community_gamification.py`)
> with admin visibility, challenge management, physical health integration, and company-level analytics.

---

## What Already Exists (Do Not Rebuild)

| What | Where | Details |
|---|---|---|
| `user_gamification` collection | Firestore | `current_streak`, `longest_streak`, `total_points`, `level`, `badges[]`, `challenges_completed`, `last_check_in` |
| `wellness_challenges` collection | Firestore | Per-company active challenges |
| `community_posts` / `community_replies` | Firestore | Anonymous community boards |
| `anonymous_profiles` | Firestore | Per-user anonymous identity |
| Points engine | `community_gamification.py` | check-in = 10pts, first = 20pts, conversation = 15pts, challenge = 50pts |
| Level formula | `calculate_level()` | Levels 1–10+, threshold-based |
| Badge system | `check_badges()` | 8 badges: `first_check_in`, `week_warrior`, `month_master`, `century_streak`, `point_collector`, `point_master`, `level_five`, `level_ten` |
| `POST /gamification` | Router | 5 actions: `get_user_stats`, `check_in`, `conversation_complete`, `get_available_challenges`, `join_challenge` |

---

## What This Plan Adds

1. **Admin gamification analytics** — platform-wide and per-company gamification health metrics
2. **Employer gamification view** — company leaderboard + challenge participation (anonymised)
3. **Challenge management** — admin can create, edit, activate/deactivate challenges
4. **Physical health integration** — physical health check-ins earn points + new health badges
5. **Enhanced point events** — more actions trigger points beyond just chat check-ins
6. **Leaderboard** — anonymous company-scoped leaderboard endpoint

---

## New Firestore Collections

### `gamification_events`
Every point-earning event logged here for audit + analytics.

```
{
    user_id:      str,
    company_id:   str,
    event_type:   str,     # "daily_checkin" | "physical_checkin" | "conversation"
                           # | "challenge_complete" | "medical_upload" | "streak_bonus"
    points:       int,
    metadata:     dict,    # e.g. { streak: 7, badge_earned: "week_warrior" }
    created_at:   Timestamp
}
```

### `challenges` (rename/replace `wellness_challenges`)
Managed by admin, company-scoped or platform-wide.

```
{
    challenge_id:   str,
    company_id:     str | "platform",  # "platform" = visible to all companies
    title:          str,
    description:    str,
    category:       str,   # "mental_health" | "physical_health" | "community" | "streak"
    type:           str,   # "individual" | "team"
    goal_metric:    str,   # "checkins_count" | "streak_days" | "exercise_days" | "points_earned"
    goal_value:     int,   # target number
    points_reward:  int,   # points on completion
    badge_reward:   str | null,   # badge id if completing awards a badge
    start_date:     str,
    end_date:       str,
    is_active:      bool,
    created_by:     str,   # admin uid
    created_at:     Timestamp,
    participants:   int,   # denormalised count
    completions:    int    # denormalised count
}
```

### `user_challenge_progress`
Tracks each user's progress on joined challenges.

```
{
    user_id:      str,
    company_id:   str,
    challenge_id: str,
    joined_at:    Timestamp,
    progress:     int,     # current value toward goal
    goal_value:   int,
    completed:    bool,
    completed_at: Timestamp | null,
    points_awarded: bool
}
```

---

## Point Events — Extended Table

Extend the existing points engine to fire on more actions:

| Action | Points | Where to Wire |
|---|---|---|
| Daily mental health check-in | 10 | `POST /gamification` (already exists) |
| First ever check-in | +10 bonus | `POST /gamification` (already exists) |
| Conversation complete | 15 | `POST /gamification` (already exists) |
| Challenge complete | 50 | `POST /gamification` (already exists) |
| Physical health check-in | 8 | `POST /api/physical-health/check-in` — wire in |
| Medical document upload | 15 | `POST /api/physical-health/medical/upload` — wire in |
| 7-day physical streak | 25 bonus | Physical health check-in streak check |
| Community post created | 5 | `POST /community` action `create_post` — wire in |
| Generate health report | 10 | `POST /api/physical-health/reports/generate` — wire in |

---

## New Badges

Add to `check_badges()` in `community_gamification.py`:

| Badge ID | Trigger | Display Name |
|---|---|---|
| `health_starter` | First physical health check-in | Health Starter |
| `health_week` | 7 consecutive physical check-in days | Health Week |
| `health_month` | 30 physical check-in days (total, not streak) | Health Habit |
| `doc_uploader` | First medical document uploaded | Know Your Numbers |
| `community_voice` | First community post | Community Voice |
| `all_rounder` | Used all 4 features (chat, physical, community, report) | All-Rounder |
| `challenge_champion` | Completed 5 challenges | Challenge Champion |

Badge metadata is stored in the `badges[]` array on `user_gamification` as badge IDs.
The frontend maps IDs to display names and icons.

---

## Backend Changes

### 1. Wire Physical Health into Points Engine

**File:** `routers/physical_health.py`

After a successful check-in save, call `_award_points(uid, company_id, "physical_checkin", 8, db)`.

After a successful medical upload, call `_award_points(uid, company_id, "medical_upload", 15, db)`.

Add a shared helper (can live in `utils/gamification_utils.py`):

```python
def award_points(
    user_id: str,
    company_id: str,
    event_type: str,
    points: int,
    db,
    metadata: dict = None,
) -> None:
    """
    Non-blocking: updates user_gamification total_points + level,
    logs to gamification_events, and checks for new badges.
    Should be called with asyncio.create_task() to stay non-blocking.
    """
```

---

### 2. Leaderboard Endpoint

**File:** `routers/community_gamification.py` — add new action to `POST /gamification`

Action: `get_leaderboard`

```python
# Query user_gamification where company_id == x
# Sort by total_points DESC, limit 20
# Return anonymous display (use anonymous_profiles display_name + avatar_color)
# Never return real user_id or name
```

Response:
```json
{
  "action": "get_leaderboard",
  "success": true,
  "leaderboard": [
    {
      "rank": 1,
      "display_name": "User AB12",
      "avatar_color": "#4ECDC4",
      "total_points": 1840,
      "level": 6,
      "current_streak": 14,
      "badges_count": 5
    }
  ],
  "my_rank": 4,
  "my_points": 920
}
```

---

### 3. Admin — Challenge Management Endpoints

**File:** `routers/admin_metrics.py` (new file per admin dashboard plan)

```
POST   /api/admin/challenges              → Create challenge
GET    /api/admin/challenges              → List all (platform + company-scoped)
PATCH  /api/admin/challenges/{id}         → Edit / activate / deactivate
DELETE /api/admin/challenges/{id}         → Delete
GET    /api/admin/challenges/{id}/stats   → Participants, completions, completion rate
```

**Create challenge request:**
```json
{
  "company_id": "abc123",
  "title": "7-Day Wellness Streak",
  "description": "Complete 7 consecutive daily check-ins this week.",
  "category": "streak",
  "type": "individual",
  "goal_metric": "checkins_count",
  "goal_value": 7,
  "points_reward": 75,
  "badge_reward": "week_warrior",
  "start_date": "2026-04-21",
  "end_date": "2026-04-28",
  "is_active": true
}
```

---

### 4. Admin — Gamification Analytics Endpoints

**File:** `routers/admin_metrics.py`

```
GET /api/admin/gamification/overview              → Platform-wide gamification health
GET /api/admin/gamification/companies/{id}        → Per-company gamification breakdown
GET /api/admin/gamification/leaderboard/{company} → Full company leaderboard (admin view, with names)
```

**`/api/admin/gamification/overview` response:**
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
  "new_badges_this_week": 124,
  "leaderboard_top5": [...]
}
```

**`/api/admin/gamification/companies/{id}` response:**
```json
{
  "company_id": "...",
  "company_name": "Acme",
  "total_players": 87,
  "active_players_7d": 54,
  "avg_points": 740,
  "avg_level": 3.8,
  "avg_streak": 6.2,
  "top_badge_earners": 12,
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

---

### 5. Employer — Gamification View

**File:** `routers/employer_dashboard.py` — add new endpoint

```
GET /api/employer/gamification
```

Returns company-scoped gamification metrics for the employer admin (no individual names, no real IDs):

```json
{
  "participation_rate_pct": 62.1,
  "avg_level": 3.8,
  "avg_streak_days": 6.2,
  "top_badges": ["week_warrior", "health_week", "first_check_in"],
  "active_challenges": [
    {
      "challenge_id": "...",
      "title": "7-Day Streak",
      "participants": 34,
      "completions": 12,
      "completion_pct": 35.3,
      "ends_at": "2026-04-28"
    }
  ],
  "leaderboard": [
    { "rank": 1, "display_name": "User AB12", "avatar_color": "#4ECDC4", "total_points": 1840, "level": 6 }
  ],
  "points_issued_this_week": 4320,
  "new_badges_this_week": 8
}
```

---

## KPIs to Add to Admin Dashboard (Phase 4 Extension)

Extend the KPI tables in `admin_dashboard.md`:

### User-Level Gamification KPIs

| KPI | Formula | Source |
|---|---|---|
| Gamification Level | `user_gamification.level` | `user_gamification` |
| Current Streak | `user_gamification.current_streak` | `user_gamification` |
| Badge Count | `len(user_gamification.badges)` | `user_gamification` |
| Points This Month | Sum of `gamification_events` MTD | `gamification_events` |
| Challenge Participation | `challenges_joined / active_challenges` | `user_challenge_progress` |

### Company-Level Gamification KPIs

| KPI | Formula | Source |
|---|---|---|
| Gamification Participation Rate | `% users with any points in last 30d` | `user_gamification` |
| Avg Team Level | `avg(level)` across company | `user_gamification` |
| Avg Team Streak | `avg(current_streak)` | `user_gamification` |
| Challenge Completion Rate | `completions / participants` | `user_challenge_progress` |
| Points Velocity | `total_points_issued / days` | `gamification_events` |

---

## Admin UI — Gamification Pages

Add to the admin frontend (from Phase 6 of `admin_dashboard.md`):

### Platform Gamification Overview
- Total active players, avg level, points issued MTD
- Points breakdown by event type (donut chart)
- Badge leaderboard (most earned badges platform-wide)
- Challenge completion rate trend line

### Company Gamification Drilldown
- Tab inside Company Detail page
- Participation %, avg level, avg streak
- Badge distribution bar chart
- Active challenges with participation/completion bars
- Anonymous leaderboard table (top 10)

### Challenge Management
- Table: all challenges (platform + per-company), status badge, participant count
- "New Challenge" button → form (title, category, goal metric, reward, dates)
- Inline activate/deactivate toggle
- Challenge detail: participants, completions, completion %, day-by-day progress chart

### Employer Gamification Dashboard
- Shown to company admins, not super-admin only
- Anonymous leaderboard (top 20, display names only)
- Active challenges + progress
- Team participation rate gauge
- "Create Company Challenge" button (goes to admin for approval, or directly creates if employer has challenge-create permission)

---

## Files to Change Summary

| File | Type | Change |
|---|---|---|
| `utils/gamification_utils.py` | **New** | Shared `award_points()` helper |
| `routers/community_gamification.py` | **Modify** | Add `get_leaderboard` action + new badges in `check_badges()` |
| `routers/physical_health.py` | **Modify** | Call `award_points()` after check-in + medical upload |
| `routers/admin_metrics.py` | **Modify** | Add challenge CRUD + gamification analytics endpoints |
| `routers/employer_dashboard.py` | **Modify** | Add `GET /api/employer/gamification` endpoint |

## New Firestore Collections Summary

| Collection | Purpose |
|---|---|
| `gamification_events` | Audit log of every point-earning action |
| `challenges` | Admin-managed challenges (replaces `wellness_challenges`) |
| `user_challenge_progress` | Per-user per-challenge progress tracking |

---

## Privacy Rules

- Leaderboard always uses `anonymous_profiles.display_name` — never real names
- Admin leaderboard (super-admin only) may show real names — employer leaderboard never does
- `gamification_events` stores `user_id` for admin audit purposes — never exposed to employers
- Challenge completion is shown as aggregate stats only at employer level
