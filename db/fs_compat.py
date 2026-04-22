"""Helpers for translating Firestore idioms to SQLAlchemy.

Kept small on purpose — most Firestore patterns have a direct SQLAlchemy
equivalent that reads fine inline. These helpers cover only the repetitive
bits (model-to-dict serialization with UUID stringification).
"""

from __future__ import annotations

import uuid
from typing import Any


def model_to_dict(obj: Any) -> dict:
    """Return a dict of column name -> value for a SQLAlchemy model instance.

    UUID values are stringified so the result is JSON-friendly. datetime
    values pass through unchanged (FastAPI / Pydantic handles them).
    """
    result: dict = {}
    for col in obj.__table__.columns:
        value = getattr(obj, col.name)
        if isinstance(value, uuid.UUID):
            value = str(value)
        result[col.name] = value
    return result
