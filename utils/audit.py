"""Audit logger (Postgres).

Writes one row to ``audit_logs`` per admin/employer mutation. Fire-and-forget —
spawns a daemon thread so the request path is never blocked.

Action constants used by callers (kept stable for analytics queries):

    user.create / user.update / user.deactivate / user.reactivate
    user.delete / user.password_reset
    company.update
    employer.deactivate / employer.reactivate / employer.delete
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Dict, Optional

from db.models import AuditLog
from db.session import get_session_factory


def _to_uuid(value) -> Optional[uuid.UUID]:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def log_audit(
    actor_uid: str,
    actor_role: str,
    action: str,
    company_id: str,
    db=None,  # accepted for backward-compat with the Firestore-era signature
    target_uid: Optional[str] = None,
    target_type: str = "user",
    metadata: Optional[Dict[str, Any]] = None,
    success: bool = True,
) -> None:
    """Fire-and-forget: write one audit_log row. Spawns a daemon thread."""
    company_uuid = _to_uuid(company_id)

    def _write():
        try:
            SessionLocal = get_session_factory()
            with SessionLocal() as session:
                session.add(
                    AuditLog(
                        actor_uid=actor_uid or "unknown",
                        actor_role=actor_role or "unknown",
                        action=action,
                        target_uid=target_uid,
                        target_type=target_type,
                        company_id=company_uuid,
                        audit_metadata=metadata or {},
                        success=success,
                    )
                )
                session.commit()
        except Exception as e:
            print(f"[audit] write error ({action}): {e}")

    threading.Thread(target=_write, daemon=True).start()
