"""Unit tests for db.fs_compat helpers."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from db.fs_compat import model_to_dict
from db.models import Company


def test_model_to_dict_stringifies_uuid():
    cid = uuid.uuid4()
    co = Company(id=cid, name="Acme", settings={"tier": "gold"}, employee_count=5)
    d = model_to_dict(co)
    assert d["id"] == str(cid)
    assert d["name"] == "Acme"
    assert d["settings"] == {"tier": "gold"}
    assert d["employee_count"] == 5


def test_model_to_dict_passes_through_datetimes():
    cid = uuid.uuid4()
    now = datetime.now(timezone.utc)
    co = Company(id=cid, name="x", created_at=now, updated_at=now)
    d = model_to_dict(co)
    assert d["created_at"] == now


def test_model_to_dict_handles_none_fields():
    cid = uuid.uuid4()
    co = Company(id=cid, name="x")
    d = model_to_dict(co)
    assert d["owner_id"] is None
