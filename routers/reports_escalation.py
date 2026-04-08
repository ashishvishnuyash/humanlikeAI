from fastapi import APIRouter, HTTPException, Query, Response, Depends
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from google.cloud.firestore_v1 import SERVER_TIMESTAMP
from firebase_config import get_db
from routers.auth import get_current_user
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
def get_recent_reports(company_id: str, days: int):
    db = get_db()
    days_ago = datetime.utcnow() - timedelta(days=days)
    
    employees_docs = db.collection('users').where('company_id', '==', company_id).where('role', '==', 'employee').stream()
    employees = {d.id: d.to_dict() for d in employees_docs}
    
    reports_docs = db.collection('mental_health_reports').where('company_id', '==', company_id).stream()
    all_reports = []
    for d in reports_docs:
        rd = d.to_dict()
        rd['id'] = d.id
        
        ca = rd.get('created_at')
        if isinstance(ca, str):
            dt = datetime.fromisoformat(ca.replace('Z', '+00:00'))
        elif hasattr(ca, 'timestamp'):
            dt = datetime.fromtimestamp(ca.timestamp())
        else:
            continue
            
        # Strip timezone for naive comparison
        if dt.tzinfo:
            dt = dt.replace(tzinfo=None)
            
        if dt >= days_ago:
            all_reports.append(rd)
            
    all_reports.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    
    res = []
    for r in all_reports:
        emp = employees.get(r.get('employee_id'))
        if emp:
            r['employee'] = {
                'id': r.get('employee_id'),
                'first_name': emp.get('first_name', 'Employee'),
                'last_name': f"#{r.get('employee_id', '')[:4]}",
                'email': emp.get('email', f"employee-{r.get('employee_id', '')[:8]}@company.com"),
                'department': emp.get('department', 'Unassigned')
            }
        res.append(r)
    return res

def generate_analytics(reports):
    if not reports:
        return {
            'totalReports': 0, 'avgWellness': 0, 'avgStress': 0, 'avgMood': 0, 'avgEnergy': 0,
            'highRiskCount': 0, 'mediumRiskCount': 0, 'lowRiskCount': 0, 'departmentBreakdown': {}, 'dailyTrends': []
        }
        
    avg_wellness = round(sum(r.get('overall_wellness', 0) for r in reports) / len(reports), 1)
    avg_stress = round(sum(r.get('stress_level', 0) for r in reports) / len(reports), 1)
    avg_mood = round(sum(r.get('mood_rating', 0) for r in reports) / len(reports), 1)
    avg_energy = round(sum(r.get('energy_level', 0) for r in reports) / len(reports), 1)
    
    high_risk = sum(1 for r in reports if r.get('risk_level') == 'high')
    medium_risk = sum(1 for r in reports if r.get('risk_level') == 'medium')
    low_risk = sum(1 for r in reports if r.get('risk_level') == 'low')
    
    dept_bd = {}
    for r in reports:
        dept = r.get('employee', {}).get('department', 'Unassigned')
        if dept not in dept_bd:
            dept_bd[dept] = {'count': 0, 'sum_wellness': 0}
        dept_bd[dept]['count'] += 1
        dept_bd[dept]['sum_wellness'] += r.get('overall_wellness', 0)
        
    for k, v in dept_bd.items():
        v['avgWellness'] = round(v['sum_wellness'] / v['count'], 1)
        del v['sum_wellness']
        
    return {
        'totalReports': len(reports),
        'avgWellness': avg_wellness, 'avgStress': avg_stress, 'avgMood': avg_mood, 'avgEnergy': avg_energy,
        'highRiskCount': high_risk, 'mediumRiskCount': medium_risk, 'lowRiskCount': low_risk,
        'departmentBreakdown': dept_bd,
        'dailyTrends': [] # Simplified
    }

def format_ai_context(analytics):
    return f"Company Wellness: Avg {analytics['avgWellness']}/10. High Risk: {analytics['highRiskCount']}."

@router.get("/reports/recent", response_model=ReportsRecentResponse)
async def get_reports_recent(companyId: str, userId: Optional[str] = None, days: int = 7):
    if not companyId:
        raise HTTPException(status_code=400, detail="Company ID is required")
        
    reports = get_recent_reports(companyId, days)
    analytics = generate_analytics(reports)
    aiContext = format_ai_context(analytics)
    
    personalData = None
    if userId:
        # Simplified personal data
        personalData = {
            "history": {"recentReports": [r for r in reports if r.get('employee_id') == userId], "previousSessions": [], "progressTrends": {}},
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

@router.post("/escalation/create-ticket", response_model=CreateTicketResponse)
async def create_ticket(req: TicketRequest):
    db = get_db()
    
    ticket = {
        'employee_id': req.employee_id,
        'company_id': req.company_id,
        'ticket_type': req.ticket_type,
        'priority': req.priority,
        'subject': req.subject,
        'description': req.description,
        'category': req.category,
        'is_anonymous': req.is_anonymous,
        'confidential': req.confidential,
        'status': 'open',
        'follow_up_required': req.priority == 'urgent' or req.category == 'mental_health_crisis',
        'attachments': req.attachments,
        'created_at': SERVER_TIMESTAMP,
        'updated_at': SERVER_TIMESTAMP
    }
    
    _, ref = db.collection('escalation_tickets').add(ticket)
    
    # Auto-assign
    assigned_to = None
    if req.ticket_type == 'hr' or req.priority == 'urgent' or req.category == 'mental_health_crisis':
        hr_docs = db.collection('users').where('company_id', '==', req.company_id).where('role', 'in', ['hr', 'admin']).where('is_active', '==', True).limit(1).stream()
        for d in hr_docs:
            assigned_to = d.id
            break
            
    if assigned_to:
        db.collection('escalation_tickets').document(ref.id).update({'assigned_to': assigned_to, 'updated_at': SERVER_TIMESTAMP})
        
    return {"success": True, "ticket_id": ref.id, "message": "Ticket created successfully"}

@router.post("/employer/export-reports", response_class=Response)
async def export_csv(req: ExportRequest):
    if not req.company_id:
        raise HTTPException(400, "Company ID required")
        
    days = 7 if req.time_range == '7d' else 30 if req.time_range == '30d' else 90
    reports = get_recent_reports(req.company_id, days)
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'Report ID', 'Employee ID', 'Session Type', 'Mood Rating', 'Stress Level', 
        'Energy Level', 'Work Satisfaction', 'Work Life Balance', 'Anxiety Level',
        'Confidence Level', 'Sleep Quality', 'Overall Wellness', 'Risk Level'
    ])
    
    for r in reports:
        writer.writerow([
            r.get('id'),
            r.get('employee_id', '')[-8:],
            r.get('session_type'),
            r.get('mood_rating'),
            r.get('stress_level'),
            r.get('energy_level'),
            r.get('work_satisfaction'),
            r.get('work_life_balance'),
            r.get('anxiety_level'),
            r.get('confidence_level'),
            r.get('sleep_quality'),
            r.get('overall_wellness'),
            r.get('risk_level')
        ])
        
    return Response(content=output.getvalue(), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=wellness-reports-{req.time_range}.csv"})

@router.post("/export/pdf", response_class=Response)
async def export_pdf(req: ExportRequest):
    # Dummy PDF or text response for now since reportlab isn't installed.
    # To fully replicate we'd generate a PDF buffer.
    return Response(content=b"%PDF-1.4\n% Dummy PDF\n", media_type="application/pdf", headers={"Content-Disposition": "attachment; filename=wellness-report.pdf"})
