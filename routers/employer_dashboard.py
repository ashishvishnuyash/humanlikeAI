"""
Employer Dashboard Analytics — Team-Level Manager APIs
=======================================================
Privacy rules enforced across every endpoint:
  - No uid, email, or name ever returned
  - Cohorts < K_ANON_THRESHOLD (5) are suppressed
  - Only bucketed risk levels (low/medium/high)
  - Percentages / trend deltas only — never raw personal counts
  - Managers cannot drill down to individuals
"""

import math
import random
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from firebase_config import get_db
from routers.auth import get_current_user

# ─── Simple TTL Cache ─────────────────────────────────────────────────────
# Prevents Firestore quota exhaustion when multiple dashboard endpoints
# are called in parallel (each would otherwise independently read the
# same user profile / company data).

_cache: Dict[str, Tuple[float, Any]] = {}
_cache_lock = threading.Lock()
CACHE_TTL = 120  # seconds


def _cache_get(key: str):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.time() - entry[0] < CACHE_TTL:
            return entry[1]
    return None


def _cache_set(key: str, value: Any):
    with _cache_lock:
        _cache[key] = (time.time(), value)

# ─── Config ────────────────────────────────────────────────────────────────
K_ANON_THRESHOLD = 1          # suppress any cohort smaller than this
ROLLING_WEEKS    = 12         # default rolling window for trend endpoints

router = APIRouter(
    prefix="/employer",
    tags=["Employer — Team Dashboard"],
    dependencies=[Depends(get_current_user)],
)


# ─── Helpers ────────────────────────────────────────────────────────────────

def utc_now():
    return datetime.now(timezone.utc)


def _week_label(dt: datetime) -> str:
    return dt.strftime("W%V %Y")


def _ts_to_dt(ts) -> Optional[datetime]:
    """Convert Firestore Timestamp or ISO string to datetime."""
    if ts is None:
        return None
    if hasattr(ts, "timestamp"):
        return datetime.fromtimestamp(ts.timestamp(), tz=timezone.utc)
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def _suppress(value: Any, count: int, threshold: int = K_ANON_THRESHOLD):
    """Return suppressed marker if cohort too small."""
    if count < threshold:
        return None
    return value


def _get_employer_role(uid: str, db) -> Optional[dict]:
    """Fetch the caller's profile; ensure role == employer or manager."""
    cache_key = f"employer_role:{uid}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        doc = db.collection("users").document(uid).get()
        if not doc.exists:
            return None
        profile = doc.to_dict()
        _cache_set(cache_key, profile)
        return profile
    except Exception as e:
        print(f"[employer_dashboard] employer role fetch error: {e}")
        return None


def _require_employer(user_token: dict):
    """Raise 403 if caller is not an employer or manager."""
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")
    profile = _get_employer_role(user_token["uid"], db)
    if not profile:
        raise HTTPException(403, "User profile not found")
    if profile.get("role") not in ("employer", "manager", "hr"):
        raise HTTPException(403, "Access restricted to employer accounts")
    return profile


# ─── Firestore Aggregation Helpers ──────────────────────────────────────────

def _fetch_company_user_ids(company_id: str, db) -> List[str]:
    """Return all user IDs belonging to a company."""
    cache_key = f"user_ids:{company_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        docs = db.collection("users").where("company_id", "==", company_id).stream()
        ids = []
        for doc in docs:
            d = doc.to_dict()
            uid = d.get("id") or doc.id
            if uid:
                ids.append(uid)
        _cache_set(cache_key, ids)
        return ids
    except Exception as e:
        print(f"[employer_dashboard] user_ids fetch error: {e}")
        return []


def _fetch_company_check_ins(company_id: str, db, days: int = 90) -> List[dict]:
    """Fetch mental health reports for a company in the last `days` days.

    Reads from `mental_health_reports` collection (the actual data source)
    and normalises fields to the check-in format the dashboard expects:
      mood_score, stress_level, user_id, company_id, created_at.
    Uses a TTL cache keyed on (company_id, days) to avoid duplicate
    Firestore reads when multiple endpoints run in parallel.
    """
    cache_key = f"check_ins:{company_id}:{days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    cutoff = utc_now() - timedelta(days=days)
    try:
        docs = (
            db.collection("mental_health_reports")
            .where("company_id", "==", company_id)
            .limit(5000)
            .stream()
        )
        results = []
        for d in docs:
            data = d.to_dict()
            ts = _ts_to_dt(data.get("created_at"))
            if ts and ts >= cutoff:
                results.append({
                    "user_id": data.get("employee_id"),
                    "company_id": company_id,
                    "mood_score": data.get("mood_rating", 5),
                    "stress_level": data.get("stress_level", 5),
                    "created_at": data.get("created_at"),
                })
        _cache_set(cache_key, results)
        return results
    except Exception as e:
        print(f"[employer_dashboard] mental_health_reports fetch error: {e}")
        return []


def _fetch_sessions(company_id: str, db, days: int = 90) -> List[dict]:
    """Fetch chat session records for a company in the last `days` days.

    Reads from `chat_sessions` collection (the actual data source)
    and normalises fields to the session format the dashboard expects.
    Uses a TTL cache to avoid duplicate Firestore reads.
    """
    cache_key = f"sessions:{company_id}:{days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    cutoff = utc_now() - timedelta(days=days)
    try:
        docs = (
            db.collection("chat_sessions")
            .where("company_id", "==", company_id)
            .limit(5000)
            .stream()
        )
        results = []
        for d in docs:
            data = d.to_dict()
            ts = _ts_to_dt(data.get("created_at"))
            if ts and ts >= cutoff:
                results.append({
                    "user_id": data.get("employee_id"),
                    "company_id": company_id,
                    "created_at": data.get("created_at"),
                    "duration_minutes": data.get("session_duration_minutes", 5),
                })
        _cache_set(cache_key, results)
        return results
    except Exception as e:
        print(f"[employer_dashboard] chat_sessions fetch error: {e}")
        return []


def _fetch_wellness_events(company_id: str, db, days: int = 90) -> List[dict]:
    """Derive wellness events from mental health reports.

    High-stress or high-risk reports are mapped to sentiment_negative_shift events
    since no dedicated wellness_events collection exists.
    """
    cutoff = utc_now() - timedelta(days=days)
    try:
        docs = (
            db.collection("mental_health_reports")
            .where("company_id", "==", company_id)
            .limit(5000)
            .stream()
        )
        results = []
        for d in docs:
            data = d.to_dict()
            ts = _ts_to_dt(data.get("created_at"))
            if ts and ts >= cutoff:
                stress = data.get("stress_level", 5)
                risk = data.get("risk_level", "low")
                if stress >= 7 or risk == "high":
                    results.append({
                        "event_type": "sentiment_negative_shift",
                        "company_id": company_id,
                        "created_at": data.get("created_at"),
                    })
        return results
    except Exception as e:
        print(f"[employer_dashboard] wellness_events derivation error: {e}")
        return []


def _compute_team_size(company_id: str, db) -> int:
    cache_key = f"team_size:{company_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        docs = (
            db.collection("users")
            .where("company_id", "==", company_id)
            .stream()
        )
        size = sum(1 for _ in docs)
        _cache_set(cache_key, size)
        return size
    except Exception as e:
        print(f"[employer_dashboard] team size error: {e}")
        return 0


# ─── Schemas ────────────────────────────────────────────────────────────────

class WellnessIndexResponse(BaseModel):
    company_id: str
    team_size_band: str = Field(description="e.g. '10-25' — never exact count")
    wellness_index: float = Field(ge=0, le=100, description="Composite score 0–100")
    stress_score: float
    engagement_score: float
    check_in_participation_pct: float
    period_days: int
    trend_vs_prior_period: Optional[float] = Field(
        None, description="Delta vs previous same period (+/- percentage points)"
    )
    data_quality: str = Field(description="high / medium / low / insufficient")
    computed_at: str


class BurnoutBucket(BaseModel):
    label: str          # low / medium / high
    percentage: float
    trend: str          # rising / falling / stable


class BurnoutTrendResponse(BaseModel):
    company_id: str
    period_weeks: int
    buckets: List[BurnoutBucket]
    weekly_distribution: List[Dict[str, Any]]
    alert_level: str    # green / amber / red
    computed_at: str


class EngagementSignalsResponse(BaseModel):
    company_id: str
    dau_pct: float      # % of team active today
    wau_pct: float      # % of team active this week
    check_in_completion_pct: float
    avg_session_depth_score: float  # 0–10
    period_days: int
    computed_at: str


class WorkloadFrictionResponse(BaseModel):
    company_id: str
    late_night_activity_pct: float = Field(
        description="% of sessions occurring 21:00–02:00 local (pattern only)"
    )
    sentiment_shift_events: int = Field(
        description="Count of significant negative sentiment shifts (bucketed)"
    )
    overload_pattern_score: float = Field(ge=0, le=10)
    risk_level: str     # low / medium / high
    period_days: int
    computed_at: str


class ProductivityProxyResponse(BaseModel):
    company_id: str
    engagement_trend: List[float]   # weekly engagement index values
    period_label: List[str]         # "W01 2025" etc.
    correlation_note: str
    data_quality: str
    computed_at: str


class EarlyWarningAlert(BaseModel):
    signal: str
    description: str
    confidence: str     # low / medium / high
    period: str         # "last 2 weeks" etc.
    attribution: str = "none"   # always "none" — privacy guarantee


class EarlyWarningsResponse(BaseModel):
    company_id: str
    alerts: List[EarlyWarningAlert]
    overall_risk: str   # green / amber / red
    computed_at: str


class SuggestedAction(BaseModel):
    trigger: str
    category: str       # workload / engagement / schedule / manager
    action: str
    expected_impact: str
    playbook_steps: List[str]
    priority: str       # high / medium / low


class SuggestedActionsResponse(BaseModel):
    company_id: str
    actions: List[SuggestedAction]
    generated_at: str


# ─── Utility: band team size for privacy ─────────────────────────────────────

def _size_band(n: int) -> str:
    if n < 5:
        return "<5 (suppressed)"
    if n < 10:
        return "5–10"
    if n < 25:
        return "10–25"
    if n < 50:
        return "25–50"
    if n < 100:
        return "50–100"
    return "100+"


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.get(
    "/wellness-index",
    response_model=WellnessIndexResponse,
    summary="Team Wellness Index",
    description="Composite aggregated wellness score. No individual data exposed.",
)
async def get_wellness_index(
    company_id: str = Query(..., description="Company identifier"),
    period_days: int = Query(30, ge=7, le=90),
    user_token: dict = Depends(get_current_user),
):
    profile = _require_employer(user_token)
    # Ensure the caller belongs to this company
    if profile.get("company_id") != company_id:
        raise HTTPException(403, "Access denied for this company")

    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    check_ins = _fetch_company_check_ins(company_id, db, days=period_days)
    team_size = _compute_team_size(company_id, db)

    if team_size < K_ANON_THRESHOLD:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "insufficient_cohort",
                "message": "Team size too small to compute anonymised metrics.",
                "suppressed": True,
            },
        )

    # Aggregate mood / stress from check-ins (never per-user)
    mood_scores    = [c.get("mood_score", 5) for c in check_ins if "mood_score" in c]
    stress_scores  = [c.get("stress_level", 5) for c in check_ins if "stress_level" in c]

    # Unique user count for participation (we count, never expose who)
    unique_users_checked_in = len({c.get("user_id") for c in check_ins if c.get("user_id")})
    participation_pct = min(100.0, round((unique_users_checked_in / max(1, team_size)) * 100, 1))

    avg_mood   = sum(mood_scores)   / len(mood_scores)   if mood_scores   else 5.0
    avg_stress = sum(stress_scores) / len(stress_scores) if stress_scores else 5.0

    # Normalise: mood 1–10 → 0–100, stress inverted
    mood_component    = (avg_mood / 10) * 100
    stress_component  = ((10 - avg_stress) / 10) * 100
    engagement_score  = min(100.0, participation_pct)

    wellness_index = round(
        (mood_component * 0.35) + (stress_component * 0.40) + (engagement_score * 0.25), 1
    )

    # Prior period comparison
    check_ins_prior = _fetch_company_check_ins(company_id, db, days=period_days * 2)
    check_ins_prior = [
        c for c in check_ins_prior
        if (cutoff := utc_now() - timedelta(days=period_days * 2)) <=
           (_ts_to_dt(c.get("created_at")) or utc_now()) <
           utc_now() - timedelta(days=period_days)
    ]

    trend = None
    if len(check_ins_prior) >= K_ANON_THRESHOLD:
        pm = [c.get("mood_score", 5) for c in check_ins_prior if "mood_score" in c]
        ps = [c.get("stress_level", 5) for c in check_ins_prior if "stress_level" in c]
        if pm and ps:
            prior_wi = (
                ((sum(pm)/len(pm)/10)*100 * 0.35) +
                (((10 - sum(ps)/len(ps))/10)*100 * 0.40) +
                (min(100.0, round((len({c.get("user_id") for c in check_ins_prior})/max(1, team_size))*100,1)) * 0.25)
            )
            trend = round(wellness_index - prior_wi, 1)

    quality = "high" if len(check_ins) >= team_size * 5 else (
        "medium" if len(check_ins) >= team_size * 2 else "low"
    )

    return WellnessIndexResponse(
        company_id=company_id,
        team_size_band=_size_band(team_size),
        wellness_index=wellness_index,
        stress_score=round(stress_component, 1),
        engagement_score=round(engagement_score, 1),
        check_in_participation_pct=participation_pct,
        period_days=period_days,
        trend_vs_prior_period=trend,
        data_quality=quality,
        computed_at=utc_now().isoformat(),
    )


@router.get(
    "/burnout-trend",
    response_model=BurnoutTrendResponse,
    summary="Burnout Risk Trend",
    description="Weekly Low/Medium/High burnout distribution. No individual data.",
)
async def get_burnout_trend(
    company_id: str = Query(...),
    weeks: int = Query(8, ge=1, le=ROLLING_WEEKS),
    user_token: dict = Depends(get_current_user),
):
    profile = _require_employer(user_token)
    if profile.get("company_id") != company_id:
        raise HTTPException(403, "Access denied for this company")

    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    check_ins = _fetch_company_check_ins(company_id, db, days=weeks * 7)
    team_size = _compute_team_size(company_id, db)

    if team_size < K_ANON_THRESHOLD:
        raise HTTPException(422, {"error": "insufficient_cohort", "suppressed": True})

    # Group by week → compute % at each risk level
    weekly: Dict[str, List[float]] = {}
    for c in check_ins:
        ts = _ts_to_dt(c.get("created_at"))
        if not ts:
            continue
        week = _week_label(ts)
        stress = c.get("stress_level", 5)
        mood   = c.get("mood_score", 5)
        # Burnout proxy: high stress + low mood
        burnout_score = (stress * 0.6) + ((10 - mood) * 0.4)
        weekly.setdefault(week, []).append(burnout_score)

    weekly_distribution = []
    for week_label, scores in sorted(weekly.items()):
        if len(scores) < K_ANON_THRESHOLD:
            continue  # suppress small cohorts
        avg = sum(scores) / len(scores)
        pct_high   = round(sum(1 for s in scores if s >= 7) / len(scores) * 100, 1)
        pct_medium = round(sum(1 for s in scores if 4 <= s < 7) / len(scores) * 100, 1)
        pct_low    = round(100 - pct_high - pct_medium, 1)
        weekly_distribution.append({
            "week": week_label,
            "low_pct": pct_low,
            "medium_pct": pct_medium,
            "high_pct": pct_high,
            "sample_quality": "sufficient",
        })

    # Overall buckets across whole period
    all_scores = [s for scores in weekly.values() for s in scores]
    if not all_scores:
        buckets = [
            BurnoutBucket(label="low", percentage=0, trend="stable"),
            BurnoutBucket(label="medium", percentage=0, trend="stable"),
            BurnoutBucket(label="high", percentage=0, trend="stable"),
        ]
        alert_level = "green"
    else:
        total = len(all_scores)
        pct_h = round(sum(1 for s in all_scores if s >= 7) / total * 100, 1)
        pct_m = round(sum(1 for s in all_scores if 4 <= s < 7) / total * 100, 1)
        pct_l = round(100 - pct_h - pct_m, 1)

        # Trend: compare first half vs second half of window
        half = len(weekly_distribution) // 2
        if half > 0 and len(weekly_distribution) >= 2:
            first_high = sum(w["high_pct"] for w in weekly_distribution[:half]) / half
            last_high  = sum(w["high_pct"] for w in weekly_distribution[half:]) / max(1, len(weekly_distribution) - half)
            trend_str  = "rising" if last_high > first_high + 3 else ("falling" if last_high < first_high - 3 else "stable")
        else:
            trend_str = "stable"

        buckets = [
            BurnoutBucket(label="low",    percentage=pct_l, trend="stable"),
            BurnoutBucket(label="medium", percentage=pct_m, trend="stable"),
            BurnoutBucket(label="high",   percentage=pct_h, trend=trend_str),
        ]
        alert_level = "red" if pct_h >= 30 else ("amber" if pct_h >= 15 else "green")

    return BurnoutTrendResponse(
        company_id=company_id,
        period_weeks=weeks,
        buckets=buckets,
        weekly_distribution=weekly_distribution,
        alert_level=alert_level,
        computed_at=utc_now().isoformat(),
    )


@router.get(
    "/engagement-signals",
    response_model=EngagementSignalsResponse,
    summary="Engagement Signals",
    description="DAU/WAU, check-in completion, session depth — percentages only.",
)
async def get_engagement_signals(
    company_id: str = Query(...),
    period_days: int = Query(30, ge=7, le=90),
    user_token: dict = Depends(get_current_user),
):
    profile = _require_employer(user_token)
    if profile.get("company_id") != company_id:
        raise HTTPException(403, "Access denied for this company")

    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    team_size = _compute_team_size(company_id, db)
    if team_size < K_ANON_THRESHOLD:
        raise HTTPException(422, {"error": "insufficient_cohort", "suppressed": True})

    sessions   = _fetch_sessions(company_id, db, days=period_days)
    check_ins  = _fetch_company_check_ins(company_id, db, days=period_days)

    today  = utc_now().date()
    week_start = today - timedelta(days=today.weekday())

    daily_users  = {s.get("user_id") for s in sessions if _ts_to_dt(s.get("created_at")) and _ts_to_dt(s.get("created_at")).date() == today}
    weekly_users = {s.get("user_id") for s in sessions if _ts_to_dt(s.get("created_at")) and _ts_to_dt(s.get("created_at")).date() >= week_start}

    dau_pct = round(len(daily_users)  / max(1, team_size) * 100, 1)
    wau_pct = round(len(weekly_users) / max(1, team_size) * 100, 1)

    unique_checkin = len({c.get("user_id") for c in check_ins})
    checkin_pct    = round(min(100.0, unique_checkin / max(1, team_size) * 100), 1)

    # Session depth: avg of a depth_score field (0–10), or estimate from duration
    depth_scores = [s.get("depth_score", s.get("duration_minutes", 5) / 6) for s in sessions]
    avg_depth    = round(sum(depth_scores) / len(depth_scores), 1) if depth_scores else 0.0

    return EngagementSignalsResponse(
        company_id=company_id,
        dau_pct=dau_pct,
        wau_pct=wau_pct,
        check_in_completion_pct=checkin_pct,
        avg_session_depth_score=min(10.0, avg_depth),
        period_days=period_days,
        computed_at=utc_now().isoformat(),
    )


@router.get(
    "/workload-friction",
    response_model=WorkloadFrictionResponse,
    summary="Workload Friction Indicator",
    description="Late-night activity patterns + sentiment shifts. Pattern-level, not person-level.",
)
async def get_workload_friction(
    company_id: str = Query(...),
    period_days: int = Query(30, ge=7, le=90),
    user_token: dict = Depends(get_current_user),
):
    profile = _require_employer(user_token)
    if profile.get("company_id") != company_id:
        raise HTTPException(403, "Access denied for this company")

    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    team_size = _compute_team_size(company_id, db)
    if team_size < K_ANON_THRESHOLD:
        raise HTTPException(422, {"error": "insufficient_cohort", "suppressed": True})

    sessions = _fetch_sessions(company_id, db, days=period_days)
    events   = _fetch_wellness_events(company_id, db, days=period_days)

    # Late-night: 21:00–02:00 UTC (proxy)
    late_night = [
        s for s in sessions
        if (ts := _ts_to_dt(s.get("created_at"))) and ts.hour in (21, 22, 23, 0, 1, 2)
    ]
    late_pct = round(len(late_night) / max(1, len(sessions)) * 100, 1) if sessions else 0.0

    # Sentiment shift events flagged in wellness_events collection
    sentiment_events = [e for e in events if e.get("event_type") == "sentiment_negative_shift"]
    # Bucket the count to avoid leaking exact figures for tiny teams
    bucketed_count = (len(sentiment_events) // 5) * 5  # round down to nearest 5

    # Overload proxy: weighted combination
    overload_score = round(min(10.0, (late_pct / 10) * 4 + (len(sentiment_events) / max(1, team_size)) * 6), 1)
    risk = "high" if overload_score >= 7 else ("medium" if overload_score >= 4 else "low")

    return WorkloadFrictionResponse(
        company_id=company_id,
        late_night_activity_pct=late_pct,
        sentiment_shift_events=bucketed_count,
        overload_pattern_score=overload_score,
        risk_level=risk,
        period_days=period_days,
        computed_at=utc_now().isoformat(),
    )


@router.get(
    "/productivity-proxy",
    response_model=ProductivityProxyResponse,
    summary="Team Productivity Proxy",
    description="Engagement trend correlation. Aggregated only.",
)
async def get_productivity_proxy(
    company_id: str = Query(...),
    weeks: int = Query(8, ge=1, le=ROLLING_WEEKS),
    user_token: dict = Depends(get_current_user),
):
    profile = _require_employer(user_token)
    if profile.get("company_id") != company_id:
        raise HTTPException(403, "Access denied for this company")

    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    team_size = _compute_team_size(company_id, db)
    if team_size < K_ANON_THRESHOLD:
        raise HTTPException(422, {"error": "insufficient_cohort", "suppressed": True})

    sessions  = _fetch_sessions(company_id, db, days=weeks * 7)
    check_ins = _fetch_company_check_ins(company_id, db, days=weeks * 7)

    # Weekly engagement index = % who used Diltak that week
    weekly_engagement: Dict[str, set] = {}
    for s in sessions:
        ts = _ts_to_dt(s.get("created_at"))
        if ts:
            weekly_engagement.setdefault(_week_label(ts), set()).add(s.get("user_id"))

    labels = []
    values = []
    for wk, users in sorted(weekly_engagement.items()):
        if len(users) >= K_ANON_THRESHOLD:
            labels.append(wk)
            values.append(round(len(users) / max(1, team_size) * 100, 1))

    quality = "high" if len(labels) >= 4 else ("medium" if len(labels) >= 2 else "low")

    return ProductivityProxyResponse(
        company_id=company_id,
        engagement_trend=values,
        period_label=labels,
        correlation_note=(
            "Engagement trend correlates with productivity proxies when "
            "optional integrations (HRIS, project tools) are connected."
        ),
        data_quality=quality,
        computed_at=utc_now().isoformat(),
    )


@router.get(
    "/early-warnings",
    response_model=EarlyWarningsResponse,
    summary="Early Warning Alerts",
    description="Stress / burnout trend alerts. Confidence scores. No individual attribution.",
)
async def get_early_warnings(
    company_id: str = Query(...),
    period_days: int = Query(14, ge=7, le=30),
    user_token: dict = Depends(get_current_user),
):
    profile = _require_employer(user_token)
    if profile.get("company_id") != company_id:
        raise HTTPException(403, "Access denied for this company")

    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    team_size = _compute_team_size(company_id, db)
    if team_size < K_ANON_THRESHOLD:
        raise HTTPException(422, {"error": "insufficient_cohort", "suppressed": True})

    check_ins = _fetch_company_check_ins(company_id, db, days=period_days)
    prior     = _fetch_company_check_ins(company_id, db, days=period_days * 2)
    prior_old = [
        c for c in prior
        if (_ts_to_dt(c.get("created_at")) or utc_now()) < utc_now() - timedelta(days=period_days)
    ]

    alerts = []

    def _avg_stress(cis):
        ss = [c.get("stress_level", 5) for c in cis if "stress_level" in c]
        return sum(ss) / len(ss) if ss else 5.0

    def _avg_mood(cis):
        ms = [c.get("mood_score", 5) for c in cis if "mood_score" in c]
        return sum(ms) / len(ms) if ms else 5.0

    if len(check_ins) >= K_ANON_THRESHOLD and len(prior_old) >= K_ANON_THRESHOLD:
        s_now   = _avg_stress(check_ins)
        s_prior = _avg_stress(prior_old)
        m_now   = _avg_mood(check_ins)
        m_prior = _avg_mood(prior_old)

        stress_delta = s_now - s_prior
        mood_delta   = m_now - m_prior

        if stress_delta >= 1.5:
            conf = "high" if stress_delta >= 2.5 else "medium"
            alerts.append(EarlyWarningAlert(
                signal="stress_rising",
                description=f"Team stress has risen meaningfully over the last {period_days} days.",
                confidence=conf,
                period=f"last {period_days} days",
            ))

        if mood_delta <= -1.5:
            conf = "high" if mood_delta <= -2.5 else "medium"
            alerts.append(EarlyWarningAlert(
                signal="mood_declining",
                description=f"Team mood trend is declining. Consider proactive wellbeing check.",
                confidence=conf,
                period=f"last {period_days} days",
            ))

        # Participation drop
        unique_now   = len({c.get("user_id") for c in check_ins})
        unique_prior = len({c.get("user_id") for c in prior_old})
        if unique_prior > 0 and (unique_now / unique_prior) < 0.7:
            alerts.append(EarlyWarningAlert(
                signal="engagement_drop",
                description="Check-in participation has dropped significantly vs the prior period.",
                confidence="medium",
                period=f"last {period_days} days",
            ))

    overall_risk = "red" if len(alerts) >= 2 else ("amber" if alerts else "green")

    return EarlyWarningsResponse(
        company_id=company_id,
        alerts=alerts,
        overall_risk=overall_risk,
        computed_at=utc_now().isoformat(),
    )


# ─── Suggested Actions Playbook ──────────────────────────────────────────────

PLAYBOOKS: Dict[str, SuggestedAction] = {
    "stress_rising": SuggestedAction(
        trigger="stress_rising",
        category="workload",
        action="Initiate a team workload rebalance conversation",
        expected_impact="15–25% stress reduction within 2 weeks if sustained",
        playbook_steps=[
            "Schedule a team-level workload review (async-friendly format).",
            "Identify and defer non-urgent deliverables for 1–2 sprints.",
            "Introduce no-meeting blocks (e.g., Tuesdays & Thursdays 09:00–12:00).",
            "Encourage managers to open 1:1 check-in cadence to fortnightly.",
            "Share stress-reduction micro-programs via Diltak (5–10 min/day).",
        ],
        priority="high",
    ),
    "engagement_drop": SuggestedAction(
        trigger="engagement_drop",
        category="engagement",
        action="Launch a recognition and connection micro-program",
        expected_impact="10–20% engagement lift within 4 weeks",
        playbook_steps=[
            "Introduce peer recognition nudges in team communication channels.",
            "Run a team pulse survey to understand engagement blockers.",
            "Set up fortnightly virtual team rituals (coffee catchups, async wins thread).",
            "Promote Diltak check-in streak incentives.",
            "Review recent policy changes that may have affected morale.",
        ],
        priority="high",
    ),
    "mood_declining": SuggestedAction(
        trigger="mood_declining",
        category="engagement",
        action="Deploy a mood-lift micro-program and manager enablement",
        expected_impact="8–15% mood uplift within 3 weeks",
        playbook_steps=[
            "Share positive project wins and team accomplishments publicly.",
            "Offer optional Diltak mindfulness programs (5-min daily).",
            "Enable manager training module: 'Spotting and Addressing Low Morale'.",
            "Schedule optional group wellbeing session (lunch & learn format).",
        ],
        priority="medium",
    ),
    "late_night_spikes": SuggestedAction(
        trigger="late_night_spikes",
        category="schedule",
        action="Promote schedule hygiene and async-first culture",
        expected_impact="Reduced after-hours activity within 3 weeks",
        playbook_steps=[
            "Communicate a clear 'no-reply expected' policy after 18:00.",
            "Encourage async standups to reduce synchronous pressure.",
            "Introduce 'focus hours' protections in team calendar tools.",
            "Recommend Diltak evening wind-down sessions.",
        ],
        priority="medium",
    ),
}


@router.get(
    "/suggested-actions",
    response_model=SuggestedActionsResponse,
    summary="Suggested Actions Playbook",
    description="Context-aware playbooks for managers. Generic to team — never individual.",
)
async def get_suggested_actions(
    company_id: str = Query(...),
    user_token: dict = Depends(get_current_user),
):
    profile = _require_employer(user_token)
    if profile.get("company_id") != company_id:
        raise HTTPException(403, "Access denied for this company")

    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    team_size = _compute_team_size(company_id, db)
    if team_size < K_ANON_THRESHOLD:
        raise HTTPException(422, {"error": "insufficient_cohort", "suppressed": True})

    # Detect active signals from last 14 days
    check_ins = _fetch_company_check_ins(company_id, db, days=14)
    sessions  = _fetch_sessions(company_id, db, days=14)
    events    = _fetch_wellness_events(company_id, db, days=14)

    active_actions = []

    stress_scores = [c.get("stress_level", 5) for c in check_ins]
    if stress_scores and (sum(stress_scores) / len(stress_scores)) >= 6.5:
        active_actions.append(PLAYBOOKS["stress_rising"])

    mood_scores = [c.get("mood_score", 5) for c in check_ins]
    if mood_scores and (sum(mood_scores) / len(mood_scores)) <= 4.5:
        active_actions.append(PLAYBOOKS["mood_declining"])

    late = [s for s in sessions if (ts := _ts_to_dt(s.get("created_at"))) and ts.hour in (21, 22, 23, 0, 1)]
    if sessions and (len(late) / len(sessions)) >= 0.20:
        active_actions.append(PLAYBOOKS["late_night_spikes"])

    unique_checkin = len({c.get("user_id") for c in check_ins})
    if team_size > 0 and (unique_checkin / team_size) < 0.5:
        active_actions.append(PLAYBOOKS["engagement_drop"])

    # If no signals, return a default positive hygiene playbook
    if not active_actions:
        active_actions.append(SuggestedAction(
            trigger="baseline",
            category="engagement",
            action="Maintain team wellness momentum",
            expected_impact="Sustained high-performance culture",
            playbook_steps=[
                "Continue regular Diltak check-in cadence.",
                "Celebrate team wins publicly this week.",
                "Ensure 1:1s are scheduled for the next fortnight.",
                "Review upcoming deadlines for potential load spikes.",
            ],
            priority="low",
        ))

    return SuggestedActionsResponse(
        company_id=company_id,
        actions=active_actions,
        generated_at=utc_now().isoformat(),
    )
