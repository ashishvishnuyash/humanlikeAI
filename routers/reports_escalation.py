from fastapi import APIRouter, HTTPException, Query, Response, Depends
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from db.session import get_session
from db.models.mental_health import MentalHealthReport, EscalationTicket
from db.models.user import User
from routers.auth import get_current_user
import uuid
import csv
import io

router = APIRouter(tags=["Reports & Escalation"], dependencies=[Depends(get_current_user)])

class TicketRequest(BaseModel):
    employee_id: str
    company_id: str
    ticket_type: str
    priority: str
    subject: str
    description: str
    category: str
    is_anonymous: bool = False
    confidential: bool = False
    attachments: List[str] = []

class ExportRequest(BaseModel):
    company_id: Optional[str] = None
    time_range: Optional[str] = None
    userId: Optional[str] = None
    reportType: Optional[str] = 'company'
    dateRange: Optional[str] = '30d'
    department: Optional[str] = 'all'
    riskLevel: Optional[str] = 'all'

class AnalyticsDepartmentData(BaseModel):
    count: int
    avgWellness: float

class AnalyticsData(BaseModel):
    totalReports: int
    avgWellness: float
    avgStress: float
    avgMood: float
    avgEnergy: float
    highRiskCount: int
    mediumRiskCount: int
    lowRiskCount: int
    departmentBreakdown: Dict[str, AnalyticsDepartmentData]
    dailyTrends: List[Any]

class CompanyReportsData(BaseModel):
    count: int
    analytics: AnalyticsData
    aiContext: str

class ReportsRecentData(BaseModel):
    companyReports: CompanyReportsData
    personalHistory: Optional[Dict[str, Any]] = None

class ReportsRecentResponse(BaseModel):
    success: bool
    data: ReportsRecentData

class CreateTicketResponse(BaseModel):
    success: bool
    ticket_id: str
    message: str

# --- HELPERS ---
def get_recent_reports(company_id: str, days: int, db: Session):
    try:
        cid_uuid = uuid.UUID(company_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid company_id UUID")

    days_ago = datetime.utcnow() - timedelta(days=days)

    employees = {
        u.id: u
        for u in db.query(User).filter(
            User.company_id == cid_uuid,
            User.role == 'employee'
        ).all()
    }

    report_rows = db.query(MentalHealthReport).filter(
        MentalHealthReport.company_id == cid_uuid,
        MentalHealthReport.generated_at >= days_ago
    ).order_by(MentalHealthReport.generated_at.desc()).all()

    res = []
    for row in report_rows:
        # Flatten: merge the JSONB report dict with top-level columns
        rd = dict(row.report) if row.report else {}
        rd['id'] = str(row.id)
        rd['risk_level'] = row.risk_level
        rd['generated_at'] = row.generated_at.isoformat()

        emp_id = rd.get('employee_id') or row.user_id
        # Persist resolved employee_id back onto the dict so downstream
        # consumers (e.g. personalHistory userId filter) can match on it.
        rd['employee_id'] = emp_id
        emp = employees.get(emp_id)
        if emp:
            rd['employee'] = {
                'id': emp_id,
                'first_name': emp.profile.get('first_name', 'Employee') if emp.profile else 'Employee',
                'last_name': f"#{(emp_id or '')[:4]}",
                'email': emp.email,
                'department': emp.department or 'Unassigned'
            }
        res.append(rd)

    return res

def generate_analytics(reports):
    if not reports:
        return {
            'totalReports': 0, 'avgWellness': 0, 'avgStress': 0, 'avgMood': 0, 'avgEnergy': 0,
            'highRiskCount': 0, 'mediumRiskCount': 0, 'lowRiskCount': 0, 'departmentBreakdown': {}, 'dailyTrends': []
        }

    # Use `or 0` because the JSONB may carry explicit nulls for these fields
    # (older reports / partial fills). dict.get(default) returns None for an
    # existing-but-null key, which would crash sum() on int + NoneType.
    def _num(r, k):
        return r.get(k) or 0

    avg_wellness = round(sum(_num(r, 'overall_wellness') for r in reports) / len(reports), 1)
    avg_stress = round(sum(_num(r, 'stress_level') for r in reports) / len(reports), 1)
    avg_mood = round(sum(_num(r, 'mood_rating') for r in reports) / len(reports), 1)
    avg_energy = round(sum(_num(r, 'energy_level') for r in reports) / len(reports), 1)

    high_risk = sum(1 for r in reports if r.get('risk_level') == 'high')
    medium_risk = sum(1 for r in reports if r.get('risk_level') == 'medium')
    low_risk = sum(1 for r in reports if r.get('risk_level') == 'low')

    dept_bd = {}
    for r in reports:
        dept = (r.get('employee') or {}).get('department', 'Unassigned')
        if dept not in dept_bd:
            dept_bd[dept] = {'count': 0, 'sum_wellness': 0}
        dept_bd[dept]['count'] += 1
        dept_bd[dept]['sum_wellness'] += _num(r, 'overall_wellness')

    for k, v in dept_bd.items():
        v['avgWellness'] = round(v['sum_wellness'] / v['count'], 1)
        del v['sum_wellness']

    return {
        'totalReports': len(reports),
        'avgWellness': avg_wellness, 'avgStress': avg_stress, 'avgMood': avg_mood, 'avgEnergy': avg_energy,
        'highRiskCount': high_risk, 'mediumRiskCount': medium_risk, 'lowRiskCount': low_risk,
        'departmentBreakdown': dept_bd,
        'dailyTrends': []  # Simplified
    }

def format_ai_context(analytics):
    return f"Company Wellness: Avg {analytics['avgWellness']}/10. High Risk: {analytics['highRiskCount']}."

@router.get("/reports/recent", response_model=ReportsRecentResponse)
async def get_reports_recent(
    companyId: str,
    userId: Optional[str] = None,
    days: int = 7,
    db: Session = Depends(get_session)
):
    if not companyId:
        raise HTTPException(status_code=400, detail="Company ID is required")

    reports = get_recent_reports(companyId, days, db)
    analytics = generate_analytics(reports)
    aiContext = format_ai_context(analytics)

    personalData = None
    if userId:
        personalData = {
            "history": {
                "recentReports": [r for r in reports if r.get('employee_id') == userId],
                "previousSessions": [],
                "progressTrends": {}
            },
            "aiContext": "Personal context generated."
        }

    return {
        "success": True,
        "data": {
            "companyReports": {
                "count": len(reports),
                "analytics": analytics,
                "aiContext": aiContext
            },
            "personalHistory": personalData
        }
    }

class CreateReportRequest(BaseModel):
    """Standalone wellness check-in (no chat). Frontend wizard at
    /employee/reports/new posts this shape."""
    employee_id: Optional[str] = None  # if absent, taken from JWT
    company_id: Optional[str] = None   # if absent, taken from JWT
    mood_rating: Optional[int] = None
    stress_level: Optional[int] = None
    energy_level: Optional[int] = None
    work_satisfaction: Optional[int] = None
    work_life_balance: Optional[int] = None
    anxiety_level: Optional[int] = None
    confidence_level: Optional[int] = None
    sleep_quality: Optional[int] = None
    overall_wellness: Optional[int] = None
    comments: Optional[str] = None
    session_type: Optional[str] = "self_report"


class CreateReportResponse(BaseModel):
    success: bool
    report_id: str


def _derive_overall_wellness(req: "CreateReportRequest") -> int:
    """Average of the present 1-10 scores (with stress/anxiety inverted),
    or fall back to whatever the caller provided."""
    if req.overall_wellness is not None:
        return req.overall_wellness
    parts = []
    for v in (req.mood_rating, req.energy_level, req.work_satisfaction,
              req.work_life_balance, req.confidence_level, req.sleep_quality):
        if v is not None:
            parts.append(v)
    for v in (req.stress_level, req.anxiety_level):
        if v is not None:
            parts.append(11 - v)  # invert: low stress is good wellness
    if not parts:
        return 5
    return round(sum(parts) / len(parts))


def _derive_risk_level(overall: int) -> str:
    if overall <= 4:
        return "high"
    if overall <= 6:
        return "medium"
    return "low"


@router.post("/reports", response_model=CreateReportResponse)
async def create_report(
    req: CreateReportRequest,
    db: Session = Depends(get_session),
    user_token: dict = Depends(get_current_user),
):
    """Persist a self-report (wizard, no chat) as a MentalHealthReport row.

    employee_id/company_id from the request are advisory; the JWT identity
    is authoritative — callers can only create reports for themselves."""
    user_id = user_token.get("uid") or user_token.get("sub")
    if not user_id:
        raise HTTPException(401, "Authenticated user has no id")

    company_id_str = user_token.get("company_id") or req.company_id
    company_uuid: Optional[uuid.UUID] = None
    if company_id_str:
        try:
            company_uuid = uuid.UUID(company_id_str)
        except (ValueError, AttributeError):
            company_uuid = None

    overall = _derive_overall_wellness(req)
    risk = _derive_risk_level(overall)

    report_data = {
        "mood_rating": req.mood_rating,
        "stress_level": req.stress_level,
        "energy_level": req.energy_level,
        "work_satisfaction": req.work_satisfaction,
        "work_life_balance": req.work_life_balance,
        "anxiety_level": req.anxiety_level,
        "confidence_level": req.confidence_level,
        "sleep_quality": req.sleep_quality,
        "overall_wellness": overall,
        "comments": req.comments,
        "session_type": req.session_type or "self_report",
        "employee_id": user_id,
    }

    row = MentalHealthReport(
        id=uuid.uuid4(),
        user_id=user_id,
        company_id=company_uuid,
        report=report_data,
        risk_level=risk,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return CreateReportResponse(success=True, report_id=str(row.id))


@router.get("/reports/{report_id}")
async def get_report_by_id(
    report_id: str,
    db: Session = Depends(get_session),
    user_token: dict = Depends(get_current_user),
):
    """Single-report fetch for the report detail page.

    Returns the flattened JSONB report dict (same shape as items in
    /reports/recent), with id/risk_level/generated_at/employee_id resolved.

    Access is allowed if the caller belongs to the same company as the
    report's owner; an employee can also fetch their own report regardless
    of company match. We do NOT 404 cross-company requests differently from
    not-found, to avoid leaking existence."""
    try:
        rid_uuid = uuid.UUID(report_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Report not found")

    row = db.query(MentalHealthReport).filter(
        MentalHealthReport.id == rid_uuid
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Report not found")

    caller_uid = user_token.get("uid") or user_token.get("sub")
    caller_cid = user_token.get("company_id")

    is_owner = caller_uid is not None and caller_uid == row.user_id
    is_same_company = (
        caller_cid is not None
        and row.company_id is not None
        and str(row.company_id) == str(caller_cid)
    )
    if not (is_owner or is_same_company):
        raise HTTPException(status_code=404, detail="Report not found")

    rd = dict(row.report) if row.report else {}
    rd['id'] = str(row.id)
    rd['risk_level'] = row.risk_level
    rd['generated_at'] = row.generated_at.isoformat() if row.generated_at else None
    rd['employee_id'] = rd.get('employee_id') or row.user_id

    # Enrich with employee info (best-effort)
    emp = db.query(User).filter(User.id == row.user_id).one_or_none()
    if emp is not None:
        rd['employee'] = {
            'id': emp.id,
            'first_name': (emp.profile or {}).get('first_name', 'Employee'),
            'last_name': f"#{(emp.id or '')[:4]}",
            'email': emp.email,
            'department': emp.department or 'Unassigned',
        }

    return {"success": True, "data": rd}


@router.post("/escalation/create-ticket", response_model=CreateTicketResponse)
async def create_ticket(req: TicketRequest, db: Session = Depends(get_session)):
    try:
        cid_uuid = uuid.UUID(req.company_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid company_id UUID")

    ticket_data = {
        'employee_id': req.employee_id,
        'ticket_type': req.ticket_type,
        'subject': req.subject,
        'description': req.description,
        'category': req.category,
        'is_anonymous': req.is_anonymous,
        'confidential': req.confidential,
        'follow_up_required': req.priority == 'urgent' or req.category == 'mental_health_crisis',
        'attachments': req.attachments,
    }

    ticket = EscalationTicket(
        id=uuid.uuid4(),
        company_id=cid_uuid,
        user_id=req.employee_id,
        status='open',
        priority=req.priority,
        data=ticket_data,
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)

    ticket_id = str(ticket.id)

    # Auto-assign
    if req.ticket_type == 'hr' or req.priority == 'urgent' or req.category == 'mental_health_crisis':
        hr_user = db.query(User).filter(
            User.company_id == cid_uuid,
            User.role.in_(['hr', 'admin']),
            User.is_active == True
        ).limit(1).first()

        if hr_user:
            db.query(EscalationTicket).filter(
                EscalationTicket.id == ticket.id
            ).update({'assigned_to': hr_user.id})
            db.commit()

    return {"success": True, "ticket_id": ticket_id, "message": "Ticket created successfully"}

def _build_csv_rows(reports: list[dict]) -> list[list]:
    """Build CSV rows (header + data) for the employer reports export.

    Returns a list of lists, with the first row being the header.
    Frontend Firestore version produces 16 columns; we match exactly so
    the eventual frontend swap is a no-op for downstream consumers."""
    header = [
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
    out: list[list] = [header]
    for r in reports:
        emp_id = (r.get("employee_id", "") or "")[-8:]
        gen_at = r.get("generated_at") or r.get("created_at") or ""
        summary = r.get("notes") or r.get("complete_report") or r.get("ai_analysis") or ""
        if isinstance(summary, str) and len(summary) > 500:
            summary = summary[:500] + "..."
        out.append([
            r.get("id"),
            emp_id,
            gen_at,
            r.get("session_type"),
            r.get("mood_rating"),
            r.get("stress_level"),
            r.get("energy_level"),
            r.get("work_satisfaction"),
            r.get("work_life_balance"),
            r.get("anxiety_level"),
            r.get("confidence_level"),
            r.get("sleep_quality"),
            r.get("overall_wellness"),
            r.get("risk_level"),
            r.get("session_duration_minutes", "") if r.get("session_duration_minutes") is not None else "",
            summary,
        ])
    return out


@router.post("/employer/export-reports", response_class=Response)
async def export_csv(req: ExportRequest, db: Session = Depends(get_session)):
    if not req.company_id:
        raise HTTPException(400, "Company ID required")

    days = 7 if req.time_range == '7d' else 30 if req.time_range == '30d' else 90
    reports = get_recent_reports(req.company_id, days, db)

    output = io.StringIO()
    writer = csv.writer(output)
    for row in _build_csv_rows(reports):
        writer.writerow(row)

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=wellness-reports-{req.time_range}.csv"}
    )

@router.post("/export/pdf", response_class=Response)
async def export_pdf(req: ExportRequest, db: Session = Depends(get_session)):
    if not req.company_id:
        raise HTTPException(400, "Company ID required")

    days = 7 if req.dateRange == "7d" else 30 if req.dateRange == "30d" else 90 if req.dateRange == "90d" else 30
    reports = get_recent_reports(req.company_id, days, db)

    # Optional employee filter
    if req.userId:
        reports = [r for r in reports if r.get("employee_id") == req.userId]
    # Optional department filter
    if req.department and req.department != "all":
        reports = [r for r in reports if (r.get("employee", {}) or {}).get("department") == req.department]
    # Optional risk filter
    if req.riskLevel and req.riskLevel != "all":
        reports = [r for r in reports if r.get("risk_level") == req.riskLevel]

    analytics = generate_analytics(reports)

    from services.pdf_export import build_wellness_pdf

    pdf_bytes = build_wellness_pdf(
        company_name=req.company_id,  # caller may want a real name; not in request shape
        date_range_label=req.dateRange or "30d",
        reports=reports,
        analytics=analytics,
        include_charts=True,
        include_raw_data=True,
        include_analytics=True,
    )

    filename = f"wellness-report-{datetime.utcnow().strftime('%Y-%m-%d')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
