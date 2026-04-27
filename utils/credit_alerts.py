"""
Credit Alert System
====================
Checks credit thresholds after every credit update and sends email
alerts when a company crosses a new threshold.

Alert levels:
    normal       →  < 80 % consumed        — no action
    warning      →  80–95 % consumed       — email to company admin
    critical     →  95–100 % consumed      — email to company admin
    limit_reached → ≥ 100 % consumed       — email to company admin

De-duplication:
    Emails are only sent when the alert_status *changes* to a higher level.
    last_warning_sent_at / last_critical_sent_at prevent repeat sends within
    the same calendar month.

Called from middleware/usage_tracker.py (inside daemon thread — never blocks).
"""

from datetime import datetime, timezone
from typing import Optional

from google.cloud.firestore_v1 import SERVER_TIMESTAMP

from utils.email import _send as send_email


# ─── Threshold boundaries ─────────────────────────────────────────────────────

def _usage_pct(data: dict) -> float:
    limit = data.get("credit_limit_usd", 10.0)
    if limit <= 0:
        return 0.0
    consumed = data.get("credits_consumed_mtd", 0.0)
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

def _get_employer_email(company_id: str, db) -> Optional[str]:
    """Return the email of the first employer-role user in this company."""
    try:
        docs = (
            db.collection("users")
            .where("company_id", "==", company_id)
            .where("role", "==", "employer")
            .limit(1)
            .stream()
        )
        for doc in docs:
            return doc.to_dict().get("email")
    except Exception:
        pass
    return None


# ─── Email templates ─────────────────────────────────────────────────────────

def _warning_email(company_name: str, pct: float, remaining: float) -> str:
    return f"""
    <div style="font-family:sans-serif;max-width:520px;margin:auto;padding:32px;">
      <h2 style="color:#f39c12;">Credit Usage Warning ⚠️</h2>
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
      <p style="color:#aaa;font-size:12px;">Diltak · Mental Wellness for Teams</p>
    </div>
    """


def _critical_email(company_name: str, pct: float, remaining: float) -> str:
    return f"""
    <div style="font-family:sans-serif;max-width:520px;margin:auto;padding:32px;">
      <h2 style="color:#e74c3c;">Critical: Credit Limit Almost Reached 🚨</h2>
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
      <p style="color:#aaa;font-size:12px;">Diltak · Mental Wellness for Teams</p>
    </div>
    """


def _limit_email(company_name: str) -> str:
    return f"""
    <div style="font-family:sans-serif;max-width:520px;margin:auto;padding:32px;">
      <h2 style="color:#c0392b;">Monthly Credit Limit Reached 🛑</h2>
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
      <p style="color:#aaa;font-size:12px;">Diltak · Mental Wellness for Teams</p>
    </div>
    """


# ─── Same-month duplicate guard ───────────────────────────────────────────────

def _already_sent_this_month(ts) -> bool:
    """True if the timestamp falls in the current calendar month."""
    if ts is None:
        return False
    try:
        if hasattr(ts, "timestamp"):
            dt = datetime.fromtimestamp(ts.timestamp(), tz=timezone.utc)
        elif isinstance(ts, datetime):
            dt = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        else:
            return False
        now = datetime.now(timezone.utc)
        return dt.year == now.year and dt.month == now.month
    except Exception:
        return False


# ─── Main entry point ─────────────────────────────────────────────────────────

def check_and_alert(company_id: str, credit_data: dict, db) -> None:
    """
    Evaluate current usage percentage, update alert_status in Firestore,
    and send an email if the company has crossed into a new alert tier.

    Called from credit_manager.update_company_credits() — already in a
    daemon thread, so any Firestore writes / email sends are non-blocking
    from the request's perspective.
    """
    try:
        pct            = _usage_pct(credit_data)
        new_status     = _new_status(pct)
        current_status = credit_data.get("alert_status", "normal")
        company_name   = credit_data.get("company_name") or company_id
        remaining      = credit_data.get("credits_remaining", 0.0)

        # Update alert_status in Firestore whenever it changes
        if new_status != current_status:
            db.collection("company_credits").document(company_id).update({
                "alert_status": new_status,
                "updated_at":   SERVER_TIMESTAMP,
            })

        new_rank     = _STATUS_RANK.get(new_status, 0)
        current_rank = _STATUS_RANK.get(current_status, 0)

        # Only escalate — never spam on the same or lower level
        if new_rank <= current_rank and new_status == current_status:
            return

        employer_email = _get_employer_email(company_id, db)
        if not employer_email:
            return

        ref = db.collection("company_credits").document(company_id)

        if new_status == "warning":
            if _already_sent_this_month(credit_data.get("last_warning_sent_at")):
                return
            subject = f"Diltak credit usage warning — {pct:.0f}% of monthly limit used"
            html    = _warning_email(company_name, pct, remaining)
            send_email(employer_email, subject, html)
            ref.update({"last_warning_sent_at": SERVER_TIMESTAMP})

        elif new_status == "critical":
            if _already_sent_this_month(credit_data.get("last_critical_sent_at")):
                return
            subject = f"Diltak credits critical — {pct:.0f}% used, only ${remaining:.2f} left"
            html    = _critical_email(company_name, pct, remaining)
            send_email(employer_email, subject, html)
            ref.update({"last_critical_sent_at": SERVER_TIMESTAMP})

        elif new_status == "limit_reached":
            # Piggyback on last_critical_sent_at for limit_reached dedup
            if _already_sent_this_month(credit_data.get("last_critical_sent_at")):
                return
            subject = f"Diltak monthly credit limit reached — {company_name}"
            html    = _limit_email(company_name)
            send_email(employer_email, subject, html)
            ref.update({"last_critical_sent_at": SERVER_TIMESTAMP})

    except Exception as e:
        print(f"[credit_alerts] check_and_alert error for {company_id}: {e}")
