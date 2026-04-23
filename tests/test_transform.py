"""Unit tests for migration.transform."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from migration.transform import (
    MissingRequiredField,
    coerce_uuid,
    split_known_and_extras,
    transform_company,
)


def test_split_known_and_extras():
    doc = {
        "name": "Acme",
        "owner_id": "u1",
        "settings": {"tier": "gold"},
        "employee_count": 5,
        "extra_field_1": "hi",
        "extra_field_2": 42,
    }
    known_cols = ["name", "owner_id", "settings", "employee_count"]
    known, extras = split_known_and_extras(doc, known_cols)
    assert known == {
        "name": "Acme",
        "owner_id": "u1",
        "settings": {"tier": "gold"},
        "employee_count": 5,
    }
    assert extras == {"extra_field_1": "hi", "extra_field_2": 42}


def test_coerce_uuid_accepts_uuid_string():
    s = "b4d7c422-f13c-4465-84fc-70b6e922b625"
    assert coerce_uuid(s) == uuid.UUID(s)


def test_coerce_uuid_passes_through_uuid_object():
    u = uuid.uuid4()
    assert coerce_uuid(u) is u


def test_coerce_uuid_returns_new_uuid_for_non_uuid_string():
    # Firestore doc IDs are arbitrary strings; when we need a UUID column and
    # the source ID isn't a UUID, generate a deterministic one from the ID.
    result = coerce_uuid("arbitrary-doc-id-abc")
    assert isinstance(result, uuid.UUID)
    # Deterministic: same input -> same output
    result2 = coerce_uuid("arbitrary-doc-id-abc")
    assert result == result2


def test_transform_company_basic():
    doc = {
        "name": "Acme",
        "owner_id": "user-abc",
        "employee_count": 10,
        "industry": "tech",  # extra — should go into settings
        "size_band": "50-100",  # extra
        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2024, 6, 1, tzinfo=timezone.utc),
    }
    row = transform_company("company-doc-id", doc)
    # id should be a uuid.UUID derived from the doc id
    assert isinstance(row["id"], uuid.UUID)
    assert row["name"] == "Acme"
    assert row["owner_id"] == "user-abc"
    assert row["employee_count"] == 10
    # Unknown fields merged into settings
    assert row["settings"] == {"industry": "tech", "size_band": "50-100"}
    assert row["created_at"] == datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_transform_company_preserves_existing_settings():
    doc = {
        "name": "Beta Co",
        "settings": {"theme": "dark"},
        "industry": "finance",  # extra — merges into settings
    }
    row = transform_company("cid", doc)
    assert row["settings"] == {"theme": "dark", "industry": "finance"}


def test_transform_company_missing_name_raises():
    with pytest.raises(MissingRequiredField):
        transform_company("cid", {"owner_id": "u1"})  # no 'name'
