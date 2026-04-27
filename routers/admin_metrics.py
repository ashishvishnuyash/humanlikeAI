"""
Admin Metrics Router — Super Admin Analytics & Observability
=============================================================

Phase A — Overview + Company + User Detail
-------------------------------------------
  GET /api/admin/overview                     → Platform KPIs (users, credits, usage)
  GET /api/admin/companies                    → All companies with credit + usage summary
  GET /api/admin/companies/{company_id}       → Single company detail + employee list + credit
  GET /api/admin/users/{uid}                  → User profile + usage summary

Phase B — Usage & Credits
--------------------------
  GET /api/admin/usage                        → Aggregated token/cost usage (filterable)
  GET /api/admin/credits                      → All company credit balances

Phase C — Audit Log
---------------------
  GET /api/admin/audit-log                    → Paginated audit trail (filterable)

Phase D — Gamification Admin
-----------------------------
  GET  /api/admin/gamification/overview                → Platform-wide gamification health
  GET  /api/admin/gamification/companies/{company_id}  → Per-company gamification breakdown
  POST /api/admin/challenges                           → Create admin-managed challenge
  GET  /api/admin/challenges                           → List all challenges
  PATCH /api/admin/challenges/{challenge_id}           → Edit / toggle active state
  GET  /api/admin/challenges/{challenge_id}/stats      → Participation + completion stats

All endpoints require super_admin role.
"""

import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from firebase_config import get_db
from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from pydantic import BaseModel

from routers.auth import get_super_admin_user

router = APIRouter(prefix="/admin", tags=["Admin Metrics"])


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _ts_to_iso(ts) -> Optional[str]:
    if ts is None:
        return None
    try:
        if hasattr(ts, "timestamp"):
            return datetime.fromtimestamp(ts.timestamp(), tz=timezone.utc).isoformat()
        if isinstance(ts, datetime):
            return ts.isoformat()
    except Exception:
        pass
    return str(ts)


def _safe_float(val, default: float = 0.0) -> float:
    try:
        return round(float(val), 6)
    except (TypeError, ValueError):
        return default


def _safe_int(val, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


# ─── Schemas ──────────────────────────────────────────────────────────────────

class PlatformOverview(BaseModel):
    totalCompanies:       int
    totalEmployers:       int
    totalEmployees:       int
    activeUsers:          int
    inactiveUsers:        int
    newUsersLast30d:      int
    totalCreditsConsumed: float     # sum across all companies (MTD)
    totalLifetimeSpend:   float
    companiesAtWarning:   int       # alert_status == "warning"
    companiesAtCritical:  int       # alert_status in ("critical", "limit_reached")
    computedAt:           str


class CompanySummary(BaseModel):
    id:                   str
    name:                 str
    industry:             Optional[str]
    planTier:             Optional[str]
    employeeCount:        int
    creditLimitUsd:       float
    creditsConsumedMtd:   float
    creditsRemaining:     float
    alertStatus:          str
    totalLifetimeSpend:   float
    ownerId:              Optional[str]
    createdAt:            Optional[str]


class CompanyDetail(BaseModel):
    id:                   str
    name:                 str
    industry:             Optional[str]
    size:                 Optional[str]
    website:              Optional[str]
    planTier:             Optional[str]
    employeeCount:        int
    creditLimitUsd:       float
    creditsConsumedMtd:   float
    creditsRemaining:     float
    warningThresholdPct:  float
    alertStatus:          str
    totalLifetimeSpend:   float
    lastResetAt:          Optional[str]
    ownerId:              Optional[str]
    createdAt:            Optional[str]
    # usage totals (last 30 days)
    tokensIn30d:          int
    tokensOut30d:         int
    costUsd30d:           float
    # employee breakdown
    employees:            List[Dict[str, Any]]


class UserDetail(BaseModel):
    uid:              str
    email:            str
    firstName:        str
    lastName:         str
    role:             str
    companyId:        Optional[str]
    companyName:      Optional[str]
    department:       Optional[str]
    isActive:         bool
    lastActiveAt:     Optional[str]
    createdAt:        Optional[str]
    # usage summary (all-time)
    totalTokensIn:    int
    totalTokensOut:   int
    totalCostUsd:     float
    totalCalls:       int
    # per-feature breakdown
    featureBreakdown: Dict[str, Dict[str, Any]]


class UsageRecord(BaseModel):
    id:               str
    userId:           str
    companyId:        str
    feature:          str
    model:            str
    provider:         str
    tokensIn:         int
    tokensOut:        int
    totalTokens:      int
    estimatedCostUsd: float
    latencyMs:        int
    success:          bool
    error:            Optional[str]
    timestamp:        Optional[str]


class CreditBalance(BaseModel):
    companyId:            str
    companyName:          str
    planTier:             str
    creditLimitUsd:       float
    creditsConsumedMtd:   float
    creditsRemaining:     float
    alertStatus:          str
    totalLifetimeSpend:   float
    lastResetAt:          Optional[str]


class AuditEntry(BaseModel):
    id:          str
    actorUid:    str
    actorRole:   str
    action:      str
    targetUid:   Optional[str]
    targetType:  str
    companyId:   str
    metadata:    Dict[str, Any]
    timestamp:   Optional[str]
    success:     bool


# ─── GET /admin/overview ──────────────────────────────────────────────────────

@router.get(
    "/overview",
    response_model=PlatformOverview,
    summary="Platform Overview KPIs",
)
async def admin_overview(_: dict = Depends(get_super_admin_user)):
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    now = datetime.now(timezone.utc)
    cutoff_30d = now.timestamp() - 30 * 86400

    # ── Users ──────────────────────────────────────────────────────────────
    try:
        all_users = list(db.collection("users").stream())
    except Exception as e:
        raise HTTPException(500, f"users query failed: {e}")

    total_employers = total_employees = active = inactive = new_30d = 0
    for doc in all_users:
        d = doc.to_dict()
        role = d.get("role", "unknown")
        if role == "employer":
            total_employers += 1
        elif role not in ("super_admin",):
            total_employees += 1
        if d.get("is_active", True):
            active += 1
        else:
            inactive += 1
        ts = d.get("created_at") or d.get("registered_at")
        if ts and hasattr(ts, "timestamp") and ts.timestamp() >= cutoff_30d:
            new_30d += 1

    # ── Companies ──────────────────────────────────────────────────────────
    try:
        all_companies = len(list(db.collection("companies").stream()))
    except Exception:
        all_companies = total_employers

    # ── Credits ────────────────────────────────────────────────────────────
    total_consumed = 0.0
    total_lifetime = 0.0
    at_warning = 0
    at_critical = 0
    try:
        credit_docs = db.collection("company_credits").stream()
        for doc in credit_docs:
            d = doc.to_dict()
            total_consumed += _safe_float(d.get("credits_consumed_mtd"))
            total_lifetime += _safe_float(d.get("total_lifetime_spend_usd"))
            status = d.get("alert_status", "normal")
            if status == "warning":
                at_warning += 1
            elif status in ("critical", "limit_reached"):
                at_critical += 1
    except Exception as e:
        print(f"[admin_metrics] credits aggregate error: {e}")

    return PlatformOverview(
        totalCompanies=all_companies,
        totalEmployers=total_employers,
        totalEmployees=total_employees,
        activeUsers=active,
        inactiveUsers=inactive,
        newUsersLast30d=new_30d,
        totalCreditsConsumed=round(total_consumed, 6),
        totalLifetimeSpend=round(total_lifetime, 6),
        companiesAtWarning=at_warning,
        companiesAtCritical=at_critical,
        computedAt=now.isoformat(),
    )


# ─── GET /admin/companies ─────────────────────────────────────────────────────

@router.get(
    "/companies",
    summary="All Companies with Credit Summary",
)
async def admin_list_companies(
    search:           Optional[str] = Query(None),
    alert_status:     Optional[str] = Query(None, description="Filter by alert_status"),
    plan_tier:        Optional[str] = Query(None),
    page:             int           = Query(1, ge=1),
    limit:            int           = Query(20, ge=1, le=100),
    _: dict = Depends(get_super_admin_user),
):
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    try:
        company_docs = list(db.collection("companies").stream())
    except Exception as e:
        raise HTTPException(500, f"companies query failed: {e}")

    # Build credit map for O(1) lookups
    credit_map: Dict[str, dict] = {}
    try:
        for doc in db.collection("company_credits").stream():
            credit_map[doc.id] = doc.to_dict()
    except Exception as e:
        print(f"[admin_metrics] credit_map error: {e}")

    result: List[CompanySummary] = []
    for doc in company_docs:
        d     = doc.to_dict()
        cid   = doc.id
        creds = credit_map.get(cid, {})

        if search:
            term = search.lower()
            searchable = f"{d.get('name','').lower()} {d.get('industry','').lower()}"
            if term not in searchable:
                continue

        a_status = creds.get("alert_status", "normal")
        if alert_status and a_status != alert_status:
            continue

        pt = creds.get("plan_tier") or d.get("plan_tier")
        if plan_tier and pt != plan_tier:
            continue

        result.append(CompanySummary(
            id=cid,
            name=d.get("name", ""),
            industry=d.get("industry"),
            planTier=pt,
            employeeCount=_safe_int(d.get("employee_count")),
            creditLimitUsd=_safe_float(creds.get("credit_limit_usd", 10.0)),
            creditsConsumedMtd=_safe_float(creds.get("credits_consumed_mtd")),
            creditsRemaining=_safe_float(creds.get("credits_remaining", 10.0)),
            alertStatus=a_status,
            totalLifetimeSpend=_safe_float(creds.get("total_lifetime_spend_usd")),
            ownerId=d.get("owner_id"),
            createdAt=_ts_to_iso(d.get("created_at")),
        ))

    total = len(result)
    offset = (page - 1) * limit

    return {
        "companies":   result[offset: offset + limit],
        "total":       total,
        "page":        page,
        "limit":       limit,
        "totalPages":  max(1, (total + limit - 1) // limit),
        "hasNext":     offset + limit < total,
        "hasPrev":     page > 1,
    }


# ─── GET /admin/companies/{company_id} ───────────────────────────────────────

@router.get(
    "/companies/{company_id}",
    response_model=CompanyDetail,
    summary="Company Detail + Employees + Credit + 30d Usage",
)
async def admin_get_company_detail(
    company_id: str,
    _: dict = Depends(get_super_admin_user),
):
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    # Company doc
    company_doc = db.collection("companies").document(company_id).get()
    if not company_doc.exists:
        raise HTTPException(404, "Company not found.")
    cd = company_doc.to_dict()

    # Credits doc
    creds_doc = db.collection("company_credits").document(company_id).get()
    creds = creds_doc.to_dict() if creds_doc.exists else {}

    # Employees
    employees: List[Dict[str, Any]] = []
    try:
        emp_docs = (
            db.collection("users")
            .where("company_id", "==", company_id)
            .stream()
        )
        for doc in emp_docs:
            d = doc.to_dict()
            if d.get("role") in ("super_admin",):
                continue
            employees.append({
                "uid":        doc.id,
                "email":      d.get("email", ""),
                "firstName":  d.get("first_name", ""),
                "lastName":   d.get("last_name", ""),
                "role":       d.get("role", "employee"),
                "department": d.get("department"),
                "isActive":   d.get("is_active", True),
            })
    except Exception as e:
        print(f"[admin_metrics] employee list error for {company_id}: {e}")

    # 30-day usage totals
    tokens_in_30d = tokens_out_30d = 0
    cost_30d = 0.0
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        usage_docs = (
            db.collection("usage_logs")
            .where("company_id", "==", company_id)
            .where("timestamp", ">=", cutoff)
            .stream()
        )
        for doc in usage_docs:
            d = doc.to_dict()
            tokens_in_30d  += _safe_int(d.get("tokens_in"))
            tokens_out_30d += _safe_int(d.get("tokens_out"))
            cost_30d       += _safe_float(d.get("estimated_cost_usd"))
    except Exception as e:
        print(f"[admin_metrics] usage 30d error for {company_id}: {e}")

    return CompanyDetail(
        id=company_id,
        name=cd.get("name", ""),
        industry=cd.get("industry"),
        size=cd.get("size"),
        website=cd.get("website"),
        planTier=creds.get("plan_tier") or cd.get("plan_tier"),
        employeeCount=_safe_int(cd.get("employee_count")),
        creditLimitUsd=_safe_float(creds.get("credit_limit_usd", 10.0)),
        creditsConsumedMtd=_safe_float(creds.get("credits_consumed_mtd")),
        creditsRemaining=_safe_float(creds.get("credits_remaining", 10.0)),
        warningThresholdPct=_safe_float(creds.get("warning_threshold_pct", 80.0)),
        alertStatus=creds.get("alert_status", "normal"),
        totalLifetimeSpend=_safe_float(creds.get("total_lifetime_spend_usd")),
        lastResetAt=_ts_to_iso(creds.get("last_reset_at")),
        ownerId=cd.get("owner_id"),
        createdAt=_ts_to_iso(cd.get("created_at")),
        tokensIn30d=tokens_in_30d,
        tokensOut30d=tokens_out_30d,
        costUsd30d=round(cost_30d, 6),
        employees=employees,
    )


# ─── GET /admin/users/{uid} ───────────────────────────────────────────────────

@router.get(
    "/users/{uid}",
    response_model=UserDetail,
    summary="User Profile + All-Time Usage Summary",
)
async def admin_get_user_detail(
    uid: str,
    _: dict = Depends(get_super_admin_user),
):
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    user_doc = db.collection("users").document(uid).get()
    if not user_doc.exists:
        raise HTTPException(404, "User not found.")
    ud = user_doc.to_dict()

    # Aggregate all-time usage for this user
    total_in = total_out = total_calls = 0
    total_cost = 0.0
    feature_breakdown: Dict[str, Dict[str, Any]] = {}

    try:
        usage_docs = (
            db.collection("usage_logs")
            .where("user_id", "==", uid)
            .stream()
        )
        for doc in usage_docs:
            d = doc.to_dict()
            tin   = _safe_int(d.get("tokens_in"))
            tout  = _safe_int(d.get("tokens_out"))
            cost  = _safe_float(d.get("estimated_cost_usd"))
            feat  = d.get("feature", "unknown")

            total_in    += tin
            total_out   += tout
            total_cost  += cost
            total_calls += 1

            if feat not in feature_breakdown:
                feature_breakdown[feat] = {"calls": 0, "tokensIn": 0, "tokensOut": 0, "costUsd": 0.0}
            feature_breakdown[feat]["calls"]     += 1
            feature_breakdown[feat]["tokensIn"]  += tin
            feature_breakdown[feat]["tokensOut"] += tout
            feature_breakdown[feat]["costUsd"]   += cost
    except Exception as e:
        print(f"[admin_metrics] usage aggregate error for {uid}: {e}")

    # Round feature costs
    for feat in feature_breakdown:
        feature_breakdown[feat]["costUsd"] = round(feature_breakdown[feat]["costUsd"], 6)

    return UserDetail(
        uid=uid,
        email=ud.get("email", ""),
        firstName=ud.get("first_name", ""),
        lastName=ud.get("last_name", ""),
        role=ud.get("role", "employee"),
        companyId=ud.get("company_id"),
        companyName=ud.get("company_name"),
        department=ud.get("department"),
        isActive=ud.get("is_active", True),
        lastActiveAt=_ts_to_iso(ud.get("last_active_at")),
        createdAt=_ts_to_iso(ud.get("created_at") or ud.get("registered_at")),
        totalTokensIn=total_in,
        totalTokensOut=total_out,
        totalCostUsd=round(total_cost, 6),
        totalCalls=total_calls,
        featureBreakdown=feature_breakdown,
    )


# ─── GET /admin/usage ─────────────────────────────────────────────────────────

@router.get(
    "/usage",
    summary="Usage Logs (filterable, paginated)",
)
async def admin_usage_logs(
    company_id: Optional[str] = Query(None),
    user_id:    Optional[str] = Query(None),
    feature:    Optional[str] = Query(None),
    days:       int            = Query(30, ge=1, le=365, description="Look-back window in days"),
    page:       int            = Query(1, ge=1),
    limit:      int            = Query(50, ge=1, le=200),
    _: dict = Depends(get_super_admin_user),
):
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    try:
        query = db.collection("usage_logs").where("timestamp", ">=", cutoff)
        if company_id:
            query = query.where("company_id", "==", company_id)
        if user_id:
            query = query.where("user_id", "==", user_id)
        docs = list(query.stream())
    except Exception as e:
        raise HTTPException(500, f"usage_logs query failed: {e}")

    records: List[UsageRecord] = []
    for doc in docs:
        d = doc.to_dict()
        if feature and d.get("feature") != feature:
            continue
        records.append(UsageRecord(
            id=doc.id,
            userId=d.get("user_id", ""),
            companyId=d.get("company_id", ""),
            feature=d.get("feature", ""),
            model=d.get("model", ""),
            provider=d.get("provider", ""),
            tokensIn=_safe_int(d.get("tokens_in")),
            tokensOut=_safe_int(d.get("tokens_out")),
            totalTokens=_safe_int(d.get("total_tokens")),
            estimatedCostUsd=_safe_float(d.get("estimated_cost_usd")),
            latencyMs=_safe_int(d.get("latency_ms")),
            success=bool(d.get("success", True)),
            error=d.get("error"),
            timestamp=_ts_to_iso(d.get("timestamp")),
        ))

    # Sort newest first
    records.sort(key=lambda r: r.timestamp or "", reverse=True)

    total  = len(records)
    offset = (page - 1) * limit

    # Summary totals
    total_tokens_in  = sum(r.tokensIn  for r in records)
    total_tokens_out = sum(r.tokensOut for r in records)
    total_cost       = round(sum(r.estimatedCostUsd for r in records), 6)

    return {
        "records":        records[offset: offset + limit],
        "total":          total,
        "page":           page,
        "limit":          limit,
        "totalPages":     max(1, (total + limit - 1) // limit),
        "hasNext":        offset + limit < total,
        "hasPrev":        page > 1,
        "summary": {
            "totalTokensIn":  total_tokens_in,
            "totalTokensOut": total_tokens_out,
            "totalCostUsd":   total_cost,
            "totalCalls":     total,
        },
    }


# ─── GET /admin/credits ───────────────────────────────────────────────────────

@router.get(
    "/credits",
    summary="All Company Credit Balances",
)
async def admin_credit_balances(
    alert_status: Optional[str] = Query(None, description="Filter: normal|warning|critical|limit_reached"),
    plan_tier:    Optional[str] = Query(None),
    page:         int           = Query(1, ge=1),
    limit:        int           = Query(50, ge=1, le=200),
    _: dict = Depends(get_super_admin_user),
):
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    try:
        docs = list(db.collection("company_credits").stream())
    except Exception as e:
        raise HTTPException(500, f"company_credits query failed: {e}")

    balances: List[CreditBalance] = []
    for doc in docs:
        d  = doc.to_dict()
        pt = d.get("plan_tier", "free")
        st = d.get("alert_status", "normal")

        if alert_status and st != alert_status:
            continue
        if plan_tier and pt != plan_tier:
            continue

        balances.append(CreditBalance(
            companyId=doc.id,
            companyName=d.get("company_name", ""),
            planTier=pt,
            creditLimitUsd=_safe_float(d.get("credit_limit_usd", 10.0)),
            creditsConsumedMtd=_safe_float(d.get("credits_consumed_mtd")),
            creditsRemaining=_safe_float(d.get("credits_remaining", 10.0)),
            alertStatus=st,
            totalLifetimeSpend=_safe_float(d.get("total_lifetime_spend_usd")),
            lastResetAt=_ts_to_iso(d.get("last_reset_at")),
        ))

    # Sort by consumed descending — most spend first
    balances.sort(key=lambda b: b.creditsConsumedMtd, reverse=True)

    total  = len(balances)
    offset = (page - 1) * limit

    return {
        "balances":   balances[offset: offset + limit],
        "total":      total,
        "page":       page,
        "limit":      limit,
        "totalPages": max(1, (total + limit - 1) // limit),
        "hasNext":    offset + limit < total,
        "hasPrev":    page > 1,
    }


# ─── GET /admin/audit-log ─────────────────────────────────────────────────────

@router.get(
    "/audit-log",
    summary="Audit Log (filterable, paginated)",
)
async def admin_audit_log(
    company_id: Optional[str] = Query(None),
    actor_uid:  Optional[str] = Query(None),
    action:     Optional[str] = Query(None, description="e.g. user.create, company.update"),
    days:       int            = Query(30, ge=1, le=365),
    page:       int            = Query(1, ge=1),
    limit:      int            = Query(50, ge=1, le=200),
    _: dict = Depends(get_super_admin_user),
):
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    try:
        query = db.collection("audit_logs").where("timestamp", ">=", cutoff)
        if company_id:
            query = query.where("company_id", "==", company_id)
        if actor_uid:
            query = query.where("actor_uid", "==", actor_uid)
        docs = list(query.stream())
    except Exception as e:
        raise HTTPException(500, f"audit_logs query failed: {e}")

    entries: List[AuditEntry] = []
    for doc in docs:
        d = doc.to_dict()
        if action and d.get("action") != action:
            continue
        entries.append(AuditEntry(
            id=doc.id,
            actorUid=d.get("actor_uid", ""),
            actorRole=d.get("actor_role", ""),
            action=d.get("action", ""),
            targetUid=d.get("target_uid"),
            targetType=d.get("target_type", "user"),
            companyId=d.get("company_id", ""),
            metadata=d.get("metadata") or {},
            timestamp=_ts_to_iso(d.get("timestamp")),
            success=bool(d.get("success", True)),
        ))

    # Sort newest first
    entries.sort(key=lambda e: e.timestamp or "", reverse=True)

    total  = len(entries)
    offset = (page - 1) * limit

    return {
        "entries":    entries[offset: offset + limit],
        "total":      total,
        "page":       page,
        "limit":      limit,
        "totalPages": max(1, (total + limit - 1) // limit),
        "hasNext":    offset + limit < total,
        "hasPrev":    page > 1,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# GAMIFICATION ADMIN (Step 7)
# ═══════════════════════════════════════════════════════════════════════════════

# ─── Schemas ──────────────────────────────────────────────────────────────────

class CreateChallengeRequest(BaseModel):
    title:        str
    description:  str
    type:         str           # "daily_checkin" | "conversation" | "physical_health" | "streak" | "custom"
    target:       int           # e.g. 7 (do it 7 times), or streak length
    pointsReward: int
    companyId:    Optional[str] = None   # null = platform-wide
    startsAt:     Optional[str] = None   # ISO datetime string
    endsAt:       Optional[str] = None


class UpdateChallengeRequest(BaseModel):
    title:        Optional[str] = None
    description:  Optional[str] = None
    target:       Optional[int] = None
    pointsReward: Optional[int] = None
    isActive:     Optional[bool] = None
    startsAt:     Optional[str] = None
    endsAt:       Optional[str] = None


# ─── GET /admin/gamification/overview ────────────────────────────────────────

@router.get(
    "/gamification/overview",
    summary="Platform-Wide Gamification Health",
)
async def admin_gamification_overview(_: dict = Depends(get_super_admin_user)):
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    # All user_gamification docs
    total_players     = 0
    total_points      = 0
    total_levels      = 0
    badge_counts: Dict[str, int] = {}
    company_points:   Dict[str, int]   = {}
    company_names:    Dict[str, str]   = {}

    try:
        for doc in db.collection("user_gamification").stream():
            d  = doc.to_dict()
            cid = d.get("company_id", "")
            pts = _safe_int(d.get("total_points"))
            lvl = _safe_int(d.get("level", 1))
            total_players += 1
            total_points  += pts
            total_levels  += lvl
            company_points[cid] = company_points.get(cid, 0) + pts
            if cid not in company_names:
                company_names[cid] = cid   # filled below
            for badge in d.get("badges", []):
                badge_counts[badge] = badge_counts.get(badge, 0) + 1
    except Exception as e:
        print(f"[admin_metrics] gamification overview error: {e}")

    # Challenges
    total_challenges       = 0
    active_challenges      = 0
    total_completions      = 0
    try:
        for doc in db.collection("challenges").stream():
            d = doc.to_dict()
            total_challenges += 1
            if d.get("is_active", False):
                active_challenges += 1
    except Exception:
        pass

    try:
        for doc in db.collection("user_challenge_progress").stream():
            d = doc.to_dict()
            if d.get("completed", False):
                total_completions += 1
    except Exception:
        pass

    # Most engaged company by total points
    most_engaged = None
    if company_points:
        top_cid = max(company_points, key=lambda c: company_points[c])
        most_engaged = {
            "companyId":   top_cid,
            "totalPoints": company_points[top_cid],
        }

    avg_level = round(total_levels / total_players, 2) if total_players else 0.0
    top_badge = max(badge_counts, key=lambda b: badge_counts[b]) if badge_counts else None

    # 30-day gamification events (points by event type)
    points_by_event: Dict[str, int] = {}
    try:
        cutoff_30 = datetime.now(timezone.utc) - timedelta(days=30)
        for doc in db.collection("gamification_events").where("created_at", ">=", cutoff_30).stream():
            d      = doc.to_dict()
            etype  = d.get("event_type", "unknown")
            points = _safe_int(d.get("points"))
            points_by_event[etype] = points_by_event.get(etype, 0) + points
    except Exception as e:
        print(f"[admin_metrics] gamification_events error: {e}")

    return {
        "totalActivePlayers":       total_players,
        "avgLevelPlatform":         avg_level,
        "totalPointsAllTime":       total_points,
        "topBadge":                 top_badge,
        "badgeCounts":              badge_counts,
        "totalChallenges":          total_challenges,
        "activeChallenges":         active_challenges,
        "totalChallengeCompletions": total_completions,
        "pointsByEventType30d":     points_by_event,
        "mostEngagedCompany":       most_engaged,
        "computedAt":               datetime.now(timezone.utc).isoformat(),
    }


# ─── GET /admin/gamification/companies/{company_id} ──────────────────────────

@router.get(
    "/gamification/companies/{company_id}",
    summary="Per-Company Gamification Breakdown",
)
async def admin_gamification_company(
    company_id: str,
    _: dict = Depends(get_super_admin_user),
):
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    gam_docs = []
    try:
        gam_docs = list(
            db.collection("user_gamification")
            .where("company_id", "==", company_id)
            .stream()
        )
    except Exception as e:
        raise HTTPException(500, f"user_gamification query failed: {e}")

    if not gam_docs:
        return {
            "companyId": company_id, "totalPlayers": 0,
            "activePlayers7d": 0, "avgPoints": 0.0, "avgLevel": 0.0,
            "avgStreak": 0.0, "badgeDistribution": {}, "totalBadgesEarned": 0,
        }

    now = datetime.now(timezone.utc)
    cutoff_7d = now - timedelta(days=7)

    total_pts = total_lvl = total_streak = 0
    active_7d = 0
    badge_dist: Dict[str, int] = {}

    for doc in gam_docs:
        d = doc.to_dict()
        total_pts    += _safe_int(d.get("total_points"))
        total_lvl    += _safe_int(d.get("level", 1))
        total_streak += _safe_int(d.get("current_streak"))
        for badge in d.get("badges", []):
            badge_dist[badge] = badge_dist.get(badge, 0) + 1
        # last_check_in used as proxy for recent activity
        lci = d.get("last_check_in")
        if lci and hasattr(lci, "timestamp"):
            dt = datetime.fromtimestamp(lci.timestamp(), tz=timezone.utc)
            if dt >= cutoff_7d:
                active_7d += 1

    n = len(gam_docs)

    # 7-day points trend from gamification_events
    points_trend_7d: List[int] = [0] * 7
    try:
        for doc in (
            db.collection("gamification_events")
            .where("company_id", "==", company_id)
            .where("created_at",  ">=", cutoff_7d)
            .stream()
        ):
            d   = doc.to_dict()
            ts  = d.get("created_at")
            pts = _safe_int(d.get("points"))
            if ts and hasattr(ts, "timestamp"):
                day_idx = (now - datetime.fromtimestamp(ts.timestamp(), tz=timezone.utc)).days
                if 0 <= day_idx < 7:
                    points_trend_7d[6 - day_idx] += pts
    except Exception as e:
        print(f"[admin_metrics] gamification trend error: {e}")

    return {
        "companyId":          company_id,
        "totalPlayers":       n,
        "activePlayers7d":    active_7d,
        "avgPoints":          round(total_pts / n, 1),
        "avgLevel":           round(total_lvl / n, 2),
        "avgStreak":          round(total_streak / n, 2),
        "badgeDistribution":  badge_dist,
        "totalBadgesEarned":  sum(badge_dist.values()),
        "pointsTrend7d":      points_trend_7d,
    }


# ─── POST /admin/challenges ───────────────────────────────────────────────────

@router.post(
    "/challenges",
    status_code=201,
    summary="Create Admin-Managed Challenge",
)
async def admin_create_challenge(
    req: CreateChallengeRequest,
    admin: dict = Depends(get_super_admin_user),
):
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    challenge_id = str(uuid.uuid4())
    doc = {
        "id":           challenge_id,
        "title":        req.title,
        "description":  req.description,
        "type":         req.type,
        "target":       req.target,
        "points_reward": req.pointsReward,
        "company_id":   req.companyId,     # None = platform-wide
        "is_active":    True,
        "starts_at":    req.startsAt,
        "ends_at":      req.endsAt,
        "created_by":   admin.get("id", ""),
        "created_at":   SERVER_TIMESTAMP,
        "updated_at":   SERVER_TIMESTAMP,
    }

    try:
        db.collection("challenges").document(challenge_id).set(doc)
    except Exception as e:
        raise HTTPException(500, f"Failed to create challenge: {e}")

    return {"success": True, "challengeId": challenge_id, "message": "Challenge created."}


# ─── GET /admin/challenges ────────────────────────────────────────────────────

@router.get(
    "/challenges",
    summary="List All Admin-Managed Challenges",
)
async def admin_list_challenges(
    company_id:  Optional[str]  = Query(None, description="Filter by company (null = platform-wide only)"),
    is_active:   Optional[bool] = Query(None),
    page:        int            = Query(1, ge=1),
    limit:       int            = Query(20, ge=1, le=100),
    _: dict = Depends(get_super_admin_user),
):
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    try:
        docs = list(db.collection("challenges").stream())
    except Exception as e:
        raise HTTPException(500, f"challenges query failed: {e}")

    results = []
    for doc in docs:
        d = doc.to_dict()
        if company_id is not None and d.get("company_id") != company_id:
            continue
        if is_active is not None and bool(d.get("is_active")) != is_active:
            continue
        results.append({
            "id":           doc.id,
            "title":        d.get("title"),
            "description":  d.get("description"),
            "type":         d.get("type"),
            "target":       d.get("target"),
            "pointsReward": d.get("points_reward"),
            "companyId":    d.get("company_id"),
            "isActive":     d.get("is_active", True),
            "startsAt":     d.get("starts_at"),
            "endsAt":       d.get("ends_at"),
            "createdBy":    d.get("created_by"),
            "createdAt":    _ts_to_iso(d.get("created_at")),
        })

    total  = len(results)
    offset = (page - 1) * limit

    return {
        "challenges": results[offset: offset + limit],
        "total":      total,
        "page":       page,
        "limit":      limit,
        "totalPages": max(1, (total + limit - 1) // limit),
        "hasNext":    offset + limit < total,
        "hasPrev":    page > 1,
    }


# ─── PATCH /admin/challenges/{challenge_id} ───────────────────────────────────

@router.patch(
    "/challenges/{challenge_id}",
    summary="Update or Toggle Challenge",
)
async def admin_update_challenge(
    challenge_id: str,
    req: UpdateChallengeRequest,
    _: dict = Depends(get_super_admin_user),
):
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    doc = db.collection("challenges").document(challenge_id).get()
    if not doc.exists:
        raise HTTPException(404, "Challenge not found.")

    updates: Dict[str, Any] = {"updated_at": SERVER_TIMESTAMP}
    updated_fields = []

    field_map = {
        "title":        "title",
        "description":  "description",
        "target":       "target",
        "pointsReward": "points_reward",
        "isActive":     "is_active",
        "startsAt":     "starts_at",
        "endsAt":       "ends_at",
    }
    for req_field, db_field in field_map.items():
        val = getattr(req, req_field, None)
        if val is not None:
            updates[db_field] = val
            updated_fields.append(req_field)

    if len(updates) == 1:
        raise HTTPException(400, "No fields to update.")

    db.collection("challenges").document(challenge_id).update(updates)

    return {"success": True, "challengeId": challenge_id, "updatedFields": updated_fields}


# ─── GET /admin/challenges/{challenge_id}/stats ───────────────────────────────

@router.get(
    "/challenges/{challenge_id}/stats",
    summary="Challenge Participation & Completion Stats",
)
async def admin_challenge_stats(
    challenge_id: str,
    _: dict = Depends(get_super_admin_user),
):
    db = get_db()
    if not db:
        raise HTTPException(503, "Database unavailable")

    challenge_doc = db.collection("challenges").document(challenge_id).get()
    if not challenge_doc.exists:
        raise HTTPException(404, "Challenge not found.")
    cd = challenge_doc.to_dict()

    participants    = 0
    completions     = 0
    company_breakdown: Dict[str, Dict[str, int]] = {}

    try:
        for doc in (
            db.collection("user_challenge_progress")
            .where("challenge_id", "==", challenge_id)
            .stream()
        ):
            d   = doc.to_dict()
            cid = d.get("company_id", "unknown")
            participants += 1
            if d.get("completed", False):
                completions += 1
            if cid not in company_breakdown:
                company_breakdown[cid] = {"participants": 0, "completions": 0}
            company_breakdown[cid]["participants"] += 1
            if d.get("completed", False):
                company_breakdown[cid]["completions"] += 1
    except Exception as e:
        print(f"[admin_metrics] challenge stats error: {e}")

    completion_rate = round(completions / participants * 100, 1) if participants else 0.0

    return {
        "challengeId":      challenge_id,
        "title":            cd.get("title"),
        "type":             cd.get("type"),
        "target":           cd.get("target"),
        "pointsReward":     cd.get("points_reward"),
        "isActive":         cd.get("is_active", True),
        "participants":     participants,
        "completions":      completions,
        "completionRatePct": completion_rate,
        "companyBreakdown": company_breakdown,
    }
