"""
Company Credit Manager
=======================
Maintains per-company credit balance in Firestore.

Called after every usage_log write (inside the tracker's daemon thread)
to keep credits_consumed_mtd and credits_remaining in sync.

Firestore schema — company_credits/{company_id}:
{
    company_id:               str,
    company_name:             str,
    plan_tier:                str,      # "free" | "starter" | "pro" | "enterprise"
    credit_limit_usd:         float,    # monthly spend cap
    credits_consumed_mtd:     float,    # month-to-date spend
    credits_remaining:        float,    # limit - consumed  (may go negative)
    warning_threshold_pct:    float,    # default 80.0
    alert_status:             str,      # "normal" | "warning" | "critical" | "limit_reached"
    last_reset_at:            Timestamp,
    last_warning_sent_at:     Timestamp | null,
    last_critical_sent_at:    Timestamp | null,
    total_lifetime_spend_usd: float,
    updated_at:               Timestamp
}

Default credit limits by plan tier:
    free:       $10 / month
    starter:    $50 / month
    pro:        $200 / month
    enterprise: $1 000 / month
"""

from datetime import datetime, timezone
from typing import Optional

from firebase_admin import firestore as admin_firestore
from google.cloud.firestore_v1 import SERVER_TIMESTAMP

_Increment = admin_firestore.firestore.Increment

# Default monthly credit limits per plan tier (USD)
_PLAN_LIMITS: dict[str, float] = {
    "free":       10.0,
    "starter":    50.0,
    "pro":        200.0,
    "enterprise": 1000.0,
}


def _default_doc(company_id: str, plan_tier: str = "free") -> dict:
    limit = _PLAN_LIMITS.get(plan_tier, 10.0)
    return {
        "company_id":               company_id,
        "company_name":             "",
        "plan_tier":                plan_tier,
        "credit_limit_usd":         limit,
        "credits_consumed_mtd":     0.0,
        "credits_remaining":        limit,
        "warning_threshold_pct":    80.0,
        "alert_status":             "normal",
        "last_reset_at":            SERVER_TIMESTAMP,
        "last_warning_sent_at":     None,
        "last_critical_sent_at":    None,
        "total_lifetime_spend_usd": 0.0,
        "updated_at":               SERVER_TIMESTAMP,
    }


def _get_or_create(company_id: str, db) -> tuple[object, dict]:
    """Return (doc_ref, data_dict). Creates with free-tier defaults if missing."""
    ref = db.collection("company_credits").document(company_id)
    snap = ref.get()
    if snap.exists:
        return ref, snap.to_dict()

    # Lazy creation — try to pull plan_tier from companies collection
    plan_tier = "free"
    try:
        co = db.collection("companies").document(company_id).get()
        if co.exists:
            plan_tier = co.to_dict().get("plan_tier", "free")
    except Exception:
        pass

    defaults = _default_doc(company_id, plan_tier)
    ref.set(defaults)
    return ref, defaults


def update_company_credits(company_id: str, cost_usd: float, db) -> Optional[dict]:
    """
    Atomically add cost_usd to credits_consumed_mtd and subtract from
    credits_remaining. Lazily creates the document if absent.

    Returns the updated data dict so the caller can check alert_status.
    Returns None on any error (non-fatal).
    """
    if not company_id or not db or cost_usd <= 0:
        return None

    try:
        ref, data = _get_or_create(company_id, db)

        ref.update({
            "credits_consumed_mtd":     _Increment(cost_usd),
            "credits_remaining":        _Increment(-cost_usd),
            "total_lifetime_spend_usd": _Increment(cost_usd),
            "updated_at":               SERVER_TIMESTAMP,
        })

        # Re-read for fresh totals (needed to determine alert threshold)
        updated = ref.get().to_dict()
        return updated

    except Exception as e:
        print(f"[credit_manager] update error for {company_id}: {e}")
        return None


def reset_monthly_credits(company_id: str, db) -> None:
    """
    Reset MTD consumption to zero at the start of a new billing month.
    Called by a scheduled job (e.g. Cloud Scheduler on the 1st of each month).
    """
    try:
        ref = db.collection("company_credits").document(company_id)
        snap = ref.get()
        if not snap.exists:
            return
        d = snap.to_dict()
        limit = d.get("credit_limit_usd", 10.0)
        ref.update({
            "credits_consumed_mtd": 0.0,
            "credits_remaining":    limit,
            "alert_status":         "normal",
            "last_reset_at":        SERVER_TIMESTAMP,
            "updated_at":           SERVER_TIMESTAMP,
        })
    except Exception as e:
        print(f"[credit_manager] reset error for {company_id}: {e}")
