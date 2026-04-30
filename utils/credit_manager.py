"""Per-company monthly credit balance (Postgres).

Maintains the ``company_credits`` row for each company. Called from
``middleware.usage_tracker`` after every successful LLM call to keep
``credits_consumed_mtd`` and ``credits_remaining`` in sync.

Default monthly credit limits per plan tier (USD):
    free:       $10
    starter:    $50
    pro:        $200
    enterprise: $1000
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import func

from db.models import Company, CompanyCredit
from db.session import get_session_factory


_PLAN_LIMITS: dict[str, float] = {
    "free": 10.0,
    "starter": 50.0,
    "pro": 200.0,
    "enterprise": 1000.0,
}


def _to_dict(row: CompanyCredit) -> dict:
    """Serialize a CompanyCredit row into the dict shape callers expect."""
    return {
        "company_id": str(row.company_id),
        "company_name": row.company_name or "",
        "plan_tier": row.plan_tier,
        "credit_limit_usd": float(row.credit_limit_usd or 0),
        "credits_consumed_mtd": float(row.credits_consumed_mtd or 0),
        "credits_remaining": float(row.credits_remaining or 0),
        "warning_threshold_pct": float(row.warning_threshold_pct or 80),
        "alert_status": row.alert_status,
        "total_lifetime_spend_usd": float(row.total_lifetime_spend_usd or 0),
        "last_reset_at": row.last_reset_at,
        "last_warning_sent_at": row.last_warning_sent_at,
        "last_critical_sent_at": row.last_critical_sent_at,
        "updated_at": row.updated_at,
    }


def _get_or_create(session, company_uuid: uuid.UUID) -> CompanyCredit:
    """Return the CompanyCredit row, creating with plan-tier defaults if absent."""
    row = (
        session.query(CompanyCredit)
        .filter(CompanyCredit.company_id == company_uuid)
        .one_or_none()
    )
    if row is not None:
        return row

    # Lazy creation — pull plan_tier + name from companies table.
    plan_tier = "free"
    company_name = ""
    co = (
        session.query(Company)
        .filter(Company.id == company_uuid)
        .one_or_none()
    )
    if co is not None:
        plan_tier = (co.settings or {}).get("plan_tier", "free")
        company_name = co.name or ""

    limit = _PLAN_LIMITS.get(plan_tier, 10.0)
    row = CompanyCredit(
        company_id=company_uuid,
        company_name=company_name,
        plan_tier=plan_tier,
        credit_limit_usd=limit,
        credits_consumed_mtd=0,
        credits_remaining=limit,
        warning_threshold_pct=80.0,
        alert_status="normal",
        total_lifetime_spend_usd=0,
    )
    session.add(row)
    session.flush()
    return row


def update_company_credits(
    company_id, cost_usd: float, db=None
) -> Optional[dict]:
    """Add ``cost_usd`` to consumed_mtd, subtract from remaining. Returns updated dict.

    The ``db`` parameter is kept for backward compat with the Firestore-era
    signature but is ignored — we open our own session.
    """
    if not company_id or cost_usd <= 0:
        return None

    if isinstance(company_id, str):
        try:
            company_uuid = uuid.UUID(company_id)
        except ValueError:
            return None
    else:
        company_uuid = company_id

    try:
        SessionLocal = get_session_factory()
        with SessionLocal() as session:
            row = _get_or_create(session, company_uuid)
            cost_dec = Decimal(str(cost_usd))
            row.credits_consumed_mtd = (row.credits_consumed_mtd or Decimal(0)) + cost_dec
            row.credits_remaining = (row.credits_remaining or Decimal(0)) - cost_dec
            row.total_lifetime_spend_usd = (row.total_lifetime_spend_usd or Decimal(0)) + cost_dec
            session.commit()
            session.refresh(row)
            return _to_dict(row)
    except Exception as e:
        print(f"[credit_manager] update error for {company_id}: {e}")
        return None


def reset_monthly_credits(company_id, db=None) -> None:
    """Reset MTD consumption to zero at the start of a new billing month.

    Intended to be called by a scheduled job (e.g. APScheduler / cron) on the
    1st of each month.
    """
    if isinstance(company_id, str):
        try:
            company_uuid = uuid.UUID(company_id)
        except ValueError:
            return
    else:
        company_uuid = company_id

    try:
        SessionLocal = get_session_factory()
        with SessionLocal() as session:
            row = (
                session.query(CompanyCredit)
                .filter(CompanyCredit.company_id == company_uuid)
                .one_or_none()
            )
            if row is None:
                return
            row.credits_consumed_mtd = 0
            row.credits_remaining = row.credit_limit_usd
            row.alert_status = "normal"
            row.last_reset_at = func.now()
            session.commit()
    except Exception as e:
        print(f"[credit_manager] reset error for {company_id}: {e}")
