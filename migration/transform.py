"""Transformers: Firestore doc dict → SQLAlchemy model kwargs.

Conventions:
- Every transformer takes ``(doc_id: str, doc: dict) -> dict``.
- Unknown fields are merged into the table's catch-all JSONB column where one
  exists; otherwise logged to stderr and dropped.
- Required fields missing from the source raise ``MissingRequiredField``.
- UUID primary keys for tables other than ``users`` are generated deterministically
  from the Firestore doc ID via UUIDv5 so re-running the ETL produces the same
  UUIDs and enables re-run safety via ``ON CONFLICT DO NOTHING`` upserts later.
"""

from __future__ import annotations

import sys
import uuid
from typing import Iterable, Tuple

from auth.password import hash_password


class MissingRequiredField(ValueError):
    """Raised when a required field is missing from a source document."""


# Namespace UUID for deterministic conversion of arbitrary doc IDs to UUIDs.
# Stable across runs; must not change or existing rows would be orphaned.
_NS = uuid.UUID("2f8b0f5a-1d8e-4a1a-9b00-000000000001")


# Every migrated user starts with this default password. Users are expected to
# change it on first login. Hashed once at module load (bcrypt is ~300 ms per
# call; one hash shared across all migrated rows is fine — salt doesn't add
# security when the plaintext is identical for everyone anyway).
DEFAULT_MIGRATED_PASSWORD = "11111111"
_DEFAULT_PASSWORD_HASH = hash_password(DEFAULT_MIGRATED_PASSWORD)


def coerce_uuid(value) -> uuid.UUID:
    """Convert a value to a uuid.UUID.

    - ``uuid.UUID`` -> returned as-is.
    - Valid UUID string -> parsed.
    - Anything else (including Firestore auto-IDs) -> UUIDv5 derived from str(value).
    """
    if isinstance(value, uuid.UUID):
        return value
    s = str(value)
    try:
        return uuid.UUID(s)
    except ValueError:
        return uuid.uuid5(_NS, s)


def split_known_and_extras(doc: dict, known_cols: Iterable[str]) -> Tuple[dict, dict]:
    """Return (known, extras) — `known` has only keys in ``known_cols``, `extras` has the rest."""
    known_set = set(known_cols)
    known = {k: v for k, v in doc.items() if k in known_set}
    extras = {k: v for k, v in doc.items() if k not in known_set}
    return known, extras


def apply_aliases(doc: dict, aliases: dict) -> dict:
    """Return a new dict with keys renamed per ``aliases`` (old_key -> new_key).

    If the target key already exists in ``doc``, the aliased value is skipped
    (source of truth wins). Missing alias keys are no-ops.
    """
    result = dict(doc)
    for old_key, new_key in aliases.items():
        if old_key in result and new_key not in result:
            result[new_key] = result.pop(old_key)
        elif old_key in result:
            # Target key already populated — drop the alias duplicate silently.
            result.pop(old_key)
    return result


def jsonable(value):
    """Recursively coerce a value so it can be stored in a JSONB column.

    Firestore's ``DatetimeWithNanoseconds`` (and plain ``datetime``) isn't
    natively JSON-serializable via psycopg2's adapter — convert to ISO strings.
    ``NaN`` / ``Infinity`` floats are invalid JSON per RFC 8259 and Postgres
    rejects them — coerce to ``None``. ``GeoPoint`` and unknown custom types
    become their ``repr()``.
    """
    from datetime import date, datetime
    import math

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    # Fallback: repr so we don't silently drop the value.
    return repr(value)


def _warn_dropped(table: str, doc_id: str, extras: dict) -> None:
    if extras:
        print(
            f"[WARN] dropped fields for {table}/{doc_id}: {sorted(extras.keys())}",
            file=sys.stderr,
        )


# ─── companies ────────────────────────────────────────────────────────────────

_COMPANY_COLS = {"name", "owner_id", "settings", "employee_count", "created_at", "updated_at"}
_COMPANY_REQUIRED = {"name"}


def transform_company(doc_id: str, doc: dict) -> dict:
    missing = _COMPANY_REQUIRED - doc.keys()
    if missing:
        raise MissingRequiredField(f"companies/{doc_id} missing: {sorted(missing)}")
    known, extras = split_known_and_extras(doc, _COMPANY_COLS)
    settings = dict(known.get("settings") or {})
    # Merge extras into settings.
    settings.update(extras)
    return {
        "id": coerce_uuid(doc_id),
        "name": known["name"],
        "owner_id": known.get("owner_id"),
        "settings": settings,
        "employee_count": int(known.get("employee_count") or 0),
        **({"created_at": known["created_at"]} if "created_at" in known else {}),
        **({"updated_at": known["updated_at"]} if "updated_at" in known else {}),
    }


# ─── users ────────────────────────────────────────────────────────────────────

_USER_COLS = {
    "email", "password_hash", "role", "company_id", "manager_id",
    "department", "is_active", "profile", "created_at", "updated_at",
}
_USER_REQUIRED = {"email"}
_USER_DROP_DENORMALIZED = {"direct_reports"}


def transform_user(doc_id: str, doc: dict) -> dict:
    missing = _USER_REQUIRED - doc.keys()
    if missing:
        raise MissingRequiredField(f"users/{doc_id} missing: {sorted(missing)}")
    # Drop known denormalized fields up-front so they don't hit `profile`.
    doc = {k: v for k, v in doc.items() if k not in _USER_DROP_DENORMALIZED}
    known, extras = split_known_and_extras(doc, _USER_COLS)

    profile = dict(known.get("profile") or {})
    profile.update(extras)

    company_id = known.get("company_id")
    if company_id is not None and not isinstance(company_id, uuid.UUID):
        company_id = coerce_uuid(company_id)

    return {
        "id": doc_id,  # preserve Firebase UID verbatim
        "email": known["email"],
        "password_hash": _DEFAULT_PASSWORD_HASH,  # default password "11111111" — users change on first login
        "role": known.get("role") or "employee",
        "company_id": company_id,
        "manager_id": known.get("manager_id"),
        "department": known.get("department"),
        "is_active": known.get("is_active") if "is_active" in known else True,
        "profile": profile,
        **({"created_at": known["created_at"]} if "created_at" in known and known["created_at"] is not None else {}),
        **({"updated_at": known["updated_at"]} if "updated_at" in known and known["updated_at"] is not None else {}),
    }


# ─── check_ins ────────────────────────────────────────────────────────────────

_CHECKIN_COLS = {"user_id", "company_id", "data", "created_at"}


def transform_check_in(doc_id: str, doc: dict) -> dict:
    known, extras = split_known_and_extras(doc, _CHECKIN_COLS)
    data = dict(known.get("data") or {})
    data.update(extras)
    cid = known.get("company_id")
    return {
        "id": coerce_uuid(doc_id),
        "user_id": known.get("user_id"),
        "company_id": coerce_uuid(cid) if cid else None,
        "data": data,
        **({"created_at": known["created_at"]} if "created_at" in known and known["created_at"] is not None else {}),
    }


# ─── sessions (mental-health chat sessions) ───────────────────────────────────

_MH_SESSION_COLS = {"user_id", "company_id", "messages", "summary", "created_at", "ended_at"}


def transform_mh_session(doc_id: str, doc: dict) -> dict:
    known, extras = split_known_and_extras(doc, _MH_SESSION_COLS)
    _warn_dropped("sessions", doc_id, extras)
    cid = known.get("company_id")
    return {
        "id": coerce_uuid(doc_id),
        "user_id": known.get("user_id"),
        "company_id": coerce_uuid(cid) if cid else None,
        "messages": known.get("messages") or [],
        "summary": known.get("summary"),
        **({"created_at": known["created_at"]} if "created_at" in known and known["created_at"] is not None else {}),
        **({"ended_at": known["ended_at"]} if "ended_at" in known and known["ended_at"] is not None else {}),
    }


# ─── mental_health_reports ────────────────────────────────────────────────────

_MHR_COLS = {"user_id", "company_id", "report", "risk_level", "generated_at"}
_MHR_ALIASES = {"employee_id": "user_id", "created_at": "generated_at"}


def transform_mental_health_report(doc_id: str, doc: dict) -> dict:
    doc = apply_aliases(doc, _MHR_ALIASES)
    known, extras = split_known_and_extras(doc, _MHR_COLS)
    report = dict(known.get("report") or {})
    report.update(extras)
    cid = known.get("company_id")
    return {
        "id": coerce_uuid(doc_id),
        "user_id": known.get("user_id"),
        "company_id": coerce_uuid(cid) if cid else None,
        "report": report,
        "risk_level": known.get("risk_level"),
        **({"generated_at": known["generated_at"]} if "generated_at" in known and known["generated_at"] is not None else {}),
    }


# ─── chat_sessions ────────────────────────────────────────────────────────────

_CS_COLS = {"user_id", "messages", "extras", "created_at", "updated_at"}
_CS_ALIASES = {"employee_id": "user_id"}


def transform_chat_session(doc_id: str, doc: dict) -> dict:
    doc = apply_aliases(doc, _CS_ALIASES)
    known, extras = split_known_and_extras(doc, _CS_COLS)
    # Unknown Firestore fields (e.g. report, company_id, status, session_type,
    # completed_at, duration, conversationData) land in the extras JSONB.
    merged_extras = dict(known.get("extras") or {})
    merged_extras.update(extras)
    return {
        "id": coerce_uuid(doc_id),
        "user_id": known.get("user_id"),
        "messages": known.get("messages") or [],
        "extras": merged_extras,
        **({"created_at": known["created_at"]} if "created_at" in known and known["created_at"] is not None else {}),
        **({"updated_at": known["updated_at"]} if "updated_at" in known and known["updated_at"] is not None else {}),
    }


# ─── ai_recommendations ───────────────────────────────────────────────────────

_AIR_COLS = {"user_id", "company_id", "recommendation", "category", "created_at"}
_AIR_ALIASES = {"employee_id": "user_id"}


def transform_ai_recommendation(doc_id: str, doc: dict) -> dict:
    doc = apply_aliases(doc, _AIR_ALIASES)
    known, extras = split_known_and_extras(doc, _AIR_COLS)
    rec = dict(known.get("recommendation") or {})
    rec.update(extras)
    cid = known.get("company_id")
    return {
        "id": coerce_uuid(doc_id),
        "user_id": known.get("user_id"),
        "company_id": coerce_uuid(cid) if cid else None,
        "recommendation": rec,
        "category": known.get("category"),
        **({"created_at": known["created_at"]} if "created_at" in known and known["created_at"] is not None else {}),
    }


# ─── interventions ────────────────────────────────────────────────────────────

_INT_COLS = {"company_id", "user_id", "data", "status", "created_at"}


def transform_intervention(doc_id: str, doc: dict) -> dict:
    known, extras = split_known_and_extras(doc, _INT_COLS)
    data = dict(known.get("data") or {})
    data.update(extras)
    cid = known.get("company_id")
    return {
        "id": coerce_uuid(doc_id),
        "company_id": coerce_uuid(cid) if cid else None,
        "user_id": known.get("user_id"),
        "data": data,
        "status": known.get("status"),
        **({"created_at": known["created_at"]} if "created_at" in known and known["created_at"] is not None else {}),
    }


# ─── escalation_tickets ───────────────────────────────────────────────────────

_ESC_COLS = {
    "company_id", "user_id", "assigned_to", "status", "priority",
    "data", "created_at", "updated_at",
}


def transform_escalation_ticket(doc_id: str, doc: dict) -> dict:
    known, extras = split_known_and_extras(doc, _ESC_COLS)
    data = dict(known.get("data") or {})
    data.update(extras)
    cid = known.get("company_id")
    return {
        "id": coerce_uuid(doc_id),
        "company_id": coerce_uuid(cid) if cid else None,
        "user_id": known.get("user_id"),
        "assigned_to": known.get("assigned_to"),
        "status": known.get("status"),
        "priority": known.get("priority"),
        "data": data,
        **({"created_at": known["created_at"]} if "created_at" in known and known["created_at"] is not None else {}),
        **({"updated_at": known["updated_at"]} if "updated_at" in known and known["updated_at"] is not None else {}),
    }


# ─── physical_health_checkins ─────────────────────────────────────────────────

_PHC_COLS = {"user_id", "company_id", "vitals", "symptoms", "created_at"}


def transform_physical_health_checkin(doc_id: str, doc: dict) -> dict:
    known, extras = split_known_and_extras(doc, _PHC_COLS)
    vitals = dict(known.get("vitals") or {})
    vitals.update(extras)
    cid = known.get("company_id")
    return {
        "id": coerce_uuid(doc_id),
        "user_id": known.get("user_id"),
        "company_id": coerce_uuid(cid) if cid else None,
        "vitals": vitals,
        "symptoms": known.get("symptoms") or {},
        **({"created_at": known["created_at"]} if "created_at" in known and known["created_at"] is not None else {}),
    }


# ─── physical_health_reports ──────────────────────────────────────────────────

_PHR_COLS = {"user_id", "company_id", "report", "generated_at"}


def transform_physical_health_report(doc_id: str, doc: dict) -> dict:
    known, extras = split_known_and_extras(doc, _PHR_COLS)
    report = dict(known.get("report") or {})
    report.update(extras)
    cid = known.get("company_id")
    return {
        "id": coerce_uuid(doc_id),
        "user_id": known.get("user_id"),
        "company_id": coerce_uuid(cid) if cid else None,
        "report": report,
        **({"generated_at": known["generated_at"]} if "generated_at" in known and known["generated_at"] is not None else {}),
    }


# ─── medical_documents ────────────────────────────────────────────────────────

_MD_COLS = {
    "user_id", "filename", "blob_url", "mime_type", "size_bytes",
    "extracted_text", "uploaded_at",
}


def transform_medical_document(doc_id: str, doc: dict) -> dict:
    known, extras = split_known_and_extras(doc, _MD_COLS)
    _warn_dropped("medical_documents", doc_id, extras)
    return {
        "id": coerce_uuid(doc_id),
        "user_id": known.get("user_id"),
        "filename": known.get("filename") or "",
        "blob_url": known.get("blob_url") or "",
        "mime_type": known.get("mime_type"),
        "size_bytes": known.get("size_bytes"),
        "extracted_text": known.get("extracted_text"),
        **({"uploaded_at": known["uploaded_at"]} if "uploaded_at" in known and known["uploaded_at"] is not None else {}),
    }


# ─── wellness_events ──────────────────────────────────────────────────────────

_WE_COLS = {"user_id", "company_id", "event_type", "data", "created_at"}


def transform_wellness_event(doc_id: str, doc: dict) -> dict:
    known, extras = split_known_and_extras(doc, _WE_COLS)
    data = dict(known.get("data") or {})
    data.update(extras)
    cid = known.get("company_id")
    return {
        "id": coerce_uuid(doc_id),
        "user_id": known.get("user_id"),
        "company_id": coerce_uuid(cid) if cid else None,
        "event_type": known.get("event_type"),
        "data": data,
        **({"created_at": known["created_at"]} if "created_at" in known and known["created_at"] is not None else {}),
    }


# ─── anonymous_profiles ───────────────────────────────────────────────────────

_AP_COLS = {"user_id", "handle", "avatar", "extras", "created_at"}
_AP_ALIASES = {
    "employee_id": "user_id",
    "anonymous_id": "handle",  # legacy field name for the user's handle
    "avatar_color": "avatar",  # legacy: stored color string as avatar
}


def transform_anonymous_profile(doc_id: str, doc: dict) -> dict:
    doc = apply_aliases(doc, _AP_ALIASES)
    known, extras = split_known_and_extras(doc, _AP_COLS)
    merged_extras = dict(known.get("extras") or {})
    merged_extras.update(extras)
    return {
        "id": coerce_uuid(doc_id),
        "user_id": known.get("user_id"),
        "handle": known.get("handle") or doc_id,
        "avatar": known.get("avatar"),
        "extras": merged_extras,
        **({"created_at": known["created_at"]} if "created_at" in known and known["created_at"] is not None else {}),
    }


# ─── community_posts ──────────────────────────────────────────────────────────

_CP_COLS = {
    "company_id", "anonymous_profile_id", "content", "likes", "replies",
    "is_approved", "extras", "created_at",
}
# Note: Firestore community posts use `author_id` = Firebase UID of the real
# user. Our schema uses `anonymous_profile_id` (UUID FK to anonymous_profiles).
# We preserve author_id in `extras.author_id`; a post-ETL linking step can
# resolve it to the anonymous_profile_id via users.id → anonymous_profiles.user_id.
_CP_ALIASES = {}


def transform_community_post(doc_id: str, doc: dict) -> dict:
    doc = apply_aliases(doc, _CP_ALIASES)
    known, extras = split_known_and_extras(doc, _CP_COLS)
    merged_extras = dict(known.get("extras") or {})
    merged_extras.update(extras)
    cid = known.get("company_id")
    apid = known.get("anonymous_profile_id")
    return {
        "id": coerce_uuid(doc_id),
        "company_id": coerce_uuid(cid) if cid else None,
        "anonymous_profile_id": coerce_uuid(apid) if apid else None,
        "content": known.get("content") or "",
        "likes": int(known.get("likes") or 0),
        "replies": int(known.get("replies") or 0),
        "is_approved": known.get("is_approved") if "is_approved" in known else True,
        "extras": merged_extras,
        **({"created_at": known["created_at"]} if "created_at" in known and known["created_at"] is not None else {}),
    }


# ─── community_replies ────────────────────────────────────────────────────────

_CR_COLS = {"post_id", "anonymous_profile_id", "content", "is_approved", "extras", "created_at"}
# Same as community_posts — `author_id` is a Firebase UID, preserved in extras.
_CR_ALIASES = {}


def transform_community_reply(doc_id: str, doc: dict) -> dict:
    doc = apply_aliases(doc, _CR_ALIASES)
    known, extras = split_known_and_extras(doc, _CR_COLS)
    merged_extras = dict(known.get("extras") or {})
    merged_extras.update(extras)
    pid = known.get("post_id")
    apid = known.get("anonymous_profile_id")
    return {
        "id": coerce_uuid(doc_id),
        "post_id": coerce_uuid(pid) if pid else None,
        "anonymous_profile_id": coerce_uuid(apid) if apid else None,
        "content": known.get("content") or "",
        "is_approved": known.get("is_approved") if "is_approved" in known else True,
        "extras": merged_extras,
        **({"created_at": known["created_at"]} if "created_at" in known and known["created_at"] is not None else {}),
    }


# ─── user_gamification ────────────────────────────────────────────────────────

_UG_COLS = {
    "user_id", "company_id", "points", "level", "badges", "streak",
    "extras", "updated_at",
}
_UG_ALIASES = {
    "employee_id": "user_id",
    "total_points": "points",
    "current_streak": "streak",
}


def transform_user_gamification(doc_id: str, doc: dict) -> dict:
    doc = apply_aliases(doc, _UG_ALIASES)
    known, extras = split_known_and_extras(doc, _UG_COLS)
    # Unknown fields (longest_streak, last_check_in, monthly_goal, weekly_goal,
    # challenges_completed, created_at) land in extras.
    merged_extras = dict(known.get("extras") or {})
    merged_extras.update(extras)
    cid = known.get("company_id")
    return {
        "id": coerce_uuid(doc_id),
        "user_id": known.get("user_id"),
        "company_id": coerce_uuid(cid) if cid else None,
        "points": int(known.get("points") or 0),
        "level": int(known.get("level") or 1),
        "badges": list(known.get("badges") or []),
        "streak": int(known.get("streak") or 0),
        "extras": merged_extras,
        **({"updated_at": known["updated_at"]} if "updated_at" in known and known["updated_at"] is not None else {}),
    }


# ─── wellness_challenges ──────────────────────────────────────────────────────

_WC_COLS = {
    "company_id", "title", "description", "is_active", "data",
    "starts_at", "ends_at", "created_at",
}


def transform_wellness_challenge(doc_id: str, doc: dict) -> dict:
    known, extras = split_known_and_extras(doc, _WC_COLS)
    data = dict(known.get("data") or {})
    data.update(extras)
    cid = known.get("company_id")
    return {
        "id": coerce_uuid(doc_id),
        "company_id": coerce_uuid(cid) if cid else None,
        "title": known.get("title") or "",
        "description": known.get("description"),
        "is_active": known.get("is_active") if "is_active" in known else True,
        "data": data,
        **({"starts_at": known["starts_at"]} if "starts_at" in known and known["starts_at"] is not None else {}),
        **({"ends_at": known["ends_at"]} if "ends_at" in known and known["ends_at"] is not None else {}),
        **({"created_at": known["created_at"]} if "created_at" in known and known["created_at"] is not None else {}),
    }


# ─── calls ────────────────────────────────────────────────────────────────────

_CALL_COLS = {
    "caller_id", "callee_id", "status", "start_time", "answered_at",
    "end_time", "end_reason", "ended_by", "extras", "created_at", "updated_at",
}
# Firestore voice-calls stored data in camelCase and used `receiverId` for
# the callee — normalise to snake_case + the schema's `callee_id` name.
_CALL_ALIASES = {
    "callerId": "caller_id",
    "calleeId": "callee_id",
    "receiverId": "callee_id",
    "startTime": "start_time",
    "answeredAt": "answered_at",
    "endTime": "end_time",
    "endReason": "end_reason",
    "endedBy": "ended_by",
    "createdAt": "created_at",
    "updatedAt": "updated_at",
}


def transform_call(doc_id: str, doc: dict) -> dict:
    doc = apply_aliases(doc, _CALL_ALIASES)
    known, extras = split_known_and_extras(doc, _CALL_COLS)
    # Stuff remaining camelCase fields (e.g. callType, metadata) into extras.
    merged_extras = dict(known.get("extras") or {})
    merged_extras.update(extras)
    return {
        "id": coerce_uuid(doc_id),
        "caller_id": known.get("caller_id"),
        "callee_id": known.get("callee_id"),
        "status": known.get("status"),
        "start_time": known.get("start_time"),
        "answered_at": known.get("answered_at"),
        "end_time": known.get("end_time"),
        "end_reason": known.get("end_reason"),
        "ended_by": known.get("ended_by"),
        "extras": merged_extras,
        **({"created_at": known["created_at"]} if "created_at" in known and known["created_at"] is not None else {}),
        **({"updated_at": known["updated_at"]} if "updated_at" in known and known["updated_at"] is not None else {}),
    }


# ─── call_sessions ────────────────────────────────────────────────────────────

_CALLSESS_COLS = {"call_id", "status", "metadata", "created_at", "updated_at"}


def transform_call_session(doc_id: str, doc: dict) -> dict:
    known, extras = split_known_and_extras(doc, _CALLSESS_COLS)
    call_meta = dict(known.get("metadata") or {})
    call_meta.update(extras)
    # Firestore's callSessions docs use the parent call's ID as their own
    # doc_id (no separate call_id field). Fall back to doc_id → UUIDv5,
    # which matches how `transform_call` coerces the same source string.
    call_id_src = known.get("call_id") or doc_id
    # NOTE: the DB column is named "metadata" even though the Python attribute
    # on the ORM model is "call_metadata" (SQLAlchemy reserves the attr name
    # `metadata`). For Core inserts we use the column name.
    return {
        "id": coerce_uuid(doc_id),
        "call_id": coerce_uuid(call_id_src),
        "status": known.get("status"),
        "metadata": call_meta,
        **({"created_at": known["created_at"]} if "created_at" in known and known["created_at"] is not None else {}),
        **({"updated_at": known["updated_at"]} if "updated_at" in known and known["updated_at"] is not None else {}),
    }


# ─── import_jobs ──────────────────────────────────────────────────────────────

_IJ_COLS = {
    "company_id", "created_by", "status", "stats", "errors", "blob_url",
    "created_at", "updated_at",
}


def transform_import_job(doc_id: str, doc: dict) -> dict:
    known, extras = split_known_and_extras(doc, _IJ_COLS)
    stats = dict(known.get("stats") or {})
    stats.update(extras)
    cid = known.get("company_id")
    return {
        "id": coerce_uuid(doc_id),
        "company_id": coerce_uuid(cid) if cid else None,
        "created_by": known.get("created_by"),
        "status": known.get("status"),
        "stats": stats,
        "errors": known.get("errors") or [],
        "blob_url": known.get("blob_url"),
        **({"created_at": known["created_at"]} if "created_at" in known and known["created_at"] is not None else {}),
        **({"updated_at": known["updated_at"]} if "updated_at" in known and known["updated_at"] is not None else {}),
    }
