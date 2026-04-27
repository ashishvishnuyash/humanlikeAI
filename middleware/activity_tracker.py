"""
Activity Tracker Middleware
============================
Two responsibilities:

1. update_last_active(uid)
   Fire-and-forget thread that stamps users/{uid}.last_active_at on every
   authenticated request. Called from get_current_user in routers/auth.py.
   Never raises — Firestore errors are silently swallowed so auth is never
   blocked by a tracking write.

2. persist_chat_session(...)
   Async upsert for chat_sessions/{session_id}.
   Called via asyncio.create_task() from the /chat endpoint in main.py
   so it never blocks the response.

Firestore schema — chat_sessions/{session_id}:
{
    session_id:       str,
    user_id:          str,       # uid or "anonymous"
    company_id:       str,       # empty string if unknown
    started_at:       Timestamp,
    last_message_at:  Timestamp,
    message_count:    int,
    total_tokens:     int,       # updated by usage_tracker in Step 2
    is_active:        bool
}
"""

import threading
from datetime import datetime, timezone

from firebase_config import get_db
from google.cloud.firestore_v1 import SERVER_TIMESTAMP


# ─── last_active_at ───────────────────────────────────────────────────────────

def update_last_active(uid: str) -> None:
    """
    Non-blocking stamp of users/{uid}.last_active_at.
    Spawns a daemon thread — returns immediately, never raises.
    """
    def _write():
        try:
            db = get_db()
            if db:
                db.collection("users").document(uid).update({
                    "last_active_at": SERVER_TIMESTAMP,
                })
        except Exception:
            pass  # Non-fatal — never block auth

    threading.Thread(target=_write, daemon=True).start()


# ─── Chat session persistence ─────────────────────────────────────────────────

async def persist_chat_session(
    session_id:    str,
    user_id:       str,
    company_id:    str,
    message_count: int,
    db,
) -> None:
    """
    Upsert chat_sessions/{session_id}.
    - First message  → creates the document (started_at = now)
    - Subsequent     → updates last_message_at + message_count

    Called with asyncio.create_task() — must not raise.
    total_tokens is left at 0 here; usage_tracker (Step 2) updates it.
    """
    if not db:
        return
    try:
        ref = db.collection("chat_sessions").document(session_id)
        doc = ref.get()
        if doc.exists:
            ref.update({
                "last_message_at": SERVER_TIMESTAMP,
                "message_count":   message_count,
                "is_active":       True,
            })
        else:
            ref.set({
                "session_id":      session_id,
                "user_id":         user_id,
                "company_id":      company_id,
                "started_at":      SERVER_TIMESTAMP,
                "last_message_at": SERVER_TIMESTAMP,
                "message_count":   message_count,
                "total_tokens":    0,
                "is_active":       True,
            })
    except Exception as e:
        print(f"[activity_tracker] persist_chat_session error for {session_id}: {e}")
