"""PDF generation for the employer wellness report export.

Pure helper — no DB or HTTP. Caller passes pre-aggregated data, this
module returns PDF bytes."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)


def build_wellness_pdf(
    *,
    company_name: str,
    date_range_label: str,
    reports: list[dict],
    analytics: dict[str, Any],
    include_charts: bool,
    include_raw_data: bool,
    include_analytics: bool,
) -> bytes:
    """Render a wellness report PDF and return the byte stream."""
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, title="Wellness Report")
    styles = getSampleStyleSheet()
    story: list = []

    # ─── Cover ────────────────────────────────────────────────────────────
    title_style = ParagraphStyle(
        "Title", parent=styles["Title"], fontSize=22, leading=26, spaceAfter=12,
    )
    story.append(Paragraph("Wellness Report", title_style))
    story.append(Paragraph(f"<b>Company:</b> {company_name}", styles["Normal"]))
    story.append(Paragraph(f"<b>Period:</b> {date_range_label}", styles["Normal"]))
    story.append(Paragraph(
        f"<b>Generated:</b> {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        styles["Normal"],
    ))
    story.append(Spacer(1, 0.3 * inch))

    # ─── Analytics ───────────────────────────────────────────────────────
    if include_analytics:
        story.append(Paragraph("Analytics", styles["Heading2"]))
        kpis = [
            ["Total Reports", str(analytics.get("totalReports", 0))],
            ["Avg Wellness", str(analytics.get("avgWellness", 0))],
            ["Avg Stress", str(analytics.get("avgStress", 0))],
            ["Avg Mood", str(analytics.get("avgMood", 0))],
            ["Avg Energy", str(analytics.get("avgEnergy", 0))],
            ["High Risk", str(analytics.get("highRiskCount", 0))],
            ["Medium Risk", str(analytics.get("mediumRiskCount", 0))],
            ["Low Risk", str(analytics.get("lowRiskCount", 0))],
        ]
        kpi_table = Table(kpis, colWidths=[2.5 * inch, 2 * inch])
        kpi_table.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
            ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ]))
        story.append(kpi_table)
        story.append(Spacer(1, 0.2 * inch))

        dept_bd = analytics.get("departmentBreakdown") or {}
        if dept_bd:
            story.append(Paragraph("Department Breakdown", styles["Heading3"]))
            dept_rows = [["Department", "Count", "Avg Wellness"]]
            for dept, vals in dept_bd.items():
                dept_rows.append([dept, str(vals.get("count", 0)), str(vals.get("avgWellness", 0))])
            dept_table = Table(dept_rows, colWidths=[2.5 * inch, 1.5 * inch, 1.5 * inch])
            dept_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightblue),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
            ]))
            story.append(dept_table)
            story.append(Spacer(1, 0.2 * inch))

    # ─── Charts (text-only summary) ──────────────────────────────────────
    if include_charts and analytics.get("totalReports", 0) > 0:
        story.append(Paragraph("Trends", styles["Heading2"]))
        story.append(Paragraph(
            f"Wellness trend over {date_range_label}: avg {analytics.get('avgWellness', 0)}/10. "
            f"Risk distribution — High: {analytics.get('highRiskCount', 0)}, "
            f"Medium: {analytics.get('mediumRiskCount', 0)}, "
            f"Low: {analytics.get('lowRiskCount', 0)}.",
            styles["Normal"],
        ))
        story.append(Spacer(1, 0.2 * inch))

    # ─── Raw data ────────────────────────────────────────────────────────
    if include_raw_data and reports:
        story.append(PageBreak())
        story.append(Paragraph("Report Detail", styles["Heading2"]))
        rows = [["Date", "Employee", "Type", "Mood", "Stress", "Energy", "Wellness", "Risk"]]
        for r in reports:
            rows.append([
                str(r.get("generated_at", ""))[:16],
                str(r.get("employee_id", ""))[-8:],
                str(r.get("session_type", "")),
                str(r.get("mood_rating", "")),
                str(r.get("stress_level", "")),
                str(r.get("energy_level", "")),
                str(r.get("overall_wellness", "")),
                str(r.get("risk_level", "")),
            ])
        table = Table(rows, colWidths=[1.2 * inch, 1 * inch, 0.7 * inch, 0.6 * inch, 0.6 * inch, 0.6 * inch, 0.7 * inch, 0.6 * inch])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightblue),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
        ]))
        story.append(table)

    doc.build(story)
    return buf.getvalue()
