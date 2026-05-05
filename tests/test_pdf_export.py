"""Verify pdf_export.build_wellness_pdf produces a valid PDF byte stream
with key sections present."""

from __future__ import annotations

from services.pdf_export import build_wellness_pdf


def test_pdf_starts_with_pdf_magic_bytes():
    pdf_bytes = build_wellness_pdf(
        company_name="Acme Inc",
        date_range_label="Last 30 days",
        reports=[],
        analytics={
            "totalReports": 0, "avgWellness": 0, "avgStress": 0, "avgMood": 0, "avgEnergy": 0,
            "highRiskCount": 0, "mediumRiskCount": 0, "lowRiskCount": 0,
            "departmentBreakdown": {}, "dailyTrends": [],
        },
        include_charts=False,
        include_raw_data=False,
        include_analytics=True,
    )
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 500


def test_pdf_with_reports_is_larger_than_empty_pdf():
    empty = build_wellness_pdf(
        company_name="X",
        date_range_label="7d",
        reports=[],
        analytics={
            "totalReports": 0, "avgWellness": 0, "avgStress": 0, "avgMood": 0, "avgEnergy": 0,
            "highRiskCount": 0, "mediumRiskCount": 0, "lowRiskCount": 0,
            "departmentBreakdown": {}, "dailyTrends": [],
        },
        include_charts=False,
        include_raw_data=True,
        include_analytics=True,
    )
    populated = build_wellness_pdf(
        company_name="X",
        date_range_label="7d",
        reports=[
            {"id": "r1", "employee_id": "abcdef", "session_type": "voice",
             "mood_rating": 6, "stress_level": 4, "energy_level": 7,
             "overall_wellness": 7, "risk_level": "low",
             "generated_at": "2026-05-04T10:00:00", "notes": "Stable mood"},
        ],
        analytics={
            "totalReports": 1, "avgWellness": 7, "avgStress": 4, "avgMood": 6, "avgEnergy": 7,
            "highRiskCount": 0, "mediumRiskCount": 0, "lowRiskCount": 1,
            "departmentBreakdown": {}, "dailyTrends": [],
        },
        include_charts=False,
        include_raw_data=True,
        include_analytics=True,
    )
    assert len(populated) > len(empty)


def test_export_pdf_endpoint_returns_real_pdf(monkeypatch):
    """The /export/pdf route must call build_wellness_pdf and return its bytes,
    not the dummy 5-byte stub."""
    from unittest.mock import MagicMock
    import asyncio

    from routers import reports_escalation

    monkeypatch.setattr(
        reports_escalation, "get_recent_reports",
        lambda company_id, days, db: [],
    )
    monkeypatch.setattr(
        reports_escalation, "generate_analytics",
        lambda reports: {
            "totalReports": 0, "avgWellness": 0, "avgStress": 0, "avgMood": 0, "avgEnergy": 0,
            "highRiskCount": 0, "mediumRiskCount": 0, "lowRiskCount": 0,
            "departmentBreakdown": {}, "dailyTrends": [],
        },
    )

    req = reports_escalation.ExportRequest(
        company_id="00000000-0000-0000-0000-000000000001",
        time_range="30d",
        dateRange="30d",
        reportType="comprehensive",
    )
    db = MagicMock()
    response = asyncio.run(reports_escalation.export_pdf(req, db))

    assert response.media_type == "application/pdf"
    assert response.body.startswith(b"%PDF-")
    assert len(response.body) > 500
