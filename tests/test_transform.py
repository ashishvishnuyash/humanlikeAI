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


# --- users ---

from migration.transform import transform_user  # noqa: E402


def test_transform_user_preserves_firebase_uid_as_id():
    doc = {
        "email": "a@example.com",
        "role": "employee",
        "company_id": "b4d7c422-f13c-4465-84fc-70b6e922b625",
        "manager_id": "uid-mgr-1",
        "department": "Eng",
        "is_active": True,
        "display_name": "Alice",  # extra → profile
        "direct_reports": ["u1", "u2"],  # denormalization, drop
        "created_at": None,
    }
    row = transform_user("firebase-uid-abc", doc)
    # id preserved verbatim (TEXT column)
    assert row["id"] == "firebase-uid-abc"
    assert row["email"] == "a@example.com"
    assert row["role"] == "employee"
    # company_id should be a uuid.UUID
    assert str(row["company_id"]) == "b4d7c422-f13c-4465-84fc-70b6e922b625"
    assert row["manager_id"] == "uid-mgr-1"
    assert row["department"] == "Eng"
    assert row["is_active"] is True
    # Extras folded into profile; denormalized `direct_reports` dropped entirely
    assert row["profile"]["display_name"] == "Alice"
    assert "direct_reports" not in row["profile"]
    # Every migrated user gets the default password "11111111".
    from auth.password import verify_password
    assert row["password_hash"] is not None
    assert row["password_hash"].startswith(("$2a$", "$2b$", "$2y$"))
    assert verify_password("11111111", row["password_hash"]) is True


def test_transform_user_missing_email_raises():
    from migration.transform import MissingRequiredField
    with pytest.raises(MissingRequiredField):
        transform_user("uid", {"role": "employee"})


def test_transform_user_missing_role_defaults_to_employee():
    # Role is required logically but some legacy docs omit it. Default to employee.
    row = transform_user("uid", {"email": "x@y.com"})
    assert row["role"] == "employee"


# --- tests for remaining 20 transformers ---

from migration.transform import (  # noqa: E402
    transform_ai_recommendation,
    transform_anonymous_profile,
    transform_call,
    transform_call_session,
    transform_chat_session,
    transform_check_in,
    transform_community_post,
    transform_community_reply,
    transform_escalation_ticket,
    transform_import_job,
    transform_intervention,
    transform_medical_document,
    transform_mental_health_report,
    transform_mh_session,
    transform_physical_health_checkin,
    transform_physical_health_report,
    transform_user_gamification,
    transform_wellness_challenge,
    transform_wellness_event,
)


def _uuid_str():
    return str(uuid.uuid4())


def test_transform_check_in_uses_data_catchall():
    row = transform_check_in("cid-1", {
        "user_id": "u1",
        "company_id": _uuid_str(),
        "mood_score": 7,  # extra → data JSONB
        "stress_level": 4,  # extra → data JSONB
        "notes": "ok",  # extra → data
        "created_at": datetime(2024, 3, 1, tzinfo=timezone.utc),
    })
    assert row["user_id"] == "u1"
    assert row["data"]["mood_score"] == 7
    assert row["data"]["stress_level"] == 4
    assert row["data"]["notes"] == "ok"


def test_transform_mh_session_preserves_messages():
    row = transform_mh_session("s1", {
        "user_id": "u1",
        "company_id": _uuid_str(),
        "messages": [{"role": "user", "content": "hi"}],
        "summary": "friendly chat",
    })
    assert row["messages"] == [{"role": "user", "content": "hi"}]
    assert row["summary"] == "friendly chat"


def test_transform_mental_health_report_uses_report_catchall():
    row = transform_mental_health_report("r1", {
        "user_id": "u1",
        "company_id": _uuid_str(),
        "risk_level": "low",
        "summary": "all good",  # extra → report
        "generated_at": datetime(2024, 3, 1, tzinfo=timezone.utc),
    })
    assert row["risk_level"] == "low"
    assert row["report"]["summary"] == "all good"


def test_transform_chat_session_message_list():
    row = transform_chat_session("cs1", {
        "user_id": "u1",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert row["user_id"] == "u1"
    assert row["messages"] == [{"role": "user", "content": "hi"}]


def test_transform_ai_recommendation_uses_recommendation_catchall():
    row = transform_ai_recommendation("r1", {
        "user_id": "u1",
        "category": "sleep",
        "title": "Go to bed earlier",  # extra → recommendation
        "body": "Try 10pm.",  # extra → recommendation
    })
    assert row["category"] == "sleep"
    assert row["recommendation"]["title"] == "Go to bed earlier"
    assert row["recommendation"]["body"] == "Try 10pm."


def test_transform_intervention():
    row = transform_intervention("iv1", {
        "user_id": "u1",
        "company_id": _uuid_str(),
        "status": "pending",
        "kind": "hr_followup",  # extra → data
    })
    assert row["status"] == "pending"
    assert row["data"]["kind"] == "hr_followup"


def test_transform_escalation_ticket():
    row = transform_escalation_ticket("t1", {
        "user_id": "u1",
        "company_id": _uuid_str(),
        "assigned_to": "hr-uid",
        "status": "open",
        "priority": "high",
        "subject": "anxiety",  # extra → data
    })
    assert row["assigned_to"] == "hr-uid"
    assert row["priority"] == "high"
    assert row["data"]["subject"] == "anxiety"


def test_transform_physical_health_checkin_vitals_catchall():
    row = transform_physical_health_checkin("p1", {
        "user_id": "u1",
        "company_id": _uuid_str(),
        "vitals": {"hr": 72},
        "symptoms": {"headache": False},
        "weight_kg": 70,  # extra → vitals
    })
    assert row["vitals"] == {"hr": 72, "weight_kg": 70}
    assert row["symptoms"] == {"headache": False}


def test_transform_physical_health_report_catchall():
    row = transform_physical_health_report("ph1", {
        "user_id": "u1",
        "company_id": _uuid_str(),
        "summary": "good",  # extra → report
    })
    assert row["report"]["summary"] == "good"


def test_transform_medical_document():
    row = transform_medical_document("md1", {
        "user_id": "u1",
        "filename": "lab.pdf",
        "blob_url": "https://firebasestorage.googleapis.com/v0/b/x/medical_reports/u1/md1/lab.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 1024,
        "extracted_text": "text",
        "random": "dropped",  # no catch-all; should be logged+dropped
    })
    assert row["filename"] == "lab.pdf"
    assert row["blob_url"].startswith("https://firebasestorage")
    assert row["size_bytes"] == 1024
    # 'random' should NOT appear in row
    assert "random" not in row


def test_transform_wellness_event():
    row = transform_wellness_event("w1", {
        "user_id": "u1",
        "company_id": _uuid_str(),
        "event_type": "streak",
        "detail": "7 days",  # extra → data
    })
    assert row["event_type"] == "streak"
    assert row["data"]["detail"] == "7 days"


def test_transform_anonymous_profile():
    row = transform_anonymous_profile("ap1", {
        "user_id": "u1",
        "handle": "cool_axolotl",
        "avatar": "🐸",
    })
    assert row["user_id"] == "u1"
    assert row["handle"] == "cool_axolotl"


def test_transform_community_post():
    row = transform_community_post("cp1", {
        "company_id": _uuid_str(),
        "anonymous_profile_id": _uuid_str(),
        "content": "hi everyone",
        "likes": 3,
        "replies": 1,
        "is_approved": True,
    })
    assert row["content"] == "hi everyone"
    assert row["likes"] == 3
    assert row["replies"] == 1
    assert row["is_approved"] is True


def test_transform_community_reply():
    row = transform_community_reply("cr1", {
        "post_id": _uuid_str(),
        "anonymous_profile_id": _uuid_str(),
        "content": "me too",
        "is_approved": True,
    })
    assert row["content"] == "me too"


def test_transform_user_gamification():
    row = transform_user_gamification("ug1", {
        "user_id": "u1",
        "company_id": _uuid_str(),
        "points": 100,
        "level": 3,
        "badges": ["streak-7", "first-checkin"],
        "streak": 7,
    })
    assert row["points"] == 100
    assert row["level"] == 3
    assert row["badges"] == ["streak-7", "first-checkin"]


def test_transform_wellness_challenge():
    row = transform_wellness_challenge("wc1", {
        "company_id": _uuid_str(),
        "title": "30 days of water",
        "description": "Drink 2L/day",
        "is_active": True,
        "reward_points": 500,  # extra → data
    })
    assert row["title"] == "30 days of water"
    assert row["is_active"] is True
    assert row["data"]["reward_points"] == 500


def test_transform_call():
    row = transform_call("call-1", {
        "caller_id": "u1",
        "callee_id": "u2",
        "status": "ended",
        "end_reason": "hangup",
    })
    assert row["caller_id"] == "u1"
    assert row["callee_id"] == "u2"
    assert row["end_reason"] == "hangup"


def test_transform_call_session():
    call_uuid = uuid.uuid4()
    row = transform_call_session("cs-1", {
        "call_id": str(call_uuid),
        "status": "active",
        "metadata": {"codec": "opus"},
        "stream_sid": "abc",  # extra → call_metadata
    })
    assert row["call_id"] == call_uuid
    assert row["status"] == "active"
    # both `metadata` and extras merged into call_metadata (DB column `metadata`)
    assert row["call_metadata"] == {"codec": "opus", "stream_sid": "abc"}


def test_transform_import_job_uses_stats_catchall():
    company_uuid = uuid.uuid4()
    row = transform_import_job("ij1", {
        "company_id": str(company_uuid),
        "created_by": "hr-uid",
        "status": "completed",
        "stats": {"total": 10, "succeeded": 9},
        "errors": [{"row": 5, "error": "bad email"}],
        "blob_url": "https://storage.example.com/x.csv",
        "filename": "roster.csv",  # extra → stats
    })
    assert row["company_id"] == company_uuid
    assert row["status"] == "completed"
    assert row["stats"]["total"] == 10
    assert row["stats"]["filename"] == "roster.csv"
    assert row["errors"] == [{"row": 5, "error": "bad email"}]
