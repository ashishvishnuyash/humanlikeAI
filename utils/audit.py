"""
Audit Logger
=============
Fire-and-forget audit trail for admin and employer actions.

Writes one record to Firestore `audit_logs` per mutation.
All writes happen in a daemon thread — never blocks the request path.

Firestore schema — audit_logs/{auto_id}:
{
    actor_uid:    str,          # uid of the user who performed the action
    actor_role:   str,          # "employer" | "hr" | "super_admin"
    action:       str,          # see ACTION CONSTANTS below
    target_uid:   str | null,   # uid of the affected user (null for company actions)
    target_type:  str,          # "user" | "company" | "employer"
    company_id:   str,          # scoping — company that owns the target
    metadata:     dict,         # extra context (changed_fields, plan_tier, etc.)
    timestamp:    Timestamp,
    success:      bool,
}

ACTION CONSTANTS
----------------
  user.create           employee or employer created
  user.update           profile fields changed
  user.deactivate       account soft-disabled
  user.reactivate       account re-enabled
  user.delete           hard-delete
  user.password_reset   forced password change
  company.update        company document patched
  employer.deactivate   employer account disabled
  employer.reactivate   employer account re-enabled
  employer.delete       employer hard-deleted
"""

import threading
from typing import Any, Dict, Optional

from google.cloud.firestore_v1 import SERVER_TIMESTAMP


def log_audit(
    actor_uid:   str,
    actor_role:  str,
    action:      str,
    company_id:  str,
    db,
    target_uid:  Optional[str]        = None,
    target_type: str                  = "user",
    metadata:    Optional[Dict[str, Any]] = None,
    success:     bool                 = True,
) -> None:
    """
    Fire-and-forget: write one audit_log record to Firestore.
    Spawns a daemon thread — returns immediately, never raises.
    """
    if not db:
        return

    def _write():
        try:
            db.collection("audit_logs").add({
                "actor_uid":   actor_uid  or "unknown",
                "actor_role":  actor_role or "unknown",
                "action":      action,
                "target_uid":  target_uid,
                "target_type": target_type,
                "company_id":  company_id or "",
                "metadata":    metadata or {},
                "timestamp":   SERVER_TIMESTAMP,
                "success":     success,
            })
        except Exception as e:
            print(f"[audit] write error ({action}): {e}")

    threading.Thread(target=_write, daemon=True).start()
