"""Verify the employer CSV export has all 16 columns the frontend
Firestore version produces."""

from __future__ import annotations

from routers.reports_escalation import _build_csv_rows


def test_csv_header_has_16_columns():
    rows = _build_csv_rows([])
    assert rows[0] == [
        "Report ID",
        "Employee ID",
        "Date",
        "Session Type",
        "Mood Rating",
        "Stress Level",
        "Energy Level",
        "Work Satisfaction",
        "Work Life Balance",
        "Anxiety Level",
        "Confidence Level",
        "Sleep Quality",
        "Overall Wellness",
        "Risk Level",
        "Session Duration (min)",
        "AI Analysis Summary",
    ]


def test_csv_data_row_includes_date_duration_and_summary():
    fake_report = {
        "id": "r-1",
        "employee_id": "AAAAAABBBBBBCCCC",
        "session_type": "voice",
        "mood_rating": 7,
        "stress_level": 4,
        "energy_level": 6,
        "work_satisfaction": 8,
        "work_life_balance": 7,
        "anxiety_level": 3,
        "confidence_level": 7,
        "sleep_quality": 6,
        "overall_wellness": 7,
        "risk_level": "low",
        "generated_at": "2026-05-04T13:30:00",
        "session_duration_minutes": 22,
        "notes": "User reported stable mood and good sleep.",
    }
    rows = _build_csv_rows([fake_report])
    assert len(rows) == 2
    data = rows[1]
    assert data[1] == "BBBBCCCC"
    assert data[2].startswith("2026-05-04")
    assert data[14] == 22
    assert "stable mood" in data[15]


def test_csv_data_row_handles_missing_optional_fields():
    fake_report = {"id": "r-2", "employee_id": "x"}
    rows = _build_csv_rows([fake_report])
    data = rows[1]
    assert data[2] == ""
    assert data[14] == ""
    assert data[15] == ""
