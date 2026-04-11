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
  5. If valid → create Firestore job doc, kick off background worker, return 202
  6. Background worker creates Firebase Auth accounts, generates invite links,
     sends emails, writes Firestore user docs, updates job progress
  7. Employer polls GET /{job_id} for live progress
  8. On completion, a results CSV is generated and stored in Firebase Storage
"""

from __future__ import annotations

import csv
import io
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from firebase_admin import auth as fb_auth
from google.cloud.firestore_v1 import SERVER_TIMESTAMP, ArrayUnion
from pydantic import BaseModel

from firebase_config import get_db
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
    Background task: create Firebase Auth accounts, generate invite links,
    send emails, write Firestore user docs, and update job progress.
    One bad row never aborts the whole job.
    """
    update_job(job_id, status=STATUS_CREATING)

    db = get_db()
    company_id   = employer.get("company_id", "")
    company_name = employer.get("company_name", "")
    employer_uid = employer.get("id", employer.get("uid", ""))
    employer_name = employer.get("display_name", "")

    created_count = 0
    failed_count  = 0
    skipped_count = 0
    batch_results: List[dict] = []

    # ── Build email → uid map for existing company members ───────────────────
    # Used to resolve manager_email → manager_id
    email_to_uid: Dict[str, str] = {}
    if db:
        try:
            existing = db.collection("users").where("company_id", "==", company_id).stream()
            for doc in existing:
                d = doc.to_dict()
                if d.get("email"):
                    email_to_uid[d["email"].lower()] = doc.id
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
            # ── Check if email already exists in Firebase ─────────────────────
            try:
                fb_auth.get_user_by_email(row.email)
                # Already exists — skip
                result_entry["status"] = "skipped_duplicate"
                result_entry["error"]  = "Email already registered in Firebase."
                skipped_count += 1
                batch_results.append(result_entry)
                continue
            except fb_auth.UserNotFoundError:
                pass   # Good — proceed to create

            # ── Create Firebase Auth account (no password) ────────────────────
            fb_user = fb_auth.create_user(
                email=row.email,
                display_name=f"{row.first_name} {row.last_name}",
                email_verified=False,
            )
            uid = fb_user.uid
            result_entry["uid"] = uid
            created_in_job[row.email] = uid

            # ── Generate secure invite link ───────────────────────────────────
            invite_link = fb_auth.generate_password_reset_link(row.email)

            # ── Resolve manager_email → manager_id ────────────────────────────
            manager_id: Optional[str] = None
            manager_level: Optional[int] = None

            if row.manager_email:
                mgr_uid = (
                    email_to_uid.get(row.manager_email)
                    or created_in_job.get(row.manager_email)
                )
                if mgr_uid:
                    manager_id = mgr_uid
                    # Get manager's hierarchy level for auto-assignment
                    if db:
                        try:
                            mgr_doc = db.collection("users").document(mgr_uid).get()
                            if mgr_doc.exists:
                                manager_level = mgr_doc.to_dict().get("hierarchy_level")
                        except Exception:
                            pass
                else:
                    print(f"[import_job] row {row.row_number}: manager_email {row.manager_email!r} not found — skipping manager link")

            # ── Compute hierarchy_level ───────────────────────────────────────
            hierarchy_level = row.hierarchy_level
            if hierarchy_level is None:
                hierarchy_level = (manager_level + 1) if manager_level else 1

            # ── Build default permissions ─────────────────────────────────────
            perms = _default_permissions(row.role)

            # ── Write Firestore user document ─────────────────────────────────
            doc_data: Dict[str, Any] = {
                "id":              uid,
                "email":           row.email,
                "first_name":      row.first_name,
                "last_name":       row.last_name,
                "display_name":    f"{row.first_name} {row.last_name}",
                "role":            row.role,
                "department":      row.department,
                "position":        row.position,
                "phone":           row.phone,
                "company_id":      company_id,
                "company_name":    company_name,
                "manager_id":      manager_id,
                "hierarchy_level": hierarchy_level,
                "direct_reports":  [],
                "reporting_chain": [],
                "is_active":       True,
                "created_by":      employer_uid,
                "import_job_id":   job_id,
                "created_at":      SERVER_TIMESTAMP,
                "updated_at":      SERVER_TIMESTAMP,
                **perms,
            }

            if db:
                db.collection("users").document(uid).set(doc_data)
                # Add uid to email_to_uid so later rows in same file can resolve this as manager
                email_to_uid[row.email] = uid

                # Update manager's direct_reports list
                if manager_id:
                    try:
                        db.collection("users").document(manager_id).update({
                            "direct_reports": ArrayUnion([uid]),
                            "updated_at":     SERVER_TIMESTAMP,
                        })
                    except Exception as e:
                        print(f"[import_job] direct_reports update error for manager {manager_id}: {e}")

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

            # Rollback Firebase Auth user if Firestore write failed
            if result_entry.get("uid"):
                try:
                    fb_auth.delete_user(result_entry["uid"])
                except Exception:
                    pass

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
    if created_count > 0 and db:
        try:
            from firebase_admin import firestore as admin_firestore
            db.collection("companies").document(company_id).update({
                "employee_count": admin_firestore.firestore.Increment(created_count),
                "updated_at":     SERVER_TIMESTAMP,
            })
        except Exception as e:
            print(f"[import_job] company count update error: {e}")

    # ── Generate results CSV and upload to Firebase Storage ───────────────────
    results_csv_url = None
    try:
        results_csv_url = await _upload_results_csv(job_id, company_id)
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

    total    = job.get("total_rows", 0)
    processed = job.get("processed", 0)
    progress = round((processed / total * 100) if total > 0 else 0, 1)

    def _ts(val) -> Optional[str]:
        if val is None:
            return None
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
        "Regenerate Firebase invite links and resend emails for employees whose "
        "invite was not sent. Pass specific emails to target them, or omit to resend all failed invites."
    ),
)
async def resend_invites(
    job_id: str,
    req: ResendInvitesRequest,
    employer: dict = Depends(get_employer_user),
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

    db = get_db()

    for entry in to_resend:
        email      = entry["email"]
        first_name = entry.get("first_name", "")
        detail: dict = {"email": email, "success": False}

        try:
            invite_link = fb_auth.generate_password_reset_link(email)
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
                # Update invite_sent flag in the job results array
                # (Firestore doesn't support updating array items in place;
                #  we reload, patch, and write back)
                if db:
                    try:
                        job_ref = db.collection("import_jobs").document(job_id)
                        fresh   = job_ref.get().to_dict() or {}
                        updated_results = fresh.get("results", [])
                        for r in updated_results:
                            if r.get("email") == email:
                                r["invite_sent"] = True
                        job_ref.update({"results": updated_results, "updated_at": SERVER_TIMESTAMP})
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
    bucket_name = os.environ.get("FIREBASE_STORAGE_BUCKET")
    if not bucket_name:
        print("[import_job] FIREBASE_STORAGE_BUCKET not set — skipping results CSV upload")
        return None

    try:
        from firebase_admin import storage
        import datetime as dt

        bucket = storage.bucket(bucket_name)
        blob   = bucket.blob(f"import_results/{company_id}/{job_id}.csv")
        blob.upload_from_string(csv_bytes, content_type="text/csv")

        # Signed URL valid for 7 days
        url = blob.generate_signed_url(
            expiration=dt.timedelta(days=7),
            method="GET",
        )
        return url
    except Exception as e:
        print(f"[import_job] Firebase Storage upload error: {e}")
        return None
