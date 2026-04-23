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


class MissingRequiredField(ValueError):
    """Raised when a required field is missing from a source document."""


# Namespace UUID for deterministic conversion of arbitrary doc IDs to UUIDs.
# Stable across runs; must not change or existing rows would be orphaned.
_NS = uuid.UUID("2f8b0f5a-1d8e-4a1a-9b00-000000000001")


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
