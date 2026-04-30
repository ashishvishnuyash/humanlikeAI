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

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from db.models import (
    AuditLog,
    Company,
    CompanyCredit,
    GamificationEvent,
    UsageLog,
    User,
    UserGamification,
    WellnessChallenge,
)
from db.session import get_session
from routers.auth import get_super_admin_user

router = APIRouter(prefix="/admin", tags=["Admin Metrics"])


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _ts_to_iso(ts) -> Optional[str]:
    if ts is None:
        return None
    try:
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts.isoformat()
        if hasattr(ts, "timestamp"):
            return datetime.fromtimestamp(ts.timestamp(), tz=timezone.utc).isoformat()
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


def _parse_company_uuid(company_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(company_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(400, f"Invalid company_id: {company_id!r}")


def _parse_challenge_uuid(challenge_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(challenge_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(400, f"Invalid challenge_id: {challenge_id!r}")


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
async def admin_overview(
    _: dict = Depends(get_super_admin_user),
    db: Session = Depends(get_session),
):
    now = datetime.now(timezone.utc)
    cutoff_30d = now - timedelta(days=30)

    # ── Users ──────────────────────────────────────────────────────────────
    try:
        all_users: List[User] = db.query(User).all()
    except Exception as e:
        raise HTTPException(500, f"users query failed: {e}")

    total_employers = total_employees = active = inactive = new_30d = 0
    for u in all_users:
        role = u.role or "unknown"
        if role == "employer":
            total_employers += 1
        elif role not in ("super_admin",):
            total_employees += 1
        if u.is_active:
            active += 1
        else:
            inactive += 1
        ts = u.created_at
        if ts is not None:
            ts_aware = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            if ts_aware >= cutoff_30d:
                new_30d += 1

    # ── Companies ──────────────────────────────────────────────────────────
    try:
        all_companies = db.query(func.count(Company.id)).scalar() or 0
    except Exception:
        all_companies = total_employers

    # ── Credits ────────────────────────────────────────────────────────────
    total_consumed = 0.0
    total_lifetime = 0.0
    at_warning = 0
    at_critical = 0
    try:
        for cc in db.query(CompanyCredit).all():
            total_consumed += _safe_float(cc.credits_consumed_mtd)
            total_lifetime += _safe_float(cc.total_lifetime_spend_usd)
            status = cc.alert_status or "normal"
            if status == "warning":
                at_warning += 1
            elif status in ("critical", "limit_reached"):
                at_critical += 1
    except Exception as e:
        print(f"[admin_metrics] credits aggregate error: {e}")

    return PlatformOverview(
        totalCompanies=int(all_companies),
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
    db: Session = Depends(get_session),
):
    try:
        company_rows: List[Company] = db.query(Company).all()
    except Exception as e:
        raise HTTPException(500, f"companies query failed: {e}")

    # Build credit map for O(1) lookups
    credit_map: Dict[str, CompanyCredit] = {}
    try:
        for cc in db.query(CompanyCredit).all():
            credit_map[str(cc.company_id)] = cc
    except Exception as e:
        print(f"[admin_metrics] credit_map error: {e}")

    result: List[CompanySummary] = []
    for company in company_rows:
        s = company.settings or {}
        cid = str(company.id)
        creds = credit_map.get(cid)

        if search:
            term = search.lower()
            searchable = f"{(company.name or '').lower()} {(s.get('industry') or '').lower()}"
            if term not in searchable:
                continue

        a_status = (creds.alert_status if creds else None) or "normal"
        if alert_status and a_status != alert_status:
            continue

        pt = (creds.plan_tier if creds else None) or s.get("plan_tier")
        if plan_tier and pt != plan_tier:
            continue

        result.append(CompanySummary(
            id=cid,
            name=company.name or "",
            industry=s.get("industry"),
            planTier=pt,
            employeeCount=_safe_int(company.employee_count),
            creditLimitUsd=_safe_float(creds.credit_limit_usd if creds else 10.0, 10.0),
            creditsConsumedMtd=_safe_float(creds.credits_consumed_mtd if creds else 0),
            creditsRemaining=_safe_float(creds.credits_remaining if creds else 10.0, 10.0),
            alertStatus=a_status,
            totalLifetimeSpend=_safe_float(creds.total_lifetime_spend_usd if creds else 0),
            ownerId=company.owner_id,
            createdAt=_ts_to_iso(company.created_at),
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
    db: Session = Depends(get_session),
):
    cid_uuid = _parse_company_uuid(company_id)

    # Company doc
    company = db.query(Company).filter(Company.id == cid_uuid).one_or_none()
    if company is None:
        raise HTTPException(404, "Company not found.")
    s = company.settings or {}

    # Credits doc
    creds = db.query(CompanyCredit).filter(CompanyCredit.company_id == cid_uuid).one_or_none()

    # Employees
    employees: List[Dict[str, Any]] = []
    try:
        emp_rows: List[User] = db.query(User).filter(User.company_id == cid_uuid).all()
        for u in emp_rows:
            if (u.role or "") in ("super_admin",):
                continue
            p = u.profile or {}
            employees.append({
                "uid":        u.id,
                "email":      u.email or "",
                "firstName":  p.get("first_name", ""),
                "lastName":   p.get("last_name", ""),
                "role":       u.role or "employee",
                "department": u.department,
                "isActive":   bool(u.is_active),
            })
    except Exception as e:
        print(f"[admin_metrics] employee list error for {company_id}: {e}")

    # 30-day usage totals
    tokens_in_30d = tokens_out_30d = 0
    cost_30d = 0.0
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        usage_rows = (
            db.query(UsageLog)
            .filter(UsageLog.company_id == cid_uuid)
            .filter(UsageLog.created_at >= cutoff)
            .all()
        )
        for u in usage_rows:
            tokens_in_30d  += _safe_int(u.tokens_in)
            tokens_out_30d += _safe_int(u.tokens_out)
            cost_30d       += _safe_float(u.estimated_cost_usd)
    except Exception as e:
        print(f"[admin_metrics] usage 30d error for {company_id}: {e}")

    return CompanyDetail(
        id=str(company.id),
        name=company.name or "",
        industry=s.get("industry"),
        size=s.get("size"),
        website=s.get("website"),
        planTier=(creds.plan_tier if creds else None) or s.get("plan_tier"),
        employeeCount=_safe_int(company.employee_count),
        creditLimitUsd=_safe_float(creds.credit_limit_usd if creds else 10.0, 10.0),
        creditsConsumedMtd=_safe_float(creds.credits_consumed_mtd if creds else 0),
        creditsRemaining=_safe_float(creds.credits_remaining if creds else 10.0, 10.0),
        warningThresholdPct=_safe_float(creds.warning_threshold_pct if creds else 80.0, 80.0),
        alertStatus=(creds.alert_status if creds else None) or "normal",
        totalLifetimeSpend=_safe_float(creds.total_lifetime_spend_usd if creds else 0),
        lastResetAt=_ts_to_iso(creds.last_reset_at if creds else None),
        ownerId=company.owner_id,
        createdAt=_ts_to_iso(company.created_at),
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
    db: Session = Depends(get_session),
):
    user = db.query(User).filter(User.id == uid).one_or_none()
    if user is None:
        raise HTTPException(404, "User not found.")

    p = user.profile or {}

    # Resolve company name via Company lookup if available
    company_name: Optional[str] = p.get("company_name")
    if user.company_id and not company_name:
        company = db.query(Company).filter(Company.id == user.company_id).one_or_none()
        if company is not None:
            company_name = company.name

    # Aggregate all-time usage for this user
    total_in = total_out = total_calls = 0
    total_cost = 0.0
    feature_breakdown: Dict[str, Dict[str, Any]] = {}

    try:
        usage_rows: List[UsageLog] = (
            db.query(UsageLog).filter(UsageLog.user_id == uid).all()
        )
        for u in usage_rows:
            tin   = _safe_int(u.tokens_in)
            tout  = _safe_int(u.tokens_out)
            cost  = _safe_float(u.estimated_cost_usd)
            feat  = u.feature or "unknown"

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
        email=user.email or "",
        firstName=p.get("first_name", ""),
        lastName=p.get("last_name", ""),
        role=user.role or "employee",
        companyId=str(user.company_id) if user.company_id else None,
        companyName=company_name,
        department=user.department,
        isActive=bool(user.is_active),
        lastActiveAt=_ts_to_iso(user.last_active_at),
        createdAt=_ts_to_iso(user.created_at),
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
    db: Session = Depends(get_session),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    try:
        q = db.query(UsageLog).filter(UsageLog.created_at >= cutoff)
        if company_id:
            cid_uuid = _parse_company_uuid(company_id)
            q = q.filter(UsageLog.company_id == cid_uuid)
        if user_id:
            q = q.filter(UsageLog.user_id == user_id)
        rows: List[UsageLog] = q.all()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"usage_logs query failed: {e}")

    records: List[UsageRecord] = []
    for u in rows:
        if feature and u.feature != feature:
            continue
        records.append(UsageRecord(
            id=str(u.id),
            userId=u.user_id or "",
            companyId=str(u.company_id) if u.company_id else "",
            feature=u.feature or "",
            model=u.model or "",
            provider=u.provider or "",
            tokensIn=_safe_int(u.tokens_in),
            tokensOut=_safe_int(u.tokens_out),
            totalTokens=_safe_int(u.total_tokens),
            estimatedCostUsd=_safe_float(u.estimated_cost_usd),
            latencyMs=_safe_int(u.latency_ms),
            success=bool(u.success if u.success is not None else True),
            error=u.error,
            timestamp=_ts_to_iso(u.created_at),
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
    db: Session = Depends(get_session),
):
    try:
        rows: List[CompanyCredit] = db.query(CompanyCredit).all()
    except Exception as e:
        raise HTTPException(500, f"company_credits query failed: {e}")

    balances: List[CreditBalance] = []
    for cc in rows:
        pt = cc.plan_tier or "free"
        st = cc.alert_status or "normal"

        if alert_status and st != alert_status:
            continue
        if plan_tier and pt != plan_tier:
            continue

        balances.append(CreditBalance(
            companyId=str(cc.company_id),
            companyName=cc.company_name or "",
            planTier=pt,
            creditLimitUsd=_safe_float(cc.credit_limit_usd, 10.0),
            creditsConsumedMtd=_safe_float(cc.credits_consumed_mtd),
            creditsRemaining=_safe_float(cc.credits_remaining, 10.0),
            alertStatus=st,
            totalLifetimeSpend=_safe_float(cc.total_lifetime_spend_usd),
            lastResetAt=_ts_to_iso(cc.last_reset_at),
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
    db: Session = Depends(get_session),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    try:
        q = db.query(AuditLog).filter(AuditLog.created_at >= cutoff)
        if company_id:
            cid_uuid = _parse_company_uuid(company_id)
            q = q.filter(AuditLog.company_id == cid_uuid)
        if actor_uid:
            q = q.filter(AuditLog.actor_uid == actor_uid)
        rows: List[AuditLog] = q.all()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"audit_logs query failed: {e}")

    entries: List[AuditEntry] = []
    for a in rows:
        if action and a.action != action:
            continue
        entries.append(AuditEntry(
            id=str(a.id),
            actorUid=a.actor_uid or "",
            actorRole=a.actor_role or "",
            action=a.action or "",
            targetUid=a.target_uid,
            targetType=a.target_type or "user",
            companyId=str(a.company_id) if a.company_id else "",
            metadata=a.audit_metadata or {},
            timestamp=_ts_to_iso(a.created_at),
            success=bool(a.success if a.success is not None else True),
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


def _parse_iso_datetime(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        # Accept "Z" suffix
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


# ─── GET /admin/gamification/overview ────────────────────────────────────────

@router.get(
    "/gamification/overview",
    summary="Platform-Wide Gamification Health",
)
async def admin_gamification_overview(
    _: dict = Depends(get_super_admin_user),
    db: Session = Depends(get_session),
):
    # All user_gamification rows
    total_players     = 0
    total_points      = 0
    total_levels      = 0
    badge_counts: Dict[str, int] = {}
    company_points:   Dict[str, int]   = {}
    company_names:    Dict[str, str]   = {}

    try:
        for ug in db.query(UserGamification).all():
            cid = str(ug.company_id) if ug.company_id else ""
            pts = _safe_int(ug.points)
            lvl = _safe_int(ug.level if ug.level is not None else 1, 1)
            total_players += 1
            total_points  += pts
            total_levels  += lvl
            company_points[cid] = company_points.get(cid, 0) + pts
            if cid not in company_names:
                company_names[cid] = cid   # filled below
            for badge in (ug.badges or []):
                badge_counts[badge] = badge_counts.get(badge, 0) + 1
    except Exception as e:
        print(f"[admin_metrics] gamification overview error: {e}")

    # Challenges (use WellnessChallenge; participation tracked via GamificationEvent)
    total_challenges       = 0
    active_challenges      = 0
    total_completions      = 0
    try:
        for ch in db.query(WellnessChallenge).all():
            total_challenges += 1
            if ch.is_active:
                active_challenges += 1
    except Exception:
        pass

    try:
        total_completions = (
            db.query(func.count(GamificationEvent.id))
            .filter(GamificationEvent.event_type == "challenge_completed")
            .scalar()
            or 0
        )
    except Exception:
        total_completions = 0

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
        for ge in db.query(GamificationEvent).filter(GamificationEvent.created_at >= cutoff_30).all():
            etype  = ge.event_type or "unknown"
            points = _safe_int(ge.points)
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
        "totalChallengeCompletions": int(total_completions),
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
    db: Session = Depends(get_session),
):
    cid_uuid = _parse_company_uuid(company_id)

    try:
        gam_rows: List[UserGamification] = (
            db.query(UserGamification)
            .filter(UserGamification.company_id == cid_uuid)
            .all()
        )
    except Exception as e:
        raise HTTPException(500, f"user_gamification query failed: {e}")

    if not gam_rows:
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

    for ug in gam_rows:
        total_pts    += _safe_int(ug.points)
        total_lvl    += _safe_int(ug.level if ug.level is not None else 1, 1)
        total_streak += _safe_int(ug.streak)
        for badge in (ug.badges or []):
            badge_dist[badge] = badge_dist.get(badge, 0) + 1
        # last_check_in (from extras JSONB) used as proxy for recent activity
        extras = ug.extras or {}
        lci = extras.get("last_check_in")
        lci_dt = _parse_iso_datetime(lci) if isinstance(lci, str) else None
        if lci_dt is None and isinstance(lci, datetime):
            lci_dt = lci if lci.tzinfo else lci.replace(tzinfo=timezone.utc)
        if lci_dt is not None and lci_dt >= cutoff_7d:
            active_7d += 1

    n = len(gam_rows)

    # 7-day points trend from gamification_events
    points_trend_7d: List[int] = [0] * 7
    try:
        events = (
            db.query(GamificationEvent)
            .filter(GamificationEvent.company_id == cid_uuid)
            .filter(GamificationEvent.created_at >= cutoff_7d)
            .all()
        )
        for ge in events:
            ts = ge.created_at
            if ts is None:
                continue
            ts_aware = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            pts = _safe_int(ge.points)
            day_idx = (now - ts_aware).days
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
    db: Session = Depends(get_session),
):
    company_uuid: Optional[uuid.UUID] = None
    if req.companyId:
        company_uuid = _parse_company_uuid(req.companyId)

    starts_at_dt = _parse_iso_datetime(req.startsAt)
    ends_at_dt = _parse_iso_datetime(req.endsAt)

    challenge = WellnessChallenge(
        id=uuid.uuid4(),
        company_id=company_uuid,
        title=req.title,
        description=req.description,
        is_active=True,
        data={
            "type":          req.type,
            "target":        req.target,
            "points_reward": req.pointsReward,
            "starts_at":     req.startsAt,
            "ends_at":       req.endsAt,
            "created_by":    admin.get("id", ""),
        },
        starts_at=starts_at_dt,
        ends_at=ends_at_dt,
    )

    try:
        db.add(challenge)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"Failed to create challenge: {e}")

    return {"success": True, "challengeId": str(challenge.id), "message": "Challenge created."}


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
    db: Session = Depends(get_session),
):
    try:
        rows: List[WellnessChallenge] = db.query(WellnessChallenge).all()
    except Exception as e:
        raise HTTPException(500, f"challenges query failed: {e}")

    # Optional filter for company_id (string compare on stringified UUID; None == platform-wide)
    filter_cid_uuid: Optional[uuid.UUID] = None
    if company_id is not None:
        filter_cid_uuid = _parse_company_uuid(company_id)

    results = []
    for ch in rows:
        if company_id is not None and ch.company_id != filter_cid_uuid:
            continue
        if is_active is not None and bool(ch.is_active) != is_active:
            continue
        data = ch.data or {}
        results.append({
            "id":           str(ch.id),
            "title":        ch.title,
            "description":  ch.description,
            "type":         data.get("type"),
            "target":       data.get("target"),
            "pointsReward": data.get("points_reward"),
            "companyId":    str(ch.company_id) if ch.company_id else None,
            "isActive":     bool(ch.is_active),
            "startsAt":     data.get("starts_at") or _ts_to_iso(ch.starts_at),
            "endsAt":       data.get("ends_at") or _ts_to_iso(ch.ends_at),
            "createdBy":    data.get("created_by"),
            "createdAt":    _ts_to_iso(ch.created_at),
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
    db: Session = Depends(get_session),
):
    ch_uuid = _parse_challenge_uuid(challenge_id)

    challenge = db.query(WellnessChallenge).filter(WellnessChallenge.id == ch_uuid).one_or_none()
    if challenge is None:
        raise HTTPException(404, "Challenge not found.")

    updated_fields: List[str] = []

    # Direct columns
    if req.title is not None:
        challenge.title = req.title
        updated_fields.append("title")
    if req.description is not None:
        challenge.description = req.description
        updated_fields.append("description")
    if req.isActive is not None:
        challenge.is_active = req.isActive
        updated_fields.append("isActive")
    if req.startsAt is not None:
        starts_at_dt = _parse_iso_datetime(req.startsAt)
        challenge.starts_at = starts_at_dt
        updated_fields.append("startsAt")
    if req.endsAt is not None:
        ends_at_dt = _parse_iso_datetime(req.endsAt)
        challenge.ends_at = ends_at_dt
        updated_fields.append("endsAt")

    # JSONB extras (data)
    data = dict(challenge.data or {})
    if req.target is not None:
        data["target"] = req.target
        updated_fields.append("target")
    if req.pointsReward is not None:
        data["points_reward"] = req.pointsReward
        updated_fields.append("pointsReward")
    if req.startsAt is not None:
        data["starts_at"] = req.startsAt
    if req.endsAt is not None:
        data["ends_at"] = req.endsAt
    challenge.data = data

    if not updated_fields:
        raise HTTPException(400, "No fields to update.")

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"Failed to update challenge: {e}")

    return {"success": True, "challengeId": challenge_id, "updatedFields": updated_fields}


# ─── GET /admin/challenges/{challenge_id}/stats ───────────────────────────────

@router.get(
    "/challenges/{challenge_id}/stats",
    summary="Challenge Participation & Completion Stats",
)
async def admin_challenge_stats(
    challenge_id: str,
    _: dict = Depends(get_super_admin_user),
    db: Session = Depends(get_session),
):
    ch_uuid = _parse_challenge_uuid(challenge_id)

    challenge = db.query(WellnessChallenge).filter(WellnessChallenge.id == ch_uuid).one_or_none()
    if challenge is None:
        raise HTTPException(404, "Challenge not found.")
    cd = challenge.data or {}

    participants    = 0
    completions     = 0
    company_breakdown: Dict[str, Dict[str, int]] = {}

    # Use GamificationEvent records as participation proxy:
    #   event_type in ("challenge_joined", "challenge_completed")
    #   event_metadata.challenge_id == challenge_id
    try:
        events = (
            db.query(GamificationEvent)
            .filter(
                GamificationEvent.event_type.in_(
                    ["challenge_joined", "challenge_completed"]
                )
            )
            .all()
        )
        # Track unique participants by user
        seen_participants: set[str] = set()
        seen_completions: set[str] = set()
        company_seen_participants: Dict[str, set[str]] = {}
        company_seen_completions: Dict[str, set[str]] = {}

        for ge in events:
            meta = ge.event_metadata or {}
            if str(meta.get("challenge_id", "")) != challenge_id:
                continue
            cid = str(ge.company_id) if ge.company_id else "unknown"
            uid = ge.user_id

            if cid not in company_breakdown:
                company_breakdown[cid] = {"participants": 0, "completions": 0}
                company_seen_participants[cid] = set()
                company_seen_completions[cid] = set()

            if uid not in seen_participants:
                seen_participants.add(uid)
                participants += 1
            if uid not in company_seen_participants[cid]:
                company_seen_participants[cid].add(uid)
                company_breakdown[cid]["participants"] += 1

            if ge.event_type == "challenge_completed":
                if uid not in seen_completions:
                    seen_completions.add(uid)
                    completions += 1
                if uid not in company_seen_completions[cid]:
                    company_seen_completions[cid].add(uid)
                    company_breakdown[cid]["completions"] += 1
    except Exception as e:
        print(f"[admin_metrics] challenge stats error: {e}")

    completion_rate = round(completions / participants * 100, 1) if participants else 0.0

    return {
        "challengeId":      challenge_id,
        "title":            challenge.title,
        "type":             cd.get("type"),
        "target":           cd.get("target"),
        "pointsReward":     cd.get("points_reward"),
        "isActive":         bool(challenge.is_active),
        "participants":     participants,
        "completions":      completions,
        "completionRatePct": completion_rate,
        "companyBreakdown": company_breakdown,
    }
