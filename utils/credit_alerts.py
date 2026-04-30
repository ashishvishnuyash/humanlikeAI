"""Credit alert system (Postgres).

Checks credit thresholds after every credit update and sends email alerts
when a company crosses into a higher alert tier.

Alert tiers:
    normal         < 80%
    warning        80-95%       email to company employer
    critical       95-100%      email to company employer
    limit_reached  >= 100%      email to company employer

Same-month duplicate suppression via ``last_warning_sent_at`` /
``last_critical_sent_at`` columns.

Called from ``middleware.usage_tracker`` (already inside a daemon thread, so
the Postgres writes + email send don't block the request).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func

from db.models import CompanyCredit, User
from db.session import get_session_factory
from utils.email import _send as send_email


# ─── Threshold helpers ────────────────────────────────────────────────────────


def _usage_pct(data: dict) -> float:
    limit = float(data.get("credit_limit_usd") or 10.0)
    if limit <= 0:
        return 0.0
    consumed = float(data.get("credits_consumed_mtd") or 0)
    return round((consumed / limit) * 100, 2)


def _new_status(pct: float) -> str:
    if pct >= 100:
        return "limit_reached"
    if pct >= 95:
        return "critical"
    if pct >= 80:
        return "warning"
    return "normal"


_STATUS_RANK = {"normal": 0, "warning": 1, "critical": 2, "limit_reached": 3}


# ─── Employer email lookup ────────────────────────────────────────────────────


def _get_employer_email(session, company_uuid: uuid.UUID) -> Optional[str]:
    user = (
        session.query(User)
        .filter(User.company_id == company_uuid, User.role == "employer")
        .order_by(User.created_at.asc())
        .first()
    )
    return user.email if user is not None else None


# ─── Same-month duplicate guard ───────────────────────────────────────────────


def _already_sent_this_month(ts) -> bool:
    if ts is None:
        return False
    try:
        if isinstance(ts, datetime):
            dt = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        else:
            return False
        now = datetime.now(timezone.utc)
        return dt.year == now.year and dt.month == now.month
    except Exception:
        return False


# ─── Email templates ──────────────────────────────────────────────────────────


def _warning_email(company_name: str, pct: float, remaining: float) -> str:
    return f"""
    <div style="font-family:sans-serif;max-width:520px;margin:auto;padding:32px;">
      <h2 style="color:#f39c12;">Credit Usage Warning</h2>
      <p>Hi,</p>
      <p>
        Your company <strong>{company_name}</strong> has used
        <strong>{pct:.1f}%</strong> of its monthly AI credit allowance on Diltak.
      </p>
      <p>You have <strong>${remaining:.4f}</strong> remaining for this month.</p>
      <p>
        If usage continues at the current rate you may hit your limit before
        the month ends. Consider reviewing feature usage or upgrading your plan.
      </p>
      <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
      <p style="color:#aaa;font-size:12px;">Diltak &middot; Mental Wellness for Teams</p>
    </div>
    """


def _critical_email(company_name: str, pct: float, remaining: float) -> str:
    return f"""
    <div style="font-family:sans-serif;max-width:520px;margin:auto;padding:32px;">
      <h2 style="color:#e74c3c;">Critical: Credit Limit Almost Reached</h2>
      <p>Hi,</p>
      <p>
        Your company <strong>{company_name}</strong> has used
        <strong>{pct:.1f}%</strong> of its monthly AI credit allowance.
      </p>
      <p>Only <strong>${remaining:.4f}</strong> remains this month.</p>
      <p>
        Please upgrade your plan or contact support to avoid service interruption.
      </p>
      <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
      <p style="color:#aaa;font-size:12px;">Diltak &middot; Mental Wellness for Teams</p>
    </div>
    """


def _limit_email(company_name: str) -> str:
    return f"""
    <div style="font-family:sans-serif;max-width:520px;margin:auto;padding:32px;">
      <h2 style="color:#c0392b;">Monthly Credit Limit Reached</h2>
      <p>Hi,</p>
      <p>
        <strong>{company_name}</strong> has reached its monthly AI credit limit on Diltak.
      </p>
      <p>
        AI-powered features may be degraded or paused until the limit resets or
        your plan is upgraded.
      </p>
      <p>Please contact support to increase your limit.</p>
      <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
      <p style="color:#aaa;font-size:12px;">Diltak &middot; Mental Wellness for Teams</p>
    </div>
    """


# ─── Main entry point ─────────────────────────────────────────────────────────


def check_and_alert(company_id, credit_data: dict, db=None) -> None:
    """Evaluate usage %, persist new alert_status, optionally send email."""
    try:
        if isinstance(company_id, str):
            try:
                company_uuid = uuid.UUID(company_id)
            except ValueError:
                return
        else:
            company_uuid = company_id

        pct = _usage_pct(credit_data)
        new_status = _new_status(pct)
        current_status = credit_data.get("alert_status", "normal")
        company_name = credit_data.get("company_name") or str(company_uuid)
        remaining = float(credit_data.get("credits_remaining") or 0)

        SessionLocal = get_session_factory()
        with SessionLocal() as session:
            row = (
                session.query(CompanyCredit)
                .filter(CompanyCredit.company_id == company_uuid)
                .one_or_none()
            )
            if row is None:
                return

            if new_status != current_status:
                row.alert_status = new_status
                session.commit()

            new_rank = _STATUS_RANK.get(new_status, 0)
            current_rank = _STATUS_RANK.get(current_status, 0)
            if new_rank <= current_rank and new_status == current_status:
                return

            employer_email = _get_employer_email(session, company_uuid)
            if not employer_email:
                return

            if new_status == "warning":
                if _already_sent_this_month(credit_data.get("last_warning_sent_at")):
                    return
                subject = f"Diltak credit usage warning - {pct:.0f}% of monthly limit used"
                send_email(employer_email, subject, _warning_email(company_name, pct, remaining))
                row.last_warning_sent_at = func.now()
                session.commit()
            elif new_status == "critical":
                if _already_sent_this_month(credit_data.get("last_critical_sent_at")):
                    return
                subject = f"Diltak credits critical - {pct:.0f}% used, only ${remaining:.2f} left"
                send_email(employer_email, subject, _critical_email(company_name, pct, remaining))
                row.last_critical_sent_at = func.now()
                session.commit()
            elif new_status == "limit_reached":
                if _already_sent_this_month(credit_data.get("last_critical_sent_at")):
                    return
                subject = f"Diltak monthly credit limit reached - {company_name}"
                send_email(employer_email, subject, _limit_email(company_name))
                row.last_critical_sent_at = func.now()
                session.commit()
    except Exception as e:
        print(f"[credit_alerts] check_and_alert error for {company_id}: {e}")
