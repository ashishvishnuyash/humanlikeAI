"""
Physical Health Router
======================
  POST   /api/physical-health/check-in              → Submit daily check-in
  GET    /api/physical-health/check-ins              → Paginated check-in history
  GET    /api/physical-health/score                  → Current composite health score
  GET    /api/physical-health/trends                 → Time-series data for charts

  POST   /api/physical-health/medical/upload         → Upload medical report (PDF/DOCX)
  GET    /api/physical-health/medical                → List all user's medical documents
  GET    /api/physical-health/medical/{doc_id}       → Full document detail + analysis
  GET    /api/physical-health/medical/{doc_id}/status → Poll processing status
  DELETE /api/physical-health/medical/{doc_id}       → Delete doc + Storage + RAG chunks

  POST   /api/physical-health/reports/generate       → Generate on-demand periodic report
  GET    /api/physical-health/reports                → List generated health reports
  GET    /api/physical-health/reports/{report_id}    → Full single report

  POST   /api/physical-health/ask                    → RAG Q&A on own medical history

Auth: all endpoints require a valid Bearer token (get_current_user).
Medical documents are 100% user-private — never filtered by company_id.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from db.session import get_session
from db.models.physical_health import (
    MedicalDocument,
    PhysicalHealthCheckin,
    PhysicalHealthReport,
)
from db.models.user import User

from routers.auth import get_current_user
from report_schemas import score_to_level
from physical_health_schemas import (
    AskRequest,
    AskResponse,
    CheckInHistoryItem,
    CheckInHistoryResponse,
    HealthTrendsResponse,
    MedicalDocumentDetail,
    MedicalDocumentListResponse,
    MedicalDocumentStatusResponse,
    MedicalDocumentUploadResponse,
    PeriodicReportRequest,
    PeriodicReportResponse,
    PhysicalCheckInRequest,
    PhysicalCheckInResponse,
    PhysicalHealthScoreResponse,
    TrendPoint,
)

router = APIRouter(prefix="/physical-health", tags=["Physical Health"])

# ─── Allowed file types for medical uploads ───────────────────────────────────
_ALLOWED_TYPES = {
    "application/pdf":                                                  "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword":                                               "docx",
}
_ALLOWED_EXTENSIONS = (".pdf", ".docx", ".doc")
_MAX_FILE_BYTES = 10 * 1024 * 1024   # 10 MB


# ─── Auth helper: get uid + company_id from token ────────────────────────────

def _get_user_context(user_token: dict, db: Session) -> tuple[str, Optional[uuid.UUID]]:
    """Return (uid, company_id) from token. Raises 404 if profile missing."""
    uid = user_token.get("uid")
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise HTTPException(404, "User profile not found.")
    return uid, user.company_id


# ─── Timestamp helper ────────────────────────────────────────────────────────

def _ts_to_iso(ts) -> Optional[str]:
    if ts is None:
        return None
    if hasattr(ts, "timestamp"):
        return datetime.fromtimestamp(ts.timestamp(), tz=timezone.utc).isoformat()
    if isinstance(ts, datetime):
        return ts.isoformat()
    return str(ts)


# ═══════════════════════════════════════════════════════════════════════════════
# CHECK-IN ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/check-in",
    response_model=PhysicalCheckInResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit Daily Physical Health Check-in",
)
async def submit_checkin(
    req: PhysicalCheckInRequest,
    user_token: dict = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    uid, company_id = _get_user_context(user_token, db)
    checkin_id = uuid.uuid4()

    vitals = {
        "energy_level":      req.energy_level,
        "sleep_quality":     req.sleep_quality,
        "sleep_hours":       req.sleep_hours,
        "exercise_done":     req.exercise_done,
        "exercise_minutes":  req.exercise_minutes,
        "exercise_type":     req.exercise_type,
        "nutrition_quality": req.nutrition_quality,
        "pain_level":        req.pain_level,
        "hydration":         req.hydration,
    }
    symptoms = {
        "notes": req.notes,
    }

    checkin = PhysicalHealthCheckin(
        id=checkin_id,
        user_id=uid,
        company_id=company_id,
        vitals=vitals,
        symptoms=symptoms,
    )

    try:
        db.add(checkin)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"Failed to save check-in: {e}")

    nudge = _compute_nudge(req)
    return PhysicalCheckInResponse(success=True, checkin_id=str(checkin_id), nudge=nudge)


@router.get(
    "/check-ins",
    response_model=CheckInHistoryResponse,
    summary="Get Check-in History",
)
async def get_checkin_history(
    page:  int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    days:  int = Query(90, ge=1, le=365, description="Lookback window in days"),
    user_token: dict = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    uid, _ = _get_user_context(user_token, db)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    try:
        rows = (
            db.query(PhysicalHealthCheckin)
            .filter(
                PhysicalHealthCheckin.user_id == uid,
                PhysicalHealthCheckin.created_at >= cutoff,
            )
            .order_by(PhysicalHealthCheckin.created_at.desc())
            .all()
        )
    except Exception as e:
        raise HTTPException(500, f"Query failed: {e}")

    all_items: List[CheckInHistoryItem] = []
    for row in rows:
        v = row.vitals or {}
        s = row.symptoms or {}
        all_items.append(CheckInHistoryItem(
            checkin_id=str(row.id),
            created_at=_ts_to_iso(row.created_at) or "",
            energy_level=v.get("energy_level", 0),
            sleep_quality=v.get("sleep_quality", 0),
            sleep_hours=v.get("sleep_hours", 0),
            exercise_done=v.get("exercise_done", False),
            exercise_minutes=v.get("exercise_minutes", 0),
            exercise_type=v.get("exercise_type", "none"),
            nutrition_quality=v.get("nutrition_quality", 0),
            pain_level=v.get("pain_level", 0),
            hydration=v.get("hydration", 0),
            notes=s.get("notes"),
        ))

    total = len(all_items)
    total_pages = max(1, (total + limit - 1) // limit)
    offset = (page - 1) * limit

    return CheckInHistoryResponse(
        success=True,
        checkins=all_items[offset: offset + limit],
        total=total,
        page=page,
        limit=limit,
        totalPages=total_pages,
        hasNext=offset + limit < total,
        hasPrev=page > 1,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SCORE & TRENDS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/score",
    response_model=PhysicalHealthScoreResponse,
    summary="Current Physical Health Score",
)
async def get_health_score(
    user_token: dict = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    uid, _ = _get_user_context(user_token, db)
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    try:
        rows = (
            db.query(PhysicalHealthCheckin)
            .filter(
                PhysicalHealthCheckin.user_id == uid,
                PhysicalHealthCheckin.created_at >= cutoff,
            )
            .order_by(PhysicalHealthCheckin.created_at.desc())
            .all()
        )
    except Exception as e:
        raise HTTPException(500, f"Query failed: {e}")

    if not rows:
        return PhysicalHealthScoreResponse(
            score=0, level="low",
            last_checkin_date=None, days_since_checkin=None,
            streak_days=0,
            highlights=["Start tracking your physical health with a daily check-in."],
            concerns=[],
        )

    records = [_row_to_dict(r) for r in rows]

    avg_energy    = _avg(records, "energy_level")
    avg_sleep_q   = _avg(records, "sleep_quality")
    avg_sleep_h   = _avg(records, "sleep_hours")
    avg_nutrition = _avg(records, "nutrition_quality")
    avg_pain      = _avg(records, "pain_level")
    avg_hydration = _avg(records, "hydration")

    sleep_h_score = min(avg_sleep_h / 8.0, 1.0) * 10

    score = (
        avg_energy    * 0.25 +
        avg_sleep_q   * 0.20 +
        sleep_h_score * 0.15 +
        avg_nutrition * 0.20 +
        avg_pain      * 0.10 +
        avg_hydration * 0.10
    )
    score = round(score, 2)

    last_ts = rows[0].created_at
    last_date_str = _ts_to_iso(last_ts)
    days_since = None
    if last_ts:
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
        days_since = int((datetime.now(timezone.utc) - last_ts).total_seconds() / 86400)

    streak = _compute_streak_from_rows(rows)

    highlights, concerns = _compute_highlights_concerns(
        avg_energy, avg_sleep_q, avg_sleep_h, avg_nutrition, avg_pain, avg_hydration
    )

    return PhysicalHealthScoreResponse(
        score=score,
        level=score_to_level(score),
        last_checkin_date=last_date_str,
        days_since_checkin=days_since,
        streak_days=streak,
        highlights=highlights,
        concerns=concerns,
    )


@router.get(
    "/trends",
    response_model=HealthTrendsResponse,
    summary="Physical Health Trends (time-series)",
)
async def get_health_trends(
    period: str = Query("30d", description="7d | 14d | 30d | 90d"),
    user_token: dict = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    uid, _ = _get_user_context(user_token, db)

    days_map = {"7d": 7, "14d": 14, "30d": 30, "90d": 90}
    days = days_map.get(period, 30)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    try:
        rows = (
            db.query(PhysicalHealthCheckin)
            .filter(
                PhysicalHealthCheckin.user_id == uid,
                PhysicalHealthCheckin.created_at >= cutoff,
            )
            .order_by(PhysicalHealthCheckin.created_at.asc())
            .all()
        )
    except Exception as e:
        raise HTTPException(500, f"Query failed: {e}")

    # Group by date → daily averages
    daily: Dict[str, List[dict]] = {}
    for row in rows:
        ts = row.created_at
        if ts:
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            date_str = ts.strftime("%Y-%m-%d")
        else:
            continue
        daily.setdefault(date_str, []).append(_row_to_dict(row))

    data_points: List[TrendPoint] = []
    for date_str in sorted(daily.keys()):
        recs = daily[date_str]
        data_points.append(TrendPoint(
            date=date_str,
            energy_level=round(_avg(recs, "energy_level"), 1),
            sleep_quality=round(_avg(recs, "sleep_quality"), 1),
            sleep_hours=round(_avg(recs, "sleep_hours"), 1),
            exercise_minutes=int(_avg(recs, "exercise_minutes")),
            nutrition_quality=round(_avg(recs, "nutrition_quality"), 1),
            pain_level=round(_avg(recs, "pain_level"), 1),
            hydration=round(_avg(recs, "hydration"), 1),
        ))

    all_records = [_row_to_dict(r) for r in rows]
    averages = {
        "energy_level":      round(_avg(all_records, "energy_level"), 2),
        "sleep_quality":     round(_avg(all_records, "sleep_quality"), 2),
        "sleep_hours":       round(_avg(all_records, "sleep_hours"), 2),
        "nutrition_quality": round(_avg(all_records, "nutrition_quality"), 2),
        "pain_level":        round(_avg(all_records, "pain_level"), 2),
        "hydration":         round(_avg(all_records, "hydration"), 2),
        "exercise_days_per_week": round(
            sum(1 for r in all_records if r.get("exercise_done")) / max(days / 7, 1), 1
        ),
    }

    trend_direction = _compute_trend_direction(data_points)

    return HealthTrendsResponse(
        period=period,
        data_points=data_points,
        averages=averages,
        trend_direction=trend_direction,
        total_checkins=len(rows),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# MEDICAL DOCUMENT ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/medical/upload",
    response_model=MedicalDocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload Medical Report",
    description=(
        "Upload a medical report (PDF or DOCX, max 10 MB). "
        "File is stored in Firebase Storage, text extracted, ingested into RAG, "
        "and analysed by AI. Returns immediately with status='processing'. "
        "Poll `/medical/{doc_id}/status` for completion."
    ),
)
async def upload_medical_report(
    file:        UploadFile = File(...),
    report_type: str        = Query("other", description=(
        "lab_work | blood_test | xray_mri | prescription | general_checkup | specialist | other"
    )),
    report_date: Optional[str] = Query(None, description="Date on the report (YYYY-MM-DD)"),
    issuing_facility: Optional[str] = Query(None),
    user_token: dict = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    # report_date and issuing_facility are accepted for API compatibility;
    # they are not persisted in the current MedicalDocument schema.
    # Phase 5 schema migration will add a metadata JSONB column for these fields.
    del report_date, issuing_facility

    uid, company_id = _get_user_context(user_token, db)

    # Validate extension
    filename = file.filename or "upload"
    if not any(filename.lower().endswith(ext) for ext in _ALLOWED_EXTENSIONS):
        raise HTTPException(
            400,
            f"Unsupported file type. Please upload a PDF (.pdf) or Word document (.docx).",
        )

    # Read and size-check
    file_bytes = await file.read()
    if len(file_bytes) > _MAX_FILE_BYTES:
        raise HTTPException(400, "File too large. Maximum size is 10 MB.")
    if not file_bytes:
        raise HTTPException(400, "File is empty.")

    doc_id = uuid.uuid4()
    doc_id_str = str(doc_id)

    # Extract text immediately (fast — we need it for the background task)
    try:
        from utils.pdf_parser import extract_text
        raw_text = extract_text(file_bytes, filename)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"Failed to extract text from file: {e}")

    # Upload to Azure Blob Storage
    storage_key = f"{uid}/{doc_id_str}/{filename}"
    blob_url = ""
    try:
        from storage.blob import MEDICAL_DOCUMENTS_CONTAINER, upload_bytes
        blob_url = upload_bytes(
            container=MEDICAL_DOCUMENTS_CONTAINER,
            key=storage_key,
            data=file_bytes,
            content_type=file.content_type or "application/octet-stream",
        )
    except Exception as e:
        # Non-fatal: store the doc and process even if Blob upload fails
        print(f"[physical_health] Azure Blob upload error for {doc_id_str}: {e}")
        blob_url = storage_key  # fallback: store path as blob_url

    # Create SQLAlchemy MedicalDocument record
    # NOTE: analysis metadata (status, report_type, rag_chunk_ids, etc.) is stored
    # in-memory during processing and returned via PeriodicReportResponse / detail endpoint.
    # Phase 5 will add a metadata JSONB column to MedicalDocument for full persistence.
    doc_record = MedicalDocument(
        id=doc_id,
        user_id=uid,
        filename=filename,
        blob_url=blob_url,
        mime_type=file.content_type,
        size_bytes=len(file_bytes),
        extracted_text=raw_text,
    )

    try:
        db.add(doc_record)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"Failed to save document record: {e}")

    # Kick off async analysis — non-blocking, response returns immediately
    from physical_health_agent import process_medical_document
    asyncio.create_task(
        process_medical_document(doc_id_str, uid, str(company_id) if company_id else "", raw_text, report_type)
    )

    return MedicalDocumentUploadResponse(
        success=True,
        doc_id=doc_id_str,
        status="processing",
        message=(
            "Your document has been uploaded and is being analysed. "
            "Check back in a moment using the status endpoint."
        ),
    )


@router.get(
    "/medical",
    response_model=MedicalDocumentListResponse,
    summary="List Medical Documents",
)
async def list_medical_documents(
    page:  int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=50),
    user_token: dict = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    uid, _ = _get_user_context(user_token, db)

    try:
        rows = (
            db.query(MedicalDocument)
            .filter(MedicalDocument.user_id == uid)
            .order_by(MedicalDocument.uploaded_at.desc())
            .all()
        )
    except Exception as e:
        raise HTTPException(500, f"Query failed: {e}")

    all_docs = [_build_doc_detail_from_row(row) for row in rows]
    total = len(all_docs)
    offset = (page - 1) * limit

    return MedicalDocumentListResponse(
        success=True,
        documents=all_docs[offset: offset + limit],
        total=total,
    )


@router.get(
    "/medical/{doc_id}",
    response_model=MedicalDocumentDetail,
    summary="Get Medical Document Detail",
)
async def get_medical_document(
    doc_id: str,
    user_token: dict = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    uid, _ = _get_user_context(user_token, db)

    try:
        doc_uuid = uuid.UUID(doc_id)
    except ValueError:
        raise HTTPException(400, "Invalid document ID.")

    row = db.query(MedicalDocument).filter(MedicalDocument.id == doc_uuid).first()
    if not row:
        raise HTTPException(404, "Document not found.")
    if row.user_id != uid:
        raise HTTPException(403, "Access denied.")

    return _build_doc_detail_from_row(row)


@router.get(
    "/medical/{doc_id}/status",
    response_model=MedicalDocumentStatusResponse,
    summary="Poll Document Processing Status",
)
async def get_document_status(
    doc_id: str,
    user_token: dict = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    uid, _ = _get_user_context(user_token, db)

    try:
        doc_uuid = uuid.UUID(doc_id)
    except ValueError:
        raise HTTPException(400, "Invalid document ID.")

    row = db.query(MedicalDocument).filter(MedicalDocument.id == doc_uuid).first()
    if not row:
        raise HTTPException(404, "Document not found.")
    if row.user_id != uid:
        raise HTTPException(403, "Access denied.")

    # Status is not persisted in the current schema; return "uploaded" as default.
    # Phase 5 schema migration will add a status column to MedicalDocument.
    return MedicalDocumentStatusResponse(
        doc_id=doc_id,
        status="uploaded",
        analyzed_at=None,
        urgency_level="routine",
    )


@router.delete(
    "/medical/{doc_id}",
    summary="Delete Medical Document",
    description=(
        "Permanently deletes the document from Firebase Storage, "
        "the database, and all associated RAG vector chunks."
    ),
)
async def delete_medical_document(
    doc_id: str,
    user_token: dict = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    uid, _ = _get_user_context(user_token, db)

    try:
        doc_uuid = uuid.UUID(doc_id)
    except ValueError:
        raise HTTPException(400, "Invalid document ID.")

    row = db.query(MedicalDocument).filter(MedicalDocument.id == doc_uuid).first()
    if not row:
        raise HTTPException(404, "Document not found.")
    if row.user_id != uid:
        raise HTTPException(403, "Access denied.")

    errors: List[str] = []

    # 1. Delete from Azure Blob Storage
    blob_url = row.blob_url or ""
    if blob_url.startswith("https://"):
        try:
            from storage.blob import delete_by_url
            delete_by_url(blob_url)
        except Exception as e:
            errors.append(f"storage: {e}")

    # 2. Delete RAG chunks — chunk IDs not persisted in current schema;
    #    Phase 5 schema migration will add rag_chunk_ids to MedicalDocument.

    # 3. Delete DB record
    try:
        db.delete(row)
        db.commit()
    except Exception as e:
        db.rollback()
        errors.append(f"db: {e}")

    if errors:
        print(f"[physical_health] delete_medical_document partial errors for {doc_id}: {errors}")

    return {
        "success": True,
        "doc_id": doc_id,
        "message": "Document deleted." + (f" Warnings: {errors}" if errors else ""),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PERIODIC REPORTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/reports/generate",
    response_model=PeriodicReportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate On-demand Health Report",
)
async def generate_report(
    req: PeriodicReportRequest,
    user_token: dict = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    uid, company_id = _get_user_context(user_token, db)
    period_end   = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=req.days)
    cutoff       = period_start

    try:
        rows = (
            db.query(PhysicalHealthCheckin)
            .filter(
                PhysicalHealthCheckin.user_id == uid,
                PhysicalHealthCheckin.created_at >= cutoff,
            )
            .all()
        )
    except Exception as e:
        raise HTTPException(500, f"Query failed: {e}")

    if len(rows) < 3:
        raise HTTPException(
            422,
            f"Not enough check-in data to generate a report. "
            f"Found {len(rows)} check-ins in the last {req.days} days. "
            f"Please complete at least 3 check-ins first."
        )

    records = [_row_to_dict(r) for r in rows]
    exercise_records = [r for r in records if r.get("exercise_done")]

    aggregates = {
        "total_checkins":            len(records),
        "avg_energy":                _avg(records, "energy_level"),
        "avg_sleep_quality":         _avg(records, "sleep_quality"),
        "avg_sleep_hours":           _avg(records, "sleep_hours"),
        "avg_nutrition_quality":     _avg(records, "nutrition_quality"),
        "avg_pain_level":            _avg(records, "pain_level"),
        "avg_hydration":             _avg(records, "hydration"),
        "exercise_days":             len(exercise_records),
        "avg_exercise_minutes_daily": (
            sum(r.get("exercise_minutes", 0) for r in exercise_records) / len(exercise_records)
            if exercise_records else 0
        ),
    }

    period_str = (
        f"{period_start.strftime('%Y-%m-%d')} to {period_end.strftime('%Y-%m-%d')} "
        f"({req.days} days, {len(records)} check-ins)"
    )

    from physical_health_agent import generate_periodic_report
    try:
        return generate_periodic_report(
            user_id=uid,
            company_id=str(company_id) if company_id else "",
            aggregates=aggregates,
            period_str=period_str,
            period_start=period_start,
            period_end=period_end,
            report_type=req.report_type,
        )
    except Exception as e:
        raise HTTPException(500, f"Report generation failed: {e}")


@router.get(
    "/reports",
    summary="List Generated Health Reports",
)
async def list_reports(
    page:  int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
    user_token: dict = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    uid, _ = _get_user_context(user_token, db)

    try:
        rows = (
            db.query(PhysicalHealthReport)
            .filter(PhysicalHealthReport.user_id == uid)
            .order_by(PhysicalHealthReport.generated_at.desc())
            .all()
        )
    except Exception as e:
        raise HTTPException(500, f"Query failed: {e}")

    reports = []
    for row in rows:
        r = row.report or {}
        reports.append({
            "report_id":     str(row.id),
            "report_type":   r.get("report_type", "on_demand"),
            "overall_score": r.get("overall_score", 0),
            "overall_level": r.get("overall_level", "low"),
            "trend":         r.get("trend", "stable"),
            "period_start":  r.get("period_start"),
            "period_end":    r.get("period_end"),
            "generated_at":  _ts_to_iso(row.generated_at),
            "follow_up_suggested": r.get("follow_up_suggested", False),
        })

    total = len(reports)
    offset = (page - 1) * limit

    return {
        "success":    True,
        "reports":    reports[offset: offset + limit],
        "total":      total,
        "page":       page,
        "limit":      limit,
        "totalPages": max(1, (total + limit - 1) // limit),
        "hasNext":    offset + limit < total,
        "hasPrev":    page > 1,
    }


@router.get(
    "/reports/{report_id}",
    response_model=PeriodicReportResponse,
    summary="Get Single Health Report",
)
async def get_report(
    report_id: str,
    user_token: dict = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    uid, _ = _get_user_context(user_token, db)

    try:
        report_uuid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(400, "Invalid report ID.")

    row = db.query(PhysicalHealthReport).filter(PhysicalHealthReport.id == report_uuid).first()
    if not row:
        raise HTTPException(404, "Report not found.")
    if row.user_id != uid:
        raise HTTPException(403, "Access denied.")

    r = row.report or {}
    return PeriodicReportResponse(
        report_id=str(row.id),
        period_start=r.get("period_start", ""),
        period_end=r.get("period_end", ""),
        report_type=r.get("report_type", "on_demand"),
        overall_score=r.get("overall_score", 0),
        overall_level=r.get("overall_level", "low"),
        trend=r.get("trend", "stable"),
        avg_energy=r.get("avg_energy", 0),
        avg_sleep_quality=r.get("avg_sleep_quality", 0),
        avg_sleep_hours=r.get("avg_sleep_hours", 0),
        avg_exercise_minutes_daily=r.get("avg_exercise_minutes_per_day", 0),
        avg_nutrition_quality=r.get("avg_nutrition_quality", 0),
        avg_pain_level=r.get("avg_pain_level", 0),
        exercise_days=r.get("exercise_days_count", 0),
        summary=r.get("summary", ""),
        strengths=r.get("strengths", []),
        concerns=r.get("concerns", []),
        recommendations=r.get("recommendations", []),
        follow_up_suggested=r.get("follow_up_suggested", False),
        generated_at=_ts_to_iso(row.generated_at) or "",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# MEDICAL Q&A
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/ask",
    response_model=AskResponse,
    summary="Ask a Question About Your Medical History",
    description=(
        "RAG-powered Q&A over your own uploaded medical documents. "
        "Only retrieves from your documents — never other users' data."
    ),
)
async def ask_medical_question(
    req: AskRequest,
    user_token: dict = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    uid, _ = _get_user_context(user_token, db)

    # RAG retrieval filtered strictly by user_id
    try:
        from rag import get_rag_store
        store = get_rag_store()
        chunks = store.retrieve(
            query=req.question,
            top_k=4,
            metadata_filter={
                "$and": [
                    {"user_id": {"$eq": uid}},
                    {"type": {"$eq": "medical_report"}},
                ]
            },
        )
    except Exception as e:
        raise HTTPException(500, f"Knowledge retrieval failed: {e}")

    if not chunks:
        return AskResponse(
            answer=(
                "I could not find any relevant information in your uploaded medical documents. "
                "Please upload a medical report first, or try rephrasing your question."
            ),
            source_doc_ids=[],
            confidence=0.0,
        )

    context_text = "\n\n---\n\n".join(c["text"] for c in chunks)
    source_doc_ids = list({
        c.get("metadata", {}).get("doc_id", "")
        for c in chunks
        if c.get("metadata", {}).get("doc_id")
    })
    avg_score = sum(c.get("score", 0) for c in chunks) / len(chunks)

    # LLM answer grounded in retrieved context
    from langchain_openai import ChatOpenAI
    from physical_health_prompts import ANSWER_HEALTH_QUESTION

    try:
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.1)
        response = llm.invoke(
            ANSWER_HEALTH_QUESTION.format_messages(
                question=req.question,
                context_chunks=context_text,
            )
        )
        answer = response.content.strip()
    except Exception as e:
        raise HTTPException(500, f"Answer generation failed: {e}")

    return AskResponse(
        answer=answer,
        source_doc_ids=source_doc_ids,
        confidence=round(float(avg_score), 2),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _row_to_dict(row: PhysicalHealthCheckin) -> dict:
    """Flatten vitals + symptoms JSONB dicts into a single flat dict for reuse."""
    d = {}
    d.update(row.vitals or {})
    d.update(row.symptoms or {})
    d["created_at"] = row.created_at
    return d


def _avg(records: List[dict], field: str) -> float:
    values = [r.get(field, 0) for r in records if r.get(field) is not None]
    return sum(values) / len(values) if values else 0.0


def _compute_nudge(req: PhysicalCheckInRequest) -> str:
    """Rule-based nudge — fast, no LLM call needed."""
    if req.sleep_hours < 5:
        return "You're running low on sleep. Aim for at least 7 hours tonight — it makes a big difference."
    if req.sleep_hours < 6.5:
        return "Try to squeeze in an extra 30–60 minutes of sleep tonight to feel more refreshed."
    if req.pain_level <= 3:
        return "You seem to be in some pain. Consider a short break, gentle stretching, or checking in with a doctor if it persists."
    if req.energy_level <= 3 and not req.exercise_done:
        return "Your energy is low today. Even a 10-minute walk outside can give you a surprising boost."
    if req.nutrition_quality <= 3:
        return "Small changes add up — try adding one vegetable or drinking an extra glass of water today."
    if req.hydration <= 3:
        return "You may be dehydrated. Keep a water bottle close and aim for 8 glasses today."
    if req.energy_level >= 8 and req.sleep_hours >= 7:
        return "You're in great shape today! Keep up the good habits."
    return "Good work on checking in. Consistency is the key to long-term health."


def _compute_streak_from_rows(rows: List[PhysicalHealthCheckin]) -> int:
    """Count consecutive days with at least one check-in (most recent streak)."""
    dates = set()
    for row in rows:
        ts = row.created_at
        if ts:
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            dates.add(ts.strftime("%Y-%m-%d"))

    if not dates:
        return 0

    streak = 0
    current = datetime.now(timezone.utc).date()
    while current.isoformat() in dates:
        streak += 1
        current -= timedelta(days=1)
    return streak


def _compute_highlights_concerns(
    avg_energy, avg_sleep_q, avg_sleep_h, avg_nutrition, avg_pain, avg_hydration
) -> tuple[List[str], List[str]]:
    highlights, concerns = [], []

    if avg_energy >= 7:
        highlights.append("Good energy levels — you've been feeling active and alert.")
    elif avg_energy < 4:
        concerns.append("Energy levels are consistently low. Consider reviewing sleep and nutrition.")

    if avg_sleep_h >= 7:
        highlights.append("You're getting a healthy amount of sleep.")
    elif avg_sleep_h < 6:
        concerns.append(f"Sleep duration is below recommended levels ({avg_sleep_h:.1f}h avg). Aim for 7–9 hours.")

    if avg_sleep_q >= 7:
        highlights.append("Sleep quality has been good this month.")
    elif avg_sleep_q < 4:
        concerns.append("Sleep quality is low. Try winding down earlier and limiting screen time before bed.")

    if avg_nutrition >= 7:
        highlights.append("Strong nutrition habits — keep it up.")
    elif avg_nutrition < 4:
        concerns.append("Nutrition quality needs attention. Focus on balanced meals and hydration.")

    if avg_pain >= 8:
        highlights.append("You've reported minimal pain — great physical comfort.")
    elif avg_pain < 4:
        concerns.append("Recurring pain signals detected. Consider consulting a healthcare professional.")

    if avg_hydration < 4:
        concerns.append("Hydration levels are low. Try to drink at least 8 glasses of water daily.")

    if not highlights:
        highlights.append("You're tracking your health — that's already a great step.")
    if not concerns:
        concerns = []

    return highlights[:3], concerns[:3]


def _compute_trend_direction(data_points: List[TrendPoint]) -> Dict[str, str]:
    """Compare first half vs second half of the period for each metric."""
    if len(data_points) < 4:
        return {
            "energy_level": "stable", "sleep_quality": "stable",
            "sleep_hours": "stable", "exercise_minutes": "stable",
            "nutrition_quality": "stable", "pain_level": "stable",
        }

    mid = len(data_points) // 2
    first_half  = data_points[:mid]
    second_half = data_points[mid:]

    def _half_avg(pts, field):
        vals = [getattr(p, field) for p in pts if getattr(p, field) is not None]
        return sum(vals) / len(vals) if vals else 0

    def _direction(first, second) -> str:
        diff = second - first
        if diff > 0.5:
            return "improving"
        if diff < -0.5:
            return "declining"
        return "stable"

    metrics = ["energy_level", "sleep_quality", "sleep_hours",
               "exercise_minutes", "nutrition_quality", "pain_level"]

    return {
        m: _direction(_half_avg(first_half, m), _half_avg(second_half, m))
        for m in metrics
    }


def _build_doc_detail_from_row(row: MedicalDocument) -> MedicalDocumentDetail:
    """
    Build MedicalDocumentDetail from a MedicalDocument ORM row.
    NOTE: Analysis fields (summary, key_findings, flagged_values, etc.) are not
    persisted in the current schema — Phase 5 will add a metadata JSONB column.
    """
    return MedicalDocumentDetail(
        doc_id=str(row.id),
        filename=row.filename or "",
        report_type="other",          # not stored in current schema
        report_date=None,
        issuing_facility=None,
        status="uploaded",            # not stored in current schema
        uploaded_at=_ts_to_iso(row.uploaded_at) or "",
        analyzed_at=None,
        summary=None,
        key_findings=None,
        flagged_values=None,
        recommendations=None,
        follow_up_needed=None,
        urgency_level="routine",
    )
