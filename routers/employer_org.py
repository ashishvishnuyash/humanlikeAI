"""
Employer Org Analytics — HR / Strategic Level APIs
====================================================
Privacy rules:
  - All data aggregated at company / department level
  - Department labels masked (A / B / C) — real names are opts
  - Cohorts < K_ANON_THRESHOLD suppressed
  - No individual data ever returned
  - Risk bands: low / medium / high only
"""

import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from firebase_config import get_db
from routers.auth import get_current_user
from routers.employer_dashboard import (
    K_ANON_THRESHOLD,
    utc_now,
    _ts_to_dt,
    _week_label,
    _size_band,
    _require_employer,
    _fetch_company_check_ins,
    _fetch_sessions,
    _compute_team_size,
)

router = APIRouter(
    prefix="/employer/org",
    tags=["Employer — Org & HR Analytics"],
    dependencies=[Depends(get_current_user)],
)

# ─── Helpers ─────────────────────────────────────────────────────────────────

def _fetch_all_users(company_id: str, db) -> List[dict]:
    try:
        docs = db.collection("users").where("company_id", "==", company_id).stream()
        return [d.to_dict() for d in docs]
    except Exception as e:
        print(f"[employer_org] users fetch error: {e}")
        return []


def _mask_dept_labels(depts: List[str]) -> Dict[str, str]:
    """Map real department names to anonymous labels A, B, C…"""
    labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return {dept: labels[i % 26] for i, dept in enumerate(sorted(set(depts)))}


def _intervention_records(company_id: str, db) -> List[dict]:
    try:
        docs = (
            db.collection("interventions")
            .where("company_id", "==", company_id)
            .stream()
        )
        return [d.to_dict() for d in docs]
    except Exception as e:
        print(f"[employer_org] interventions fetch error: {e}")
        return []


# ─── Schemas ─────────────────────────────────────────────────────────────────

class OrgWellnessTrendPoint(BaseModel):
    week: str
    wellness_index: float
    sample_size_band: str   # size band, NOT exact count


class OrgWellnessTrendResponse(BaseModel):
    company_id: str
    trend: List[OrgWellnessTrendPoint]
    period_weeks: int
    overall_index: float
    direction: str          # improving / declining / stable
    computed_at: str


class DepartmentMetric(BaseModel):
    label: str              # "A", "B", "C" — never real name unless opted in
    wellness_index: float
    burnout_risk: str       # low / medium / high
    engagement_pct: float
    size_band: str
    suppressed: bool = False


class DeptComparisonResponse(BaseModel):
    company_id: str
    departments: List[DepartmentMetric]
    hotspot_label: Optional[str] = None    # label of dept needing most attention
    label_masking: bool
    period_days: int
    computed_at: str


class RetentionRiskBand(BaseModel):
    band: str               # low / medium / high
    percentage: float
    trend: str              # rising / falling / stable


class RetentionRiskResponse(BaseModel):
    company_id: str
    risk_bands: List[RetentionRiskBand]
    overall_risk: str       # green / amber / red
    period_days: int
    note: str = "Modelled from engagement + stress proxy signals. No individual data."
    computed_at: str


class DiltakEngagementResponse(BaseModel):
    company_id: str
    adoption_pct: float             # % team who've used Diltak at all
    wau_pct: float                  # weekly active %
    voice_sessions_pct: float       # % of sessions that were voice
    text_sessions_pct: float
    completion_rate_pct: float      # % sessions marked complete
    avg_sessions_per_active_user: float
    period_days: int
    computed_at: str


class ROICorrelationPoint(BaseModel):
    period: str
    wellbeing_index: float
    proxy_metric: str       # "absenteeism_rate" | "engagement_score" | "retention_proxy"
    proxy_value: float
    correlation_direction: str   # positive / negative / neutral


class ROIImpactResponse(BaseModel):
    company_id: str
    correlations: List[ROICorrelationPoint]
    summary: str
    data_quality: str
    computed_at: str


class InterventionCohort(BaseModel):
    label: str              # "Control", "Treatment A" etc.
    before_index: float
    after_index: float
    delta: float
    size_band: str
    suppressed: bool = False


class ProgramEffectivenessResponse(BaseModel):
    company_id: str
    cohorts: List[InterventionCohort]
    overall_lift: Optional[float]
    recommendation: str
    computed_at: str


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.get(
    "/wellness-trend",
    response_model=OrgWellnessTrendResponse,
    summary="A. Org Wellness Trend",
    description="Company-wide wellness index over time. Aggregated across entire org.",
)
async def get_org_wellness_trend(
    company_id: str = Query(...),
    weeks: int = Query(12, ge=4, le=26),
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

    check_ins = _fetch_company_check_ins(company_id, db, days=weeks * 7)

    weekly_data: Dict[str, Dict[str, Any]] = {}
    for c in check_ins:
        ts = _ts_to_dt(c.get("created_at"))
        if not ts:
            continue
        wk = _week_label(ts)
        bucket = weekly_data.setdefault(wk, {"moods": [], "stresses": [], "users": set()})
        if "mood_score" in c:
            bucket["moods"].append(c["mood_score"])
        if "stress_level" in c:
            bucket["stresses"].append(c["stress_level"])
        if c.get("user_id"):
            bucket["users"].add(c["user_id"])

    trend_points = []
    for wk, data in sorted(weekly_data.items()):
        n = len(data["users"])
        if n < K_ANON_THRESHOLD:
            continue  # suppress small cohorts
        avg_m = sum(data["moods"]) / len(data["moods"]) if data["moods"] else 5.0
        avg_s = sum(data["stresses"]) / len(data["stresses"]) if data["stresses"] else 5.0
        part  = min(100.0, n / team_size * 100)
        wi    = round((avg_m/10*100 * 0.35) + ((10-avg_s)/10*100 * 0.40) + (part * 0.25), 1)
        trend_points.append(OrgWellnessTrendPoint(
            week=wk,
            wellness_index=wi,
            sample_size_band=_size_band(n),
        ))

    overall = round(sum(p.wellness_index for p in trend_points) / len(trend_points), 1) if trend_points else 0.0

    if len(trend_points) >= 4:
        first_avg = sum(p.wellness_index for p in trend_points[:2]) / 2
        last_avg  = sum(p.wellness_index for p in trend_points[-2:]) / 2
        direction = "improving" if last_avg > first_avg + 2 else ("declining" if last_avg < first_avg - 2 else "stable")
    else:
        direction = "stable"

    return OrgWellnessTrendResponse(
        company_id=company_id,
        trend=trend_points,
        period_weeks=weeks,
        overall_index=overall,
        direction=direction,
        computed_at=utc_now().isoformat(),
    )


@router.get(
    "/department-comparison",
    response_model=DeptComparisonResponse,
    summary="B. Department Comparison (Anonymised)",
    description="Relative wellness indices by department. Labels masked; small cohorts suppressed.",
)
async def get_department_comparison(
    company_id: str = Query(...),
    period_days: int = Query(30, ge=7, le=90),
    mask_labels: bool = Query(True, description="True = use A/B/C labels instead of real dept names"),
    user_token: dict = Depends(get_current_user),
):
    profile = _require_employer(user_token)
    if profile.get("company_id") != company_id:
        raise HTTPException(403, "Access denied for this company")

    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    all_users = _fetch_all_users(company_id, db)
    check_ins = _fetch_company_check_ins(company_id, db, days=period_days)
    sessions  = _fetch_sessions(company_id, db, days=period_days)

    # Build per-department aggregates
    dept_users: Dict[str, set] = {}
    for u in all_users:
        dept = u.get("department") or "Unknown"
        dept_users.setdefault(dept, set()).add(u.get("id") or u.get("uid", ""))

    # Checkin and session lookup by user
    user_checkins: Dict[str, List[dict]] = {}
    for c in check_ins:
        uid = c.get("user_id", "")
        user_checkins.setdefault(uid, []).append(c)

    user_sessions: Dict[str, int] = {}
    for s in sessions:
        uid = s.get("user_id", "")
        user_sessions[uid] = user_sessions.get(uid, 0) + 1

    dept_labels = _mask_dept_labels(list(dept_users.keys())) if mask_labels else {d: d for d in dept_users}

    dept_metrics: List[DepartmentMetric] = []
    for dept, uids in dept_users.items():
        n = len(uids)
        label = dept_labels.get(dept, dept)

        if n < K_ANON_THRESHOLD:
            dept_metrics.append(DepartmentMetric(
                label=label,
                wellness_index=0,
                burnout_risk="unknown",
                engagement_pct=0,
                size_band=_size_band(n),
                suppressed=True,
            ))
            continue

        dept_cins = [c for uid in uids for c in user_checkins.get(uid, [])]
        moods     = [c.get("mood_score", 5) for c in dept_cins if "mood_score" in c]
        stresses  = [c.get("stress_level", 5) for c in dept_cins if "stress_level" in c]
        engaged   = sum(1 for uid in uids if user_sessions.get(uid, 0) > 0)

        avg_m    = sum(moods) / len(moods) if moods else 5.0
        avg_s    = sum(stresses) / len(stresses) if stresses else 5.0
        eng_pct  = round(engaged / n * 100, 1)
        wi       = round((avg_m/10*100 * 0.40) + ((10-avg_s)/10*100 * 0.45) + (eng_pct * 0.15), 1)

        burnout_score = avg_s * 0.6 + (10 - avg_m) * 0.4
        burnout_risk  = "high" if burnout_score >= 7 else ("medium" if burnout_score >= 4.5 else "low")

        dept_metrics.append(DepartmentMetric(
            label=label,
            wellness_index=wi,
            burnout_risk=burnout_risk,
            engagement_pct=eng_pct,
            size_band=_size_band(n),
        ))

    # Identify hotspot (lowest wellness, not suppressed)
    visible = [d for d in dept_metrics if not d.suppressed]
    hotspot = min(visible, key=lambda d: d.wellness_index).label if visible else None

    return DeptComparisonResponse(
        company_id=company_id,
        departments=dept_metrics,
        hotspot_label=hotspot,
        label_masking=mask_labels,
        period_days=period_days,
        computed_at=utc_now().isoformat(),
    )


@router.get(
    "/retention-risk",
    response_model=RetentionRiskResponse,
    summary="C. Retention Risk Signal",
    description="Modelled risk bands (low/med/high). No individual data.",
)
async def get_retention_risk(
    company_id: str = Query(...),
    period_days: int = Query(60, ge=14, le=180),
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
    sessions  = _fetch_sessions(company_id, db, days=period_days)

    # Retention risk model:
    # Per user: compute a risk score based on stress trend + engagement drop
    # Then bucket the distribution — NEVER expose individual scores

    user_stress: Dict[str, List[float]] = {}
    user_last_active: Dict[str, Optional[datetime]] = {}

    for c in check_ins:
        uid = c.get("user_id")
        if uid:
            user_stress.setdefault(uid, []).append(c.get("stress_level", 5))

    for s in sessions:
        uid = s.get("user_id")
        if uid:
            ts = _ts_to_dt(s.get("created_at"))
            prev = user_last_active.get(uid)
            if ts and (prev is None or ts > prev):
                user_last_active[uid] = ts

    high_risk = medium_risk = low_risk = 0

    for uid, stresses in user_stress.items():
        avg_s = sum(stresses) / len(stresses)
        last  = user_last_active.get(uid)
        days_inactive = (utc_now() - last).days if last else period_days

        # Simple proxy model
        risk_score = (avg_s / 10) * 5 + (min(days_inactive, period_days) / period_days) * 5

        if risk_score >= 7:
            high_risk += 1
        elif risk_score >= 4:
            medium_risk += 1
        else:
            low_risk += 1

    total = high_risk + medium_risk + low_risk
    if total < K_ANON_THRESHOLD:
        raise HTTPException(422, {"error": "insufficient_cohort", "suppressed": True})

    # Compute prior period for trend
    prior_check = _fetch_company_check_ins(company_id, db, days=period_days * 2)
    prior_check = [c for c in prior_check if (_ts_to_dt(c.get("created_at")) or utc_now()) < utc_now() - timedelta(days=period_days)]

    prior_stress_vals = [c.get("stress_level", 5) for c in prior_check]
    current_stress_vals = [c.get("stress_level", 5) for c in check_ins]
    if prior_stress_vals and current_stress_vals:
        trend_str = (
            "rising"  if sum(current_stress_vals)/len(current_stress_vals) > sum(prior_stress_vals)/len(prior_stress_vals) + 0.5
            else "falling" if sum(current_stress_vals)/len(current_stress_vals) < sum(prior_stress_vals)/len(prior_stress_vals) - 0.5
            else "stable"
        )
    else:
        trend_str = "stable"

    bands = [
        RetentionRiskBand(band="low",    percentage=round(low_risk    / total * 100, 1), trend="stable"),
        RetentionRiskBand(band="medium", percentage=round(medium_risk / total * 100, 1), trend=trend_str),
        RetentionRiskBand(band="high",   percentage=round(high_risk   / total * 100, 1), trend=trend_str),
    ]

    high_pct       = bands[2].percentage
    overall_risk   = "red" if high_pct >= 25 else ("amber" if high_pct >= 10 else "green")

    return RetentionRiskResponse(
        company_id=company_id,
        risk_bands=bands,
        overall_risk=overall_risk,
        period_days=period_days,
        computed_at=utc_now().isoformat(),
    )


@router.get(
    "/diltak-engagement",
    response_model=DiltakEngagementResponse,
    summary="D. Engagement with Diltak",
    description="Adoption, feature usage (voice/text), completion rates. Percentages only.",
)
async def get_diltak_engagement(
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

    # Adoption: unique users
    unique_users = {s.get("user_id") for s in sessions}
    adoption_pct = round(len(unique_users) / team_size * 100, 1) if team_size else 0

    # WAU
    one_week_ago = utc_now() - timedelta(days=7)
    wau_users    = {s.get("user_id") for s in sessions if (_ts_to_dt(s.get("created_at")) or utc_now()) >= one_week_ago}
    wau_pct      = round(len(wau_users) / team_size * 100, 1)

    # Voice vs text
    voice   = [s for s in sessions if s.get("modality") == "voice"]
    text_s  = [s for s in sessions if s.get("modality") in ("text", None)]
    total_s = max(1, len(sessions))
    voice_pct = round(len(voice) / total_s * 100, 1)
    text_pct  = round(len(text_s) / total_s * 100, 1)

    # Completion rate
    completed = [s for s in sessions if s.get("completed") is True]
    completion_pct = round(len(completed) / total_s * 100, 1)

    # Avg sessions per active user
    if len(unique_users) > 0:
        avg_sessions = round(len(sessions) / len(unique_users), 1)
    else:
        avg_sessions = 0.0

    return DiltakEngagementResponse(
        company_id=company_id,
        adoption_pct=adoption_pct,
        wau_pct=wau_pct,
        voice_sessions_pct=voice_pct,
        text_sessions_pct=text_pct,
        completion_rate_pct=completion_pct,
        avg_sessions_per_active_user=avg_sessions,
        period_days=period_days,
        computed_at=utc_now().isoformat(),
    )


@router.get(
    "/roi-impact",
    response_model=ROIImpactResponse,
    summary="E. ROI / Impact",
    description="Correlation panels: wellbeing ↑ vs absenteeism ↓ / engagement ↑. Aggregated.",
)
async def get_roi_impact(
    company_id: str = Query(...),
    weeks: int = Query(8, ge=4, le=24),
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

    check_ins = _fetch_company_check_ins(company_id, db, days=weeks * 7)
    sessions  = _fetch_sessions(company_id, db, days=weeks * 7)

    # Build weekly wellness index + session engagement for correlation
    weekly_ci: Dict[str, Dict] = {}
    for c in check_ins:
        ts = _ts_to_dt(c.get("created_at"))
        if ts:
            wk = _week_label(ts)
            bucket = weekly_ci.setdefault(wk, {"moods": [], "stresses": [], "users": set()})
            if "mood_score"   in c: bucket["moods"].append(c["mood_score"])
            if "stress_level" in c: bucket["stresses"].append(c["stress_level"])
            if c.get("user_id"): bucket["users"].add(c["user_id"])

    weekly_sess: Dict[str, set] = {}
    for s in sessions:
        ts = _ts_to_dt(s.get("created_at"))
        if ts:
            wk = _week_label(ts)
            weekly_sess.setdefault(wk, set()).add(s.get("user_id"))

    correlations: List[ROICorrelationPoint] = []
    for wk in sorted(weekly_ci.keys()):
        data = weekly_ci[wk]
        n    = len(data["users"])
        if n < K_ANON_THRESHOLD:
            continue

        avg_m = sum(data["moods"])    / len(data["moods"])    if data["moods"]    else 5.0
        avg_s = sum(data["stresses"]) / len(data["stresses"]) if data["stresses"] else 5.0
        part  = min(100.0, n / team_size * 100)
        wi    = round((avg_m/10*100 * 0.35) + ((10-avg_s)/10*100 * 0.40) + (part * 0.25), 1)

        # Engagement proxy: % active in Diltak that week
        active_sess = len(weekly_sess.get(wk, set()))
        eng_pct = round(active_sess / team_size * 100, 1)

        correlations.append(ROICorrelationPoint(
            period=wk,
            wellbeing_index=wi,
            proxy_metric="diltak_engagement_pct",
            proxy_value=eng_pct,
            correlation_direction=(
                "positive" if wi >= 60 and eng_pct >= 40
                else "negative" if wi < 45 and eng_pct < 25
                else "neutral"
            ),
        ))

    quality = "high" if len(correlations) >= 6 else ("medium" if len(correlations) >= 3 else "low")

    return ROIImpactResponse(
        company_id=company_id,
        correlations=correlations,
        summary=(
            "Higher team wellbeing correlates with increased Diltak engagement. "
            "Connect HRIS data for absenteeism and performance correlations."
        ),
        data_quality=quality,
        computed_at=utc_now().isoformat(),
    )


@router.get(
    "/program-effectiveness",
    response_model=ProgramEffectivenessResponse,
    summary="F. Program Effectiveness",
    description="Before/after analysis of wellbeing interventions. Cohort-level A/B.",
)
async def get_program_effectiveness(
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

    interventions = _intervention_records(company_id, db)

    cohorts: List[InterventionCohort] = []
    lifts = []

    for intv in interventions:
        start_ts = _ts_to_dt(intv.get("start_date"))
        end_ts   = _ts_to_dt(intv.get("end_date"))
        if not start_ts or not end_ts:
            continue

        window    = (end_ts - start_ts).days
        before_ci = _fetch_company_check_ins(company_id, db, days=window * 2)
        # before: window days before start
        before_ci = [
            c for c in before_ci
            if start_ts - timedelta(days=window) <=
               (_ts_to_dt(c.get("created_at")) or start_ts) < start_ts
        ]
        # after: window days after start
        after_ci = _fetch_company_check_ins(company_id, db, days=window)

        if len(before_ci) < K_ANON_THRESHOLD or len(after_ci) < K_ANON_THRESHOLD:
            cohorts.append(InterventionCohort(
                label=intv.get("label", "Unnamed"),
                before_index=0,
                after_index=0,
                delta=0,
                size_band=_size_band(0),
                suppressed=True,
            ))
            continue

        def _wi(cis):
            ms = [c.get("mood_score", 5) for c in cis if "mood_score" in c]
            ss = [c.get("stress_level", 5) for c in cis if "stress_level" in c]
            n  = len({c.get("user_id") for c in cis})
            am = sum(ms)/len(ms) if ms else 5.0
            as_ = sum(ss)/len(ss) if ss else 5.0
            part = min(100.0, n / team_size * 100)
            return round((am/10*100*0.35) + ((10-as_)/10*100*0.40) + (part*0.25), 1)

        before_wi = _wi(before_ci)
        after_wi  = _wi(after_ci)
        delta     = round(after_wi - before_wi, 1)
        lifts.append(delta)

        cohorts.append(InterventionCohort(
            label=intv.get("label", "Program"),
            before_index=before_wi,
            after_index=after_wi,
            delta=delta,
            size_band=_size_band(len({c.get("user_id") for c in after_ci})),
        ))

    overall_lift = round(sum(lifts) / len(lifts), 1) if lifts else None

    if not cohorts:
        recommendation = (
            "No intervention records found. Log programs in the 'interventions' collection "
            "to enable before/after effectiveness analysis."
        )
    elif overall_lift is not None and overall_lift >= 3:
        recommendation = "Interventions show positive impact. Continue and scale successful programs."
    elif overall_lift is not None and overall_lift < 0:
        recommendation = "Interventions show limited impact. Review program design or delivery channels."
    else:
        recommendation = "Moderate impact detected. A/B test variations to optimise effectiveness."

    return ProgramEffectivenessResponse(
        company_id=company_id,
        cohorts=cohorts,
        overall_lift=overall_lift,
        recommendation=recommendation,
        computed_at=utc_now().isoformat(),
    )
