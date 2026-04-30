"""Activity tracker middleware (Postgres).

Two responsibilities, both fire-and-forget so the request is never blocked:

1. ``update_last_active(uid)`` — stamps ``users.last_active_at = NOW()`` on
   every authenticated request (called from ``auth.deps.get_current_user``).

2. ``persist_chat_session(...)`` — async upsert of the ``chat_sessions`` row
   for a given session_id. Stores user/company/message-count and bookkeeping
   timestamps in the row's ``extras`` JSONB.

Exceptions are swallowed — auth and chat must never fail because tracking failed.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from sqlalchemy import func

from db.models import ChatSession, User
from db.session import get_session_factory


def _to_uuid(value) -> uuid.UUID | None:
    """Best-effort string -> UUID. Returns None on parse failure."""
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


# ─── last_active_at ───────────────────────────────────────────────────────────


def update_last_active(uid: str) -> None:
    """Non-blocking stamp of ``users.last_active_at``. Spawns a daemon thread."""

    def _write():
        try:
            SessionLocal = get_session_factory()
            with SessionLocal() as session:
                session.query(User).filter(User.id == uid).update(
                    {"last_active_at": func.now()}
                )
                session.commit()
        except Exception:
            # Non-fatal — never block auth.
            pass

    threading.Thread(target=_write, daemon=True).start()


# ─── Chat session persistence ─────────────────────────────────────────────────


async def persist_chat_session(
    session_id: str,
    user_id: str,
    company_id: str,
    message_count: int,
    db=None,  # accepted for backward-compat with the Firebase-era signature
) -> None:
    """Upsert the ``chat_sessions`` row for ``session_id``.

    Bookkeeping fields (started_at, last_message_at, message_count, total_tokens,
    is_active, company_id, user_id-as-string, anonymous flag) live in the row's
    ``extras`` JSONB so we don't widen the schema for tracking-only fields.

    Called via ``asyncio.create_task()`` — must not raise.
    """
    try:
        sid = _to_uuid(session_id)
        if sid is None:
            return  # Can't track a session whose id isn't a UUID

        SessionLocal = get_session_factory()
        with SessionLocal() as session:
            row = (
                session.query(ChatSession)
                .filter(ChatSession.id == sid)
                .one_or_none()
            )
            now_iso = datetime.now(timezone.utc).isoformat()
            if row is None:
                # Anonymous /chat sessions don't have a real user yet —
                # ChatSession.user_id is NOT NULL, so anonymous sessions
                # are recorded only in extras (not the row), and skipped here
                # if the user can't be linked.
                if not user_id or user_id == "anonymous":
                    return
                new_extras = {
                    "started_at": now_iso,
                    "last_message_at": now_iso,
                    "message_count": message_count,
                    "total_tokens": 0,
                    "is_active": True,
                    "company_id": company_id or "",
                }
                row = ChatSession(
                    id=sid,
                    user_id=user_id,
                    messages=[],
                    extras=new_extras,
                )
                session.add(row)
            else:
                merged = dict(row.extras or {})
                merged["last_message_at"] = now_iso
                merged["message_count"] = message_count
                merged["is_active"] = True
                row.extras = merged
            session.commit()
    except Exception as e:
        print(f"[activity_tracker] persist_chat_session error for {session_id}: {e}")
