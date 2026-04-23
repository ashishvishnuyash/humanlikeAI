"""
Bulk Employee Import via File Upload
=====================================

Endpoints:
  GET  /api/employees/import/template            → Download CSV template
  POST /api/employees/import                     → Upload file, start async import job
  GET  /api/employees/import/{job_id}            → Poll job status / progress
  POST /api/employees/import/{job_id}/resend-invites → Resend failed invite emails

Flow:
  1. Employer uploads CSV or XLSX
  2. File is parsed + validated synchronously (no Firebase calls)
  3. If errors → return 400 with full error list immediately
  4. If dry_run=True → return validation preview, no job created
  5. If valid → create Postgres import_jobs row, kick off background worker, return 202
  6. Background worker creates user rows, generates invite links,
     sends emails, and updates job progress
  7. Employer polls GET /{job_id} for live progress
  8. On completion, a results CSV is generated and stored in Firebase Storage
     (Firebase Storage kept until Phase 5 — see TODO comments below)
"""

from __future__ import annotations

import csv
import io
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db.session import get_session
from db.models.user import User
from db.models.company import Company
from routers.auth import get_employer_user
from utils.import_parser import ParsedEmployee, parse_file
from utils.import_jobs import (
    STATUS_CREATING, STATUS_DONE, STATUS_FAILED, STATUS_PENDING,
    append_result, create_job, flush_results, get_job, update_job,
)
from utils.email import send_invite_email
from routers.users import _default_permissions

router = APIRouter(prefix="/employees", tags=["Employee Import"])


# ── Response models ───────────────────────────────────────────────────────────

class ValidationErrorItem(BaseModel):
    row_number: int
    column: str
    value: str
    message: str


class ImportStartResponse(BaseModel):
    job_id: str
    status: str
    total_rows: int
    message: str
    poll_url: str


class ImportValidationResponse(BaseModel):
    valid: bool
    total_rows: int
    valid_rows: int
    error_count: int
    errors: List[ValidationErrorItem]
    duplicate_emails: List[str]
    preview: Optional[List[dict]] = None
    message: str


class ImportStatusResponse(BaseModel):
    job_id: str
    status: str
    total_rows: int
    processed: int
    created_count: int
    failed_count: int
    skipped_count: int
    progress_pct: float
    results_csv_url: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]


class ResendInvitesRequest(BaseModel):
    emails: Optional[List[str]] = None    # None = resend all that failed


class ResendInvitesResponse(BaseModel):
    resent: int
    failed: int
    details: List[dict]


# ── Template CSV ─────────────────────────────────────────────────────────────

TEMPLATE_ROWS = [
    {
        "email": "john.doe@company.com",
        "first_name": "John",
        "last_name": "Doe",
        "role": "employee",
        "department": "Engineering",
        "position": "Backend Developer",
        "phone": "+919876543210",
        "manager_email": "manager@company.com",
        "hierarchy_level": "3",
    },
    {
        "email": "jane.smith@company.com",
        "first_name": "Jane",
        "last_name": "Smith",
        "role": "manager",
        "department": "Engineering",
        "position": "Engineering Manager",
        "phone": "",
        "manager_email": "cto@company.com",
        "hierarchy_level": "2",
    },
    {
        "email": "ravi.kumar@company.com",
        "first_name": "Ravi",
        "last_name": "Kumar",
        "role": "hr",
        "department": "People",
        "position": "HR Generalist",
        "phone": "",
        "manager_email": "",
        "hierarchy_level": "2",
    },
]

TEMPLATE_HEADERS = [
    "email", "first_name", "last_name", "role",
    "department", "position", "phone", "manager_email", "hierarchy_level",
]


@router.get(
    "/import/template",
    summary="Download Import Template",
    description="Returns a CSV file with the required column headers and 3 example rows.",
)
async def download_import_template(employer: dict = Depends(get_employer_user)):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=TEMPLATE_HEADERS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(TEMPLATE_ROWS)
    buf.seek(0)

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=employee_import_template.csv"},
    )


# ── Upload + start job ────────────────────────────────────────────────────────

@router.post(
    "/import",
    summary="Upload Employee File & Start Import",
    description=(
        "Upload a .csv or .xlsx file to bulk-create employees. "
        "Set dry_run=true to validate only without creating accounts. "
        "On success returns a job_id — poll GET /import/{job_id} for progress."
    ),
)
async def start_import(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description=".csv or .xlsx file"),
    dry_run: bool = Form(False, description="Validate only — no accounts created"),
    employer: dict = Depends(get_employer_user),
):
    # ── Read file bytes ───────────────────────────────────────────────────────
    file_bytes = await file.read()

    # ── Parse & validate ──────────────────────────────────────────────────────
    result = parse_file(file_bytes, file.filename or "upload")

    # Always return errors immediately — employer must fix before proceeding
    if result.errors:
        error_items = [
            ValidationErrorItem(
                row_number=e.row_number,
                column=e.column,
                value=e.value,
                message=e.message,
            )
            for e in result.errors
        ]
        return ImportValidationResponse(
            valid=False,
            total_rows=result.total_rows,
            valid_rows=len(result.valid_rows),
            error_count=len(result.errors),
            errors=error_items,
            duplicate_emails=result.duplicate_emails,
            message=f"Found {len(result.errors)} error(s). Fix them and re-upload.",
        )

    # ── Dry run — return preview without creating anything ────────────────────
    if dry_run:
        preview = [
            {
                "row": r.row_number,
                "email": r.email,
                "name": f"{r.first_name} {r.last_name}",
                "role": r.role,
                "department": r.department,
            }
            for r in result.valid_rows[:5]
        ]
        return ImportValidationResponse(
            valid=True,
            total_rows=result.total_rows,
            valid_rows=len(result.valid_rows),
            error_count=0,
            errors=[],
            duplicate_emails=[],
            preview=preview,
            message=(
                f"File is valid. {len(result.valid_rows)} employee(s) ready to import. "
                "Remove dry_run=true to proceed."
            ),
        )

    # ── Create job and kick off background worker ─────────────────────────────
    company_id   = employer.get("company_id", "")
    employer_uid = employer.get("id", employer.get("uid", ""))

    job_id = create_job(
        company_id=company_id,
        created_by=employer_uid,
        total_rows=len(result.valid_rows),
    )

    background_tasks.add_task(
        run_import_job,
        job_id=job_id,
        rows=result.valid_rows,
        employer=employer,
    )

    return ImportStartResponse(
        job_id=job_id,
        status=STATUS_PENDING,
        total_rows=len(result.valid_rows),
        message=f"Import started for {len(result.valid_rows)} employee(s). Poll the URL below for progress.",
        poll_url=f"/api/employees/import/{job_id}",
    )


# ── Background worker ─────────────────────────────────────────────────────────

async def run_import_job(
    job_id: str,
    rows: List[ParsedEmployee],
    employer: dict,
) -> None:
    """
    Background task: create Postgres user rows, generate invite links,
    send emails, and update job progress.
    One bad row never aborts the whole job.
    """
    from db.session import get_session_factory

    update_job(job_id, status=STATUS_CREATING)

    company_id_str = employer.get("company_id", "")
    company_name   = employer.get("company_name", "")
    employer_uid   = employer.get("id", employer.get("uid", ""))
    employer_name  = employer.get("display_name", "")

    try:
        company_uuid = uuid.UUID(company_id_str)
    except (ValueError, AttributeError):
        update_job(job_id, status=STATUS_FAILED)
        print(f"[import_job] Invalid company_id UUID: {company_id_str!r}")
        return

    created_count = 0
    failed_count  = 0
    skipped_count = 0
    batch_results: List[dict] = []

    SessionLocal = get_session_factory()

    # ── Build email → uid map for existing company members ───────────────────
    # Used to resolve manager_email → manager_id
    email_to_uid: Dict[str, str] = {}
    try:
        with SessionLocal() as db:
            existing = (
                db.query(User)
                .filter(User.company_id == company_uuid)
                .all()
            )
            for u in existing:
                if u.email:
                    email_to_uid[u.email.lower()] = u.id
    except Exception as e:
        print(f"[import_job] Could not pre-load email map: {e}")

    # Also track emails created in this job (for intra-file manager resolution)
    created_in_job: Dict[str, str] = {}   # email → uid

    for row in rows:
        result_entry: Dict[str, Any] = {
            "row_number":  row.row_number,
            "email":       row.email,
            "first_name":  row.first_name,
            "last_name":   row.last_name,
            "role":        row.role,
            "success":     False,
            "uid":         None,
            "invite_sent": False,
            "error":       None,
            "status":      "failed",
        }

        try:
            # ── Check if email already exists in Postgres ─────────────────────
            with SessionLocal() as db:
                existing_user = (
                    db.query(User)
                    .filter(User.email == row.email)
                    .one_or_none()
                )

            if existing_user is not None:
                result_entry["status"] = "skipped_duplicate"
                result_entry["error"]  = "Email already registered."
                skipped_count += 1
                batch_results.append(result_entry)
                continue

            # ── Generate UID (no Firebase Auth — user resets password on first login) ──
            uid = str(uuid.uuid4())
            result_entry["uid"] = uid
            created_in_job[row.email] = uid

            # ── Generate invite link via Firebase (kept until Phase 5) ────────
            # TODO: Phase 5 - Azure Blob / Azure AD B2C invite link generation
            from firebase_admin import auth as fb_auth  # TODO: Phase 5 - Azure Blob
            invite_link = fb_auth.generate_password_reset_link(row.email)  # TODO: Phase 5 - Azure Blob

            # ── Resolve manager_email → manager_id ────────────────────────────
            manager_id: Optional[str] = None

            if row.manager_email:
                mgr_uid = (
                    email_to_uid.get(row.manager_email.lower())
                    or created_in_job.get(row.manager_email)
                )
                if mgr_uid:
                    manager_id = mgr_uid
                else:
                    print(
                        f"[import_job] row {row.row_number}: "
                        f"manager_email {row.manager_email!r} not found — skipping manager link"
                    )

            # ── Compute hierarchy_level ───────────────────────────────────────
            hierarchy_level = row.hierarchy_level
            if hierarchy_level is None:
                # Attempt to derive from manager
                if manager_id:
                    try:
                        with SessionLocal() as db:
                            mgr = db.get(User, manager_id)
                            mgr_level = (mgr.profile or {}).get("hierarchy_level") if mgr else None
                            hierarchy_level = (mgr_level + 1) if mgr_level else 1
                    except Exception:
                        hierarchy_level = 1
                else:
                    hierarchy_level = 1

            # ── Build default permissions ─────────────────────────────────────
            perms = _default_permissions(row.role)

            # ── Write Postgres user row ───────────────────────────────────────
            profile: Dict[str, Any] = {
                "first_name":      row.first_name,
                "last_name":       row.last_name,
                "display_name":    f"{row.first_name} {row.last_name}",
                "position":        row.position,
                "phone":           row.phone,
                "company_name":    company_name,
                "hierarchy_level": hierarchy_level,
                "reporting_chain": [],
                "created_by":      employer_uid,
                "import_job_id":   job_id,
                **perms,
            }

            new_user = User(
                id=uid,
                email=row.email,
                password_hash=None,   # user sets password via invite link on first login
                role=row.role,
                company_id=company_uuid,
                manager_id=manager_id,
                department=row.department,
                is_active=True,
                profile=profile,
            )

            with SessionLocal() as db:
                db.add(new_user)
                db.commit()

            # Register in local maps so later rows in same file can resolve this as a manager
            email_to_uid[row.email.lower()] = uid

            # ── Send invite email ─────────────────────────────────────────────
            invite_sent = send_invite_email(
                to_email=row.email,
                first_name=row.first_name,
                company_name=company_name,
                invite_link=invite_link,
                sender_name=employer_name,
            )

            result_entry.update({
                "success":     True,
                "invite_sent": invite_sent,
                "status":      "created",
            })
            created_count += 1

        except Exception as e:
            err_msg = str(e)
            print(f"[import_job] row {row.row_number} ({row.email}) failed: {err_msg}")
            result_entry["error"] = err_msg
            failed_count += 1

        finally:
            batch_results.append(result_entry)

        # Flush results and update progress every 10 rows
        if len(batch_results) >= 10:
            processed_so_far = created_count + failed_count + skipped_count
            flush_results(job_id, batch_results)
            update_job(
                job_id,
                processed=processed_so_far,
                created_count=created_count,
                failed_count=failed_count,
                skipped_count=skipped_count,
            )
            batch_results = []

    # Flush any remaining results
    if batch_results:
        flush_results(job_id, batch_results)

    # ── Increment company employee_count ──────────────────────────────────────
    if created_count > 0:
        try:
            with SessionLocal() as db:
                db.query(Company).filter(Company.id == company_uuid).update(
                    {"employee_count": Company.employee_count + created_count}
                )
                db.commit()
        except Exception as e:
            print(f"[import_job] company count update error: {e}")

    # ── Generate results CSV and upload to Firebase Storage ───────────────────
    results_csv_url = None
    try:
        results_csv_url = await _upload_results_csv(job_id, company_id_str)
    except Exception as e:
        print(f"[import_job] results CSV upload error: {e}")

    # ── Mark job done ─────────────────────────────────────────────────────────
    final_status = STATUS_DONE if failed_count < len(rows) else STATUS_FAILED
    update_job(
        job_id,
        status=final_status,
        processed=len(rows),
        created_count=created_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        results_csv_url=results_csv_url,
    )

    print(
        f"[import_job] {job_id} complete — "
        f"created={created_count}, failed={failed_count}, skipped={skipped_count}"
    )


# ── Poll job status ───────────────────────────────────────────────────────────

@router.get(
    "/import/{job_id}",
    response_model=ImportStatusResponse,
    summary="Poll Import Job Status",
    description="Check the progress of a running import job. Poll every 2-3 seconds from the frontend.",
)
async def get_import_status(
    job_id: str,
    employer: dict = Depends(get_employer_user),
):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Import job not found.")

    if job.get("company_id") != employer.get("company_id"):
        raise HTTPException(403, "Access denied.")

    total     = job.get("total_rows", 0)
    processed = job.get("processed", 0)
    progress  = round((processed / total * 100) if total > 0 else 0, 1)

    def _ts(val) -> Optional[str]:
        if val is None:
            return None
        if isinstance(val, datetime):
            return val.astimezone(timezone.utc).isoformat()
        if hasattr(val, "timestamp"):
            return datetime.fromtimestamp(val.timestamp(), tz=timezone.utc).isoformat()
        return str(val)

    return ImportStatusResponse(
        job_id=job_id,
        status=job.get("status", STATUS_PENDING),
        total_rows=total,
        processed=processed,
        created_count=job.get("created_count", 0),
        failed_count=job.get("failed_count", 0),
        skipped_count=job.get("skipped_count", 0),
        progress_pct=progress,
        results_csv_url=job.get("results_csv_url"),
        created_at=_ts(job.get("created_at")),
        updated_at=_ts(job.get("updated_at")),
    )


# ── Resend invite emails ──────────────────────────────────────────────────────

@router.post(
    "/import/{job_id}/resend-invites",
    response_model=ResendInvitesResponse,
    summary="Resend Invite Emails",
    description=(
        "Regenerate invite links and resend emails for employees whose "
        "invite was not sent. Pass specific emails to target them, or omit to resend all failed invites."
    ),
)
async def resend_invites(
    job_id: str,
    req: ResendInvitesRequest,
    employer: dict = Depends(get_employer_user),
    db: Session = Depends(get_session),
):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Import job not found.")
    if job.get("company_id") != employer.get("company_id"):
        raise HTTPException(403, "Access denied.")
    if job.get("status") not in (STATUS_DONE, STATUS_FAILED):
        raise HTTPException(400, "Job is still running. Wait for it to complete before resending.")

    company_name  = employer.get("company_name", "")
    employer_name = employer.get("display_name", "")

    # Determine which entries to resend
    target_emails = {e.lower() for e in req.emails} if req.emails else None
    results: List[dict] = job.get("results", [])

    to_resend = [
        r for r in results
        if r.get("success")                             # only successfully created accounts
        and not r.get("invite_sent", False)             # only those whose email failed
        and (target_emails is None or r["email"].lower() in target_emails)
    ]

    if not to_resend:
        return ResendInvitesResponse(resent=0, failed=0, details=[])

    resent = 0
    failed = 0
    details: List[dict] = []

    for entry in to_resend:
        email      = entry["email"]
        first_name = entry.get("first_name", "")
        detail: dict = {"email": email, "success": False}

        try:
            # TODO: Phase 5 - Azure Blob / Azure AD B2C invite link generation
            from firebase_admin import auth as fb_auth  # TODO: Phase 5 - Azure Blob
            invite_link = fb_auth.generate_password_reset_link(email)  # TODO: Phase 5 - Azure Blob

            sent = send_invite_email(
                to_email=email,
                first_name=first_name,
                company_name=company_name,
                invite_link=invite_link,
                sender_name=employer_name,
            )
            if sent:
                resent += 1
                detail["success"] = True
                # Update invite_sent flag for this email in the job's results array
                try:
                    from utils.import_jobs import flush_results as _flush
                    from db.models.imports import ImportJob
                    import uuid as _uuid
                    job_obj = db.get(ImportJob, _uuid.UUID(job_id))
                    if job_obj is not None:
                        updated_results = list(job_obj.errors or [])
                        for r in updated_results:
                            if r.get("email") == email:
                                r["invite_sent"] = True
                        job_obj.errors = updated_results
                        db.add(job_obj)
                        db.commit()
                except Exception as e:
                    print(f"[resend_invites] result flag update error: {e}")
            else:
                failed += 1
                detail["error"] = "Email delivery failed."
        except Exception as e:
            failed += 1
            detail["error"] = str(e)

        details.append(detail)

    return ResendInvitesResponse(resent=resent, failed=failed, details=details)


# ── Results CSV helper ────────────────────────────────────────────────────────

async def _upload_results_csv(job_id: str, company_id: str) -> Optional[str]:
    """
    Generate a results CSV from the completed job and upload to Firebase Storage.
    Returns the signed download URL (valid 7 days), or None if upload fails.
    """
    job = get_job(job_id)
    if not job:
        return None

    results: List[dict] = job.get("results", [])
    if not results:
        return None

    # Build CSV in memory
    buf = io.StringIO()
    fieldnames = ["row_number", "email", "first_name", "last_name", "role", "status", "uid", "invite_sent", "error"]
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(results)
    csv_bytes = buf.getvalue().encode("utf-8")

    # Upload to Firebase Storage
    # TODO: Phase 5 - Azure Blob (replace Firebase Storage with Azure Blob Storage)
    bucket_name = os.environ.get("FIREBASE_STORAGE_BUCKET")
    if not bucket_name:
        print("[import_job] FIREBASE_STORAGE_BUCKET not set — skipping results CSV upload")
        return None

    try:
        from firebase_admin import storage  # TODO: Phase 5 - Azure Blob
        import datetime as dt

        bucket = storage.bucket(bucket_name)  # TODO: Phase 5 - Azure Blob
        blob   = bucket.blob(f"import_results/{company_id}/{job_id}.csv")  # TODO: Phase 5 - Azure Blob
        blob.upload_from_string(csv_bytes, content_type="text/csv")  # TODO: Phase 5 - Azure Blob

        # Signed URL valid for 7 days
        url = blob.generate_signed_url(  # TODO: Phase 5 - Azure Blob
            expiration=dt.timedelta(days=7),
            method="GET",
        )
        return url
    except Exception as e:
        print(f"[import_job] Firebase Storage upload error: {e}")
        return None
