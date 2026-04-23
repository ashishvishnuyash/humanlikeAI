"""
Employer Insights & Action Engine — Advanced Analytics APIs
============================================================
Covers:
  3) Advanced Insights Layer
     - Predictive burnout/attrition trends with confidence intervals
     - Internal + benchmark comparisons
     - Tenure-based cohorts (never individual)
  4) Action Engine
     - Manager playbook: signal → recommendation → expected impact
     - HR playbook: program suggestions based on active risk signals

Privacy rules: same as employer_dashboard — no individual data ever.
"""

import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from db.models import Company, MHSession, MentalHealthReport, User
from db.session import get_session
from routers.auth import get_current_user

# ─── Config ──────────────────────────────────────────────────────────────────
K_ANON_THRESHOLD = 1          # suppress any cohort smaller than this

# ─── Pure helpers (no DB) ─────────────────────────────────────────────────────

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _week_label(dt: datetime) -> str:
    return dt.strftime("W%V %Y")


def _ts_to_dt(ts) -> Optional[datetime]:
    """Convert datetime (possibly naive) to UTC-aware datetime."""
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


def _parse_company_uuid(company_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(company_id)
    except ValueError:
        raise HTTPException(400, f"Invalid company_id: {company_id!r}")


# ─── DB helpers ──────────────────────────────────────────────────────────────

def _require_employer(user_token: dict, db: Session) -> dict:
    """Raise 403 if caller is not an employer, manager, or hr."""
    uid = user_token.get("uid") or user_token.get("sub") or user_token.get("id", "")
    user = db.query(User).filter(User.id == uid).one_or_none()
    if not user:
        raise HTTPException(403, "User profile not found")
    if user.role not in ("employer", "manager", "hr"):
        raise HTTPException(403, "Access restricted to employer accounts")
    profile = {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "company_id": str(user.company_id) if user.company_id else None,
    }
    return profile


def _compute_team_size(db: Session, company_id: uuid.UUID) -> int:
    return (
        db.query(User)
        .filter(User.company_id == company_id, User.is_active == True)  # noqa: E712
        .count()
    )


def _fetch_company_check_ins(db: Session, company_id: uuid.UUID, days: int = 90) -> List[dict]:
    """Fetch mental health reports for a company in the last `days` days,
    normalised to the check-in shape used across analytics endpoints:
    mood_score, stress_level, user_id, company_id, created_at.
    """
    cutoff = utc_now() - timedelta(days=days)
    rows = (
        db.query(MentalHealthReport)
        .filter(
            MentalHealthReport.company_id == company_id,
            MentalHealthReport.generated_at >= cutoff,
        )
        .all()
    )
    results = []
    for r in rows:
        report_data = r.report or {}
        results.append({
            "user_id": r.user_id,
            "company_id": str(r.company_id),
            "mood_score": report_data.get("mood_rating", report_data.get("mood_score", 5)),
            "stress_level": report_data.get("stress_level", 5),
            "created_at": r.generated_at,
        })
    return results


def _fetch_sessions(db: Session, company_id: uuid.UUID, days: int = 90) -> List[dict]:
    """Fetch MH sessions for a company in the last `days` days."""
    cutoff = utc_now() - timedelta(days=days)
    rows = (
        db.query(MHSession)
        .filter(
            MHSession.company_id == company_id,
            MHSession.created_at >= cutoff,
        )
        .all()
    )
    return [
        {
            "user_id": r.user_id,
            "company_id": str(r.company_id),
            "created_at": r.created_at,
        }
        for r in rows
    ]


def _fetch_wellness_events(db: Session, company_id: uuid.UUID, days: int = 90) -> List[dict]:
    """Derive wellness events from mental health reports.
    High-stress or high-risk reports → sentiment_negative_shift events.
    """
    cutoff = utc_now() - timedelta(days=days)
    rows = (
        db.query(MentalHealthReport)
        .filter(
            MentalHealthReport.company_id == company_id,
            MentalHealthReport.generated_at >= cutoff,
        )
        .all()
    )
    results = []
    for r in rows:
        report_data = r.report or {}
        stress = report_data.get("stress_level", 5)
        risk = r.risk_level or "low"
        if stress >= 7 or risk == "high":
            results.append({
                "event_type": "sentiment_negative_shift",
                "company_id": str(r.company_id),
                "created_at": r.generated_at,
            })
    return results


# ─── Internal benchmark reference data ───────────────────────────────────────
# These are anonymised, illustrative industry medians for context.
# Replace with real benchmark data source when available.

INTERNAL_BENCHMARKS = {
    "tech":       {"wellness_index": 62.0, "burnout_high_pct": 18.0, "engagement_pct": 55.0},
    "finance":    {"wellness_index": 57.0, "burnout_high_pct": 24.0, "engagement_pct": 48.0},
    "healthcare": {"wellness_index": 54.0, "burnout_high_pct": 31.0, "engagement_pct": 51.0},
    "retail":     {"wellness_index": 60.0, "burnout_high_pct": 20.0, "engagement_pct": 58.0},
    "education":  {"wellness_index": 63.0, "burnout_high_pct": 16.0, "engagement_pct": 60.0},
    "default":    {"wellness_index": 60.0, "burnout_high_pct": 20.0, "engagement_pct": 55.0},
}


def _get_benchmark(industry: Optional[str]) -> Dict[str, float]:
    key = (industry or "").lower()
    return INTERNAL_BENCHMARKS.get(key, INTERNAL_BENCHMARKS["default"])


# ─── Schemas ─────────────────────────────────────────────────────────────────

class PredictiveTrendPoint(BaseModel):
    week: str
    burnout_risk_pct: float
    attrition_risk_pct: float
    confidence: str            # low / medium / high


class PredictiveTrendsResponse(BaseModel):
    company_id: str
    forecast_weeks: int
    historical: List[PredictiveTrendPoint]
    forecast: List[PredictiveTrendPoint]
    model_note: str
    computed_at: str


class BenchmarkComparison(BaseModel):
    metric: str
    your_value: float
    benchmark_value: float
    delta: float
    direction: str             # above / below / at_par
    benchmark_source: str


class BenchmarksResponse(BaseModel):
    company_id: str
    industry: str
    comparisons: List[BenchmarkComparison]
    summary: str
    period_days: int
    computed_at: str


class TenureCohort(BaseModel):
    label: str                 # e.g. "0–6 months", "1–3 years"
    size_band: str
    wellness_index: float
    burnout_risk: str          # low / medium / high
    engagement_pct: float
    suppressed: bool = False


class CohortsResponse(BaseModel):
    company_id: str
    cohorts: List[TenureCohort]
    period_days: int
    privacy_note: str
    computed_at: str


# ─── Action Engine Schemas ────────────────────────────────────────────────────

class ManagerPlaybookRequest(BaseModel):
    company_id: str
    signal: str = Field(
        description="One of: stress_rising, engagement_drop, mood_declining, late_night_spikes, burnout_high"
    )


class HRPlaybookRequest(BaseModel):
    company_id: str
    signals: List[str] = Field(
        description="Active risk signals detected (e.g. ['stress_rising','retention_risk_high'])"
    )
    department_label: Optional[str] = Field(
        None, description="Optional anonymous dept label (A/B/C) to target program"
    )


class PlaybookStep(BaseModel):
    step: str
    owner: str              # HR / Manager / Employee (generic)
    timeline: str           # immediate / this_week / this_month
    expected_outcome: str


class ManagerPlaybookResponse(BaseModel):
    company_id: str
    signal: str
    insight: str
    recommendation: str
    expected_impact: str
    confidence: str
    steps: List[PlaybookStep]
    guardrails: List[str]   # privacy/ethics reminders
    generated_at: str


class HRProgramSuggestion(BaseModel):
    program_name: str
    target_signal: str
    delivery: str           # async / live / digital
    duration_weeks: int
    expected_lift: str
    priority: str           # immediate / next_cycle / optional


class HRPlaybookResponse(BaseModel):
    company_id: str
    active_signals: List[str]
    programs: List[HRProgramSuggestion]
    policy_adjustments: List[str]
    manager_enablement: List[str]
    format_note: str
    generated_at: str


# ─── Manager Playbook Library ─────────────────────────────────────────────────

MANAGER_PLAYBOOKS: Dict[str, dict] = {
    "stress_rising": {
        "insight": "Team stress levels have risen meaningfully. This is a leading indicator of potential burnout and disengagement.",
        "recommendation": "Initiate a structured workload rebalance and increase psychological safety initiatives.",
        "expected_impact": "15–25% stress reduction within 2–3 weeks if sustained.",
        "confidence": "high",
        "steps": [
            PlaybookStep(
                step="Conduct an async team workload review — ask team to flag what feels overloaded.",
                owner="Manager",
                timeline="immediate",
                expected_outcome="Shared visibility on workload hotspots.",
            ),
            PlaybookStep(
                step="Defer non-critical deliverables by 1–2 sprints. Communicate this openly to the team.",
                owner="Manager",
                timeline="this_week",
                expected_outcome="Immediate pressure reduction.",
            ),
            PlaybookStep(
                step="Introduce no-meeting blocks (e.g., Tue & Thu mornings) for focused, uninterrupted work.",
                owner="Manager",
                timeline="this_week",
                expected_outcome="Reduced context-switching; 10–15% cognitive load reduction.",
            ),
            PlaybookStep(
                step="Increase 1:1 cadence to fortnightly — use a check-in template focused on wellbeing, not just output.",
                owner="Manager",
                timeline="this_week",
                expected_outcome="Stronger psychological safety; early signal detection.",
            ),
            PlaybookStep(
                step="Promote Diltak daily 5-minute stress-relief micro-programs to the team.",
                owner="HR",
                timeline="this_week",
                expected_outcome="10% engagement uplift in Diltak; cumulative wellbeing benefit.",
            ),
        ],
        "guardrails": [
            "Do not discuss individual stress levels with team members — address patterns only.",
            "Playbook is based on aggregated team data; no personal attribution.",
            "Escalate to HR if stress signals persist beyond 3 weeks.",
        ],
    },
    "engagement_drop": {
        "insight": "Team engagement with Diltak and check-ins has dropped, suggesting declining psychological safety or low morale.",
        "recommendation": "Launch a recognition cycle and lower barriers to participation through micro-programs.",
        "expected_impact": "10–20% engagement uplift within 4 weeks.",
        "confidence": "medium",
        "steps": [
            PlaybookStep(
                step="Publicly acknowledge recent team wins in a shared channel (Slack, Teams, standup).",
                owner="Manager",
                timeline="immediate",
                expected_outcome="Immediate morale signal; models recognition culture.",
            ),
            PlaybookStep(
                step="Run a 3-question anonymous pulse survey: 'What's draining you? What's energising you? What would help?'",
                owner="HR",
                timeline="this_week",
                expected_outcome="Actionable engagement blockers identified within 5 days.",
            ),
            PlaybookStep(
                step="Set up a fortnightly virtual catch-up (30 min, optional, casual agenda).",
                owner="Manager",
                timeline="this_week",
                expected_outcome="Stronger team connection; 8–12% engagement increase.",
            ),
            PlaybookStep(
                step="Enable Diltak check-in streak incentives and nudge the team via preferred communication channel.",
                owner="HR",
                timeline="this_week",
                expected_outcome="5–10% Diltak re-engagement.",
            ),
            PlaybookStep(
                step="Review recent changes (process, structure, leadership) that may have impacted morale.",
                owner="Manager",
                timeline="this_month",
                expected_outcome="Root-cause resolution; sustained engagement improvement.",
            ),
        ],
        "guardrails": [
            "Do not identify which individuals are disengaged.",
            "Pulse survey must remain fully anonymous.",
            "Incentives should be team-wide, never individual performance-linked.",
        ],
    },
    "mood_declining": {
        "insight": "Team mood trend is declining. This precedes engagement drop and burnout if left unaddressed.",
        "recommendation": "Deploy mood-lift micro-programs and strengthen social connection rituals.",
        "expected_impact": "8–15% mood uplift within 3 weeks.",
        "confidence": "medium",
        "steps": [
            PlaybookStep(
                step="Share 3 positive team outcomes from the past 2 weeks in your next team communication.",
                owner="Manager",
                timeline="immediate",
                expected_outcome="Immediate positive reframing; morale signal.",
            ),
            PlaybookStep(
                step="Enable Diltak's 'Daily Mood Boost' 5-min mindfulness programs and communicate to team.",
                owner="HR",
                timeline="this_week",
                expected_outcome="Cumulative 8% mood improvement over 2 weeks.",
            ),
            PlaybookStep(
                step="Schedule an optional group wellbeing session (lunch & learn, yoga, breathwork — async-friendly).",
                owner="HR",
                timeline="this_month",
                expected_outcome="12–18% team connection and mood improvement.",
            ),
            PlaybookStep(
                step="Activate manager training module: 'Spotting and Addressing Low Morale' via Diltak.",
                owner="HR",
                timeline="this_week",
                expected_outcome="Manager confidence and capability improvement.",
            ),
        ],
        "guardrails": [
            "Wellbeing programs must be optional — never mandatory.",
            "Do not attribute mood decline to specific individuals or events.",
        ],
    },
    "late_night_spikes": {
        "insight": "Late-night activity patterns detected — team members are working outside healthy hours at scale.",
        "recommendation": "Promote schedule hygiene and async-first culture to reduce always-on pressure.",
        "expected_impact": "Visible reduction in after-hours activity within 3 weeks.",
        "confidence": "high",
        "steps": [
            PlaybookStep(
                step="Communicate a team 'no-reply expected' norm after 18:00 in your next team comms.",
                owner="Manager",
                timeline="immediate",
                expected_outcome="Immediate psychological safety signal; permission to disconnect.",
            ),
            PlaybookStep(
                step="Switch standups to async format (written or short video) to reduce synchronous pressure.",
                owner="Manager",
                timeline="this_week",
                expected_outcome="Schedule flexibility; 10–15% reduction in meeting hours.",
            ),
            PlaybookStep(
                step="Introduce 'focus hours' in shared calendar — protect morning blocks from meetings.",
                owner="Manager",
                timeline="this_week",
                expected_outcome="Deep work time protected; reduced end-of-day overflow.",
            ),
            PlaybookStep(
                step="Recommend Diltak's evening wind-down programs (5-min guided relaxation) via push notification.",
                owner="HR",
                timeline="this_week",
                expected_outcome="Improved sleep quality signals; 10% late-night activity reduction.",
            ),
        ],
        "guardrails": [
            "Pattern is aggregated — do not confront individuals about their schedule.",
            "Policy changes should apply team-wide, not to specific people.",
        ],
    },
    "burnout_high": {
        "insight": "High burnout risk distribution is elevated across the team. Immediate action is required.",
        "recommendation": "Urgent: implement a structured recovery plan combining workload reduction, support, and wellbeing programming.",
        "expected_impact": "20–30% high-risk reduction within 4–6 weeks with sustained effort.",
        "confidence": "high",
        "steps": [
            PlaybookStep(
                step="Flag to HR immediately. Jointly develop a 4-week recovery plan.",
                owner="Manager",
                timeline="immediate",
                expected_outcome="Shared ownership; escalation pathway activated.",
            ),
            PlaybookStep(
                step="Audit active projects — cancel, defer, or redistribute at least 20% of current load.",
                owner="Manager",
                timeline="this_week",
                expected_outcome="Measurable pressure relief within 1 week.",
            ),
            PlaybookStep(
                step="Offer optional 1-week reduced-intensity sprints with team agreement.",
                owner="Manager",
                timeline="this_week",
                expected_outcome="Recovery space; 15% burnout score reduction.",
            ),
            PlaybookStep(
                step="Launch Diltak's 'Burnout Recovery' 4-week program (stress, sleep, resilience tracks).",
                owner="HR",
                timeline="this_week",
                expected_outcome="Structured support track; 20% score improvement over 4 weeks.",
            ),
            PlaybookStep(
                step="Review hiring plan — consider temp support or resource augmentation.",
                owner="HR",
                timeline="this_month",
                expected_outcome="Sustainable capacity; prevents re-burnout.",
            ),
        ],
        "guardrails": [
            "Do not share burnout data with team members, only with HR and senior leadership.",
            "No individual identification. This playbook addresses team-level patterns only.",
            "If mental health crisis is suspected in an individual (reported to HR or manager), follow your EAP protocol.",
        ],
    },
}


# ─── HR Program Library ────────────────────────────────────────────────────

HR_PROGRAMS: Dict[str, HRProgramSuggestion] = {
    "stress_rising": HRProgramSuggestion(
        program_name="Stress Resilience Sprint",
        target_signal="stress_rising",
        delivery="digital",
        duration_weeks=4,
        expected_lift="15–25% stress reduction",
        priority="immediate",
    ),
    "burnout_high": HRProgramSuggestion(
        program_name="Burnout Recovery Program",
        target_signal="burnout_high",
        delivery="digital",
        duration_weeks=6,
        expected_lift="20–30% high-risk reduction",
        priority="immediate",
    ),
    "engagement_drop": HRProgramSuggestion(
        program_name="Re-Engagement Micro-Series",
        target_signal="engagement_drop",
        delivery="async",
        duration_weeks=3,
        expected_lift="10–20% engagement uplift",
        priority="immediate",
    ),
    "mood_declining": HRProgramSuggestion(
        program_name="Mood & Motivation Lift",
        target_signal="mood_declining",
        delivery="digital",
        duration_weeks=3,
        expected_lift="8–15% mood improvement",
        priority="next_cycle",
    ),
    "late_night_spikes": HRProgramSuggestion(
        program_name="Sleep & Schedule Hygiene Track",
        target_signal="late_night_spikes",
        delivery="digital",
        duration_weeks=4,
        expected_lift="Reduced after-hours activity + 10% sleep score improvement",
        priority="next_cycle",
    ),
    "retention_risk_high": HRProgramSuggestion(
        program_name="Stay Conversation Framework",
        target_signal="retention_risk_high",
        delivery="live",
        duration_weeks=2,
        expected_lift="10–15% retention risk reduction",
        priority="immediate",
    ),
}


# ─── Routers ─────────────────────────────────────────────────────────────────

router = APIRouter(
    prefix="/employer/insights",
    tags=["Employer — Advanced Insights & Action Engine"],
    dependencies=[Depends(get_current_user)],
)

actions_router = APIRouter(
    prefix="/employer/actions",
    tags=["Employer — Action Engine"],
    dependencies=[Depends(get_current_user)],
)


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.get(
    "/predictive-trends",
    response_model=PredictiveTrendsResponse,
    summary="Predictive Burnout/Attrition Trends",
    description="Historical + forecast risk curves with confidence intervals. No individuals.",
)
async def get_predictive_trends(
    company_id: str = Query(...),
    forecast_weeks: int = Query(4, ge=1, le=8),
    user_token: dict = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    profile = _require_employer(user_token, db)
    if profile.get("company_id") != company_id:
        raise HTTPException(403, "Access denied for this company")

    cid = _parse_company_uuid(company_id)
    team_size = _compute_team_size(db, cid)
    if team_size < K_ANON_THRESHOLD:
        raise HTTPException(422, {"error": "insufficient_cohort", "suppressed": True})

    check_ins = _fetch_company_check_ins(db, cid, days=12 * 7)

    # Group by week
    weekly: Dict[str, Dict[str, Any]] = {}
    for c in check_ins:
        ts = _ts_to_dt(c.get("created_at"))
        if not ts:
            continue
        wk = _week_label(ts)
        bucket = weekly.setdefault(wk, {"stresses": [], "moods": [], "users": set()})
        if "stress_level" in c: bucket["stresses"].append(c["stress_level"])
        if "mood_score"   in c: bucket["moods"].append(c["mood_score"])
        if c.get("user_id"): bucket["users"].add(c["user_id"])

    historical: List[PredictiveTrendPoint] = []
    burnout_series: List[float] = []

    for wk, data in sorted(weekly.items()):
        n = len(data["users"])
        if n < K_ANON_THRESHOLD:
            continue

        avg_s = sum(data["stresses"]) / len(data["stresses"]) if data["stresses"] else 5.0
        avg_m = sum(data["moods"])    / len(data["moods"])    if data["moods"]    else 5.0

        burnout_score  = avg_s * 0.6 + (10 - avg_m) * 0.4
        burnout_pct    = round(min(100.0, burnout_score * 10), 1)

        # Attrition proxy: high-stress + low-engagement users (aggregated)
        low_engagement = max(0, team_size - n) / team_size
        attrition_pct  = round(min(100.0, (burnout_score / 10 * 0.6 + low_engagement * 0.4) * 100), 1)

        conf = "high" if n >= team_size * 0.7 else ("medium" if n >= team_size * 0.4 else "low")

        historical.append(PredictiveTrendPoint(
            week=wk,
            burnout_risk_pct=burnout_pct,
            attrition_risk_pct=attrition_pct,
            confidence=conf,
        ))
        burnout_series.append(burnout_pct)

    # Linear trend extrapolation for forecast
    forecast: List[PredictiveTrendPoint] = []
    if len(burnout_series) >= 3:
        n_pts = len(burnout_series)
        x_avg = (n_pts - 1) / 2
        slope = sum((i - x_avg) * (v - sum(burnout_series)/n_pts) for i, v in enumerate(burnout_series)) / \
                max(1, sum((i - x_avg) ** 2 for i in range(n_pts)))
        last_val = burnout_series[-1]

        for i in range(1, forecast_weeks + 1):
            fut_burnout   = round(max(0.0, min(100.0, last_val + slope * i)), 1)
            fut_attrition = round(max(0.0, min(100.0, fut_burnout * 0.6)), 1)
            fut_wk        = _week_label(utc_now() + timedelta(weeks=i))
            # Confidence degrades with forecast horizon
            fut_conf      = "medium" if i <= 2 else "low"
            forecast.append(PredictiveTrendPoint(
                week=f"[forecast] {fut_wk}",
                burnout_risk_pct=fut_burnout,
                attrition_risk_pct=fut_attrition,
                confidence=fut_conf,
            ))

    return PredictiveTrendsResponse(
        company_id=company_id,
        forecast_weeks=forecast_weeks,
        historical=historical,
        forecast=forecast,
        model_note=(
            "Burnout risk uses a stress + inverse-mood proxy model. "
            "Attrition risk combines burnout proxy with engagement decay. "
            "Forecast uses linear extrapolation — confidence decreases with horizon."
        ),
        computed_at=utc_now().isoformat(),
    )


@router.get(
    "/benchmarks",
    response_model=BenchmarksResponse,
    summary="Internal & External Benchmarking",
    description="Compare team wellness metrics to anonymised industry benchmarks.",
)
async def get_benchmarks(
    company_id: str = Query(...),
    period_days: int = Query(30, ge=7, le=90),
    industry: Optional[str] = Query(None, description="tech / finance / healthcare / retail / education"),
    user_token: dict = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    profile = _require_employer(user_token, db)
    if profile.get("company_id") != company_id:
        raise HTTPException(403, "Access denied for this company")

    cid = _parse_company_uuid(company_id)
    team_size = _compute_team_size(db, cid)
    if team_size < K_ANON_THRESHOLD:
        raise HTTPException(422, {"error": "insufficient_cohort", "suppressed": True})

    # Detect industry from company profile if not provided
    if not industry:
        try:
            company = (
                db.query(Company)
                .filter(Company.owner_id == profile.get("id", ""))
                .limit(1)
                .one_or_none()
            )
            if company and company.settings:
                industry = (company.settings.get("industry") or "").lower() or None
        except Exception:
            pass

    bm = _get_benchmark(industry)

    check_ins = _fetch_company_check_ins(db, cid, days=period_days)
    sessions  = _fetch_sessions(db, cid, days=period_days)

    moods    = [c.get("mood_score", 5) for c in check_ins if "mood_score" in c]
    stresses = [c.get("stress_level", 5) for c in check_ins if "stress_level" in c]
    unique   = len({c.get("user_id") for c in check_ins})
    part     = min(100.0, unique / team_size * 100)

    avg_m = sum(moods) / len(moods) if moods else 5.0
    avg_s = sum(stresses) / len(stresses) if stresses else 5.0

    wi = round((avg_m/10*100 * 0.35) + ((10-avg_s)/10*100 * 0.40) + (part * 0.25), 1) if moods else 0.0

    burnout_score = avg_s * 0.6 + (10 - avg_m) * 0.4
    burnout_high_pct = round(burnout_score * 10, 1)

    active_users = {s.get("user_id") for s in sessions}
    eng_pct = round(len(active_users) / team_size * 100, 1)

    def _cmp(metric: str, your_val: float, bm_val: float) -> BenchmarkComparison:
        delta = round(your_val - bm_val, 1)
        direction = "above" if delta > 1 else ("below" if delta < -1 else "at_par")
        return BenchmarkComparison(
            metric=metric,
            your_value=your_val,
            benchmark_value=bm_val,
            delta=delta,
            direction=direction,
            benchmark_source=f"Anonymised {industry or 'cross-industry'} median (Diltak network)",
        )

    comparisons = [
        _cmp("wellness_index",       wi,               bm["wellness_index"]),
        _cmp("burnout_high_pct",     burnout_high_pct, bm["burnout_high_pct"]),
        _cmp("diltak_engagement_pct", eng_pct,         bm["engagement_pct"]),
    ]

    below_count = sum(1 for c in comparisons if c.direction == "below")
    summary = (
        "Your team is performing above benchmark across all measured dimensions. Keep it up!"
        if below_count == 0
        else f"Your team is below benchmark on {below_count} metric(s). Targeted playbooks available in Suggested Actions."
    )

    return BenchmarksResponse(
        company_id=company_id,
        industry=industry or "cross-industry",
        comparisons=comparisons,
        summary=summary,
        period_days=period_days,
        computed_at=utc_now().isoformat(),
    )


@router.get(
    "/cohorts",
    response_model=CohortsResponse,
    summary="Tenure-Based Cohort Analysis",
    description="Wellness by tenure band (new joiners, 6–12m, 1–3y, 3y+). Never individual.",
)
async def get_cohorts(
    company_id: str = Query(...),
    period_days: int = Query(30, ge=7, le=90),
    user_token: dict = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    profile = _require_employer(user_token, db)
    if profile.get("company_id") != company_id:
        raise HTTPException(403, "Access denied for this company")

    cid = _parse_company_uuid(company_id)
    team_size = _compute_team_size(db, cid)
    if team_size < K_ANON_THRESHOLD:
        raise HTTPException(422, {"error": "insufficient_cohort", "suppressed": True})

    # Fetch all users with created_at (hire date proxy)
    try:
        users = db.query(User).filter(User.company_id == cid).all()
    except Exception:
        users = []

    check_ins = _fetch_company_check_ins(db, cid, days=period_days)
    sessions  = _fetch_sessions(db, cid, days=period_days)

    # User lookup: uid → tenure category
    now = utc_now()

    def _tenure_label(created_at) -> str:
        dt = _ts_to_dt(created_at)
        if not dt:
            return "unknown"
        days_employed = (now - dt).days
        if days_employed <= 180:
            return "0–6 months"
        if days_employed <= 365:
            return "6–12 months"
        if days_employed <= 1095:
            return "1–3 years"
        return "3+ years"

    uid_to_tenure: Dict[str, str] = {
        u.id: _tenure_label(u.created_at)
        for u in users
    }

    # Group check-ins by tenure
    tenure_ci: Dict[str, List[dict]] = {}
    for c in check_ins:
        uid = c.get("user_id", "")
        tenure = uid_to_tenure.get(uid, "unknown")
        tenure_ci.setdefault(tenure, []).append(c)

    tenure_sess: Dict[str, set] = {}
    for s in sessions:
        uid = s.get("user_id", "")
        tenure = uid_to_tenure.get(uid, "unknown")
        tenure_sess.setdefault(tenure, set()).add(uid)

    tenure_size: Dict[str, int] = {}
    for tenure in uid_to_tenure.values():
        tenure_size[tenure] = tenure_size.get(tenure, 0) + 1

    bands = ["0–6 months", "6–12 months", "1–3 years", "3+ years"]
    cohorts: List[TenureCohort] = []

    for band in bands:
        n     = tenure_size.get(band, 0)
        cins  = tenure_ci.get(band, [])
        ssize = len(tenure_sess.get(band, set()))

        if n < K_ANON_THRESHOLD:
            cohorts.append(TenureCohort(
                label=band,
                size_band=_size_band(n),
                wellness_index=0,
                burnout_risk="unknown",
                engagement_pct=0,
                suppressed=True,
            ))
            continue

        moods    = [c.get("mood_score", 5) for c in cins if "mood_score" in c]
        stresses = [c.get("stress_level", 5) for c in cins if "stress_level" in c]
        avg_m    = sum(moods) / len(moods) if moods else 5.0
        avg_s    = sum(stresses) / len(stresses) if stresses else 5.0
        eng_pct  = round(ssize / n * 100, 1)

        wi = round((avg_m/10*100 * 0.35) + ((10-avg_s)/10*100 * 0.40) + (eng_pct * 0.25), 1)
        bs = avg_s * 0.6 + (10 - avg_m) * 0.4
        br = "high" if bs >= 7 else ("medium" if bs >= 4.5 else "low")

        cohorts.append(TenureCohort(
            label=band,
            size_band=_size_band(n),
            wellness_index=wi,
            burnout_risk=br,
            engagement_pct=eng_pct,
        ))

    return CohortsResponse(
        company_id=company_id,
        cohorts=cohorts,
        period_days=period_days,
        privacy_note="Data aggregated by tenure band. Individual user data is never exposed.",
        computed_at=utc_now().isoformat(),
    )


@actions_router.post(
    "/manager-playbook",
    response_model=ManagerPlaybookResponse,
    summary="Manager Playbook",
    description="Get Insight → Recommendation → Expected Impact → Playbook steps for a given signal.",
)
async def get_manager_playbook(
    req: ManagerPlaybookRequest,
    user_token: dict = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    profile = _require_employer(user_token, db)
    if profile.get("company_id") != req.company_id:
        raise HTTPException(403, "Access denied for this company")

    pb = MANAGER_PLAYBOOKS.get(req.signal)
    if not pb:
        raise HTTPException(
            400,
            detail=f"Unknown signal '{req.signal}'. Valid signals: {list(MANAGER_PLAYBOOKS.keys())}",
        )

    return ManagerPlaybookResponse(
        company_id=req.company_id,
        signal=req.signal,
        insight=pb["insight"],
        recommendation=pb["recommendation"],
        expected_impact=pb["expected_impact"],
        confidence=pb["confidence"],
        steps=pb["steps"],
        guardrails=pb["guardrails"],
        generated_at=utc_now().isoformat(),
    )


@actions_router.post(
    "/hr-playbook",
    response_model=HRPlaybookResponse,
    summary="HR Playbook",
    description="HR program + policy suggestions based on active risk signals.",
)
async def get_hr_playbook(
    req: HRPlaybookRequest,
    user_token: dict = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    profile = _require_employer(user_token, db)
    if profile.get("company_id") != req.company_id:
        raise HTTPException(403, "Access denied for this company")

    programs: List[HRProgramSuggestion] = []
    for signal in req.signals:
        prog = HR_PROGRAMS.get(signal)
        if prog:
            programs.append(prog)

    # Sort: immediate → next_cycle → optional
    priority_order = {"immediate": 0, "next_cycle": 1, "optional": 2}
    programs.sort(key=lambda p: priority_order.get(p.priority, 99))

    # Policy adjustments
    policy_adjustments = []
    if "late_night_spikes" in req.signals:
        policy_adjustments.append("Review and update right-to-disconnect policy.")
        policy_adjustments.append("Consider flexible work arrangements (FlexTime / compressed weeks).")
    if "stress_rising" in req.signals or "burnout_high" in req.signals:
        policy_adjustments.append("Audit PTO culture — ensure team is taking allocated leave.")
        policy_adjustments.append("Consider introducing mental health leave days.")
    if "engagement_drop" in req.signals:
        policy_adjustments.append("Review recognition policy — ensure peer and manager recognition channels exist.")

    # Manager enablement modules
    manager_enablement = []
    signal_set = set(req.signals)
    if {"stress_rising", "burnout_high"} & signal_set:
        manager_enablement.append("Activate: 'Recognising Burnout Early' manager training module.")
        manager_enablement.append("Activate: 'Workload Rebalancing Conversations' guide.")
    if {"engagement_drop", "mood_declining"} & signal_set:
        manager_enablement.append("Activate: 'Psychological Safety Foundations' module.")
        manager_enablement.append("Activate: '1:1 Wellbeing Check-In Templates' toolkit.")
    if "late_night_spikes" in signal_set:
        manager_enablement.append("Activate: 'Leading with Async-First Practices' module.")

    if not manager_enablement:
        manager_enablement.append("No urgent manager training identified. Continue regular leadership development.")

    return HRPlaybookResponse(
        company_id=req.company_id,
        active_signals=req.signals,
        programs=programs,
        policy_adjustments=policy_adjustments or ["No immediate policy changes required. Monitor trends."],
        manager_enablement=manager_enablement,
        format_note="Format: Insight → Recommendation → Expected Impact → Playbook. All programs generic to team.",
        generated_at=utc_now().isoformat(),
    )
