"""
Import job store — SQLAlchemy-backed async job tracking.

Table: import_jobs

Job lifecycle:
  pending → validating → creating → done
                                  ↘ failed
"""

from __future__ import annotations

import uuid
from typing import Any, List, Optional

from db.session import get_session_factory
from db.models.imports import ImportJob


# ── Job status constants ──────────────────────────────────────────────────────

STATUS_PENDING     = "pending"
STATUS_VALIDATING  = "validating"
STATUS_CREATING    = "creating"
STATUS_DONE        = "done"
STATUS_FAILED      = "failed"


# ── CRUD helpers ──────────────────────────────────────────────────────────────

def create_job(company_id: str, created_by: str, total_rows: int) -> str:
    """
    Create a new import job row in Postgres.
    Returns the job_id as a string.
    """
    try:
        company_uuid = uuid.UUID(company_id)
    except (ValueError, AttributeError):
        raise ValueError(f"Invalid company_id UUID: {company_id!r}")

    job_id = uuid.uuid4()

    job = ImportJob(
        id=job_id,
        company_id=company_uuid,
        created_by=created_by,
        status=STATUS_PENDING,
        stats={
            "total_rows":    total_rows,
            "processed":     0,
            "created_count": 0,
            "failed_count":  0,
            "skipped_count": 0,
        },
        errors=[],
        blob_url=None,
    )

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        db.add(job)
        db.commit()

    return str(job_id)


def get_job(job_id: str) -> Optional[dict]:
    """
    Fetch a job row by ID and return a dict compatible with the old Firestore shape.
    Returns None if not found.
    """
    try:
        job_uuid = uuid.UUID(job_id)
    except (ValueError, AttributeError):
        return None

    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        job = db.get(ImportJob, job_uuid)
        if job is None:
            return None

        stats = job.stats or {}
        errors_data = job.errors or []

        return {
            "job_id":          str(job.id),
            "company_id":      str(job.company_id) if job.company_id else None,
            "created_by":      job.created_by,
            "status":          job.status,
            "total_rows":      stats.get("total_rows", 0),
            "processed":       stats.get("processed", 0),
            "created_count":   stats.get("created_count", 0),
            "failed_count":    stats.get("failed_count", 0),
            "skipped_count":   stats.get("skipped_count", 0),
            # results list lives in errors JSONB column
            "results":         errors_data if isinstance(errors_data, list) else [],
            "results_csv_url": job.blob_url,
            "created_at":      job.created_at,
            "updated_at":      job.updated_at,
        }


def update_job(job_id: str, **fields: Any) -> None:
    """
    Partial update on a job row.
    Accepts: status, processed, created_count, failed_count, skipped_count,
             results_csv_url.
    Silently does nothing if job not found.
    """
    try:
        job_uuid = uuid.UUID(job_id)
    except (ValueError, AttributeError):
        return

    # Separate stats-level fields from top-level fields
    stats_keys = {"processed", "created_count", "failed_count", "skipped_count", "total_rows"}

    stats_updates: dict = {}
    top_updates: dict = {}

    for key, val in fields.items():
        if key in stats_keys:
            stats_updates[key] = val
        elif key == "results_csv_url":
            top_updates["blob_url"] = val
        elif key == "status":
            top_updates["status"] = val

    SessionLocal = get_session_factory()
    try:
        with SessionLocal() as db:
            job = db.get(ImportJob, job_uuid)
            if job is None:
                return

            if stats_updates:
                current_stats = dict(job.stats or {})
                current_stats.update(stats_updates)
                job.stats = current_stats

            for k, v in top_updates.items():
                setattr(job, k, v)

            db.add(job)
            db.commit()
    except Exception as e:
        print(f"[import_jobs] update_job error for {job_id}: {e}")


def append_result(job_id: str, result_entry: dict) -> None:
    """
    Append a single result entry to the job's errors (results) JSONB array.
    """
    flush_results(job_id, [result_entry])


def flush_results(job_id: str, result_entries: List[dict]) -> None:
    """
    Append a batch of result entries at once to the errors JSONB column.
    More efficient than calling append_result in a tight loop.
    """
    if not result_entries:
        return

    try:
        job_uuid = uuid.UUID(job_id)
    except (ValueError, AttributeError):
        return

    SessionLocal = get_session_factory()
    try:
        with SessionLocal() as db:
            job = db.get(ImportJob, job_uuid)
            if job is None:
                return

            current = list(job.errors) if isinstance(job.errors, list) else []
            current.extend(result_entries)
            job.errors = current

            db.add(job)
            db.commit()
    except Exception as e:
        print(f"[import_jobs] flush_results error for {job_id}: {e}")
