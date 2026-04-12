"""
Import job store — Firestore-backed async job tracking.

Collection: import_jobs/{job_id}

Job lifecycle:
  pending → validating → creating → done
                                  ↘ failed
"""

from __future__ import annotations

import uuid
from typing import Any, List, Optional

from firebase_config import get_db
from google.cloud.firestore_v1 import SERVER_TIMESTAMP


# ── Job status constants ──────────────────────────────────────────────────────

STATUS_PENDING     = "pending"
STATUS_VALIDATING  = "validating"
STATUS_CREATING    = "creating"
STATUS_DONE        = "done"
STATUS_FAILED      = "failed"


# ── CRUD helpers ──────────────────────────────────────────────────────────────

def create_job(company_id: str, created_by: str, total_rows: int) -> str:
    """
    Create a new import job document in Firestore.
    Returns the job_id.
    """
    db = get_db()
    if not db:
        raise RuntimeError("Database unavailable — cannot create import job.")

    job_id = str(uuid.uuid4())
    db.collection("import_jobs").document(job_id).set({
        "job_id":            job_id,
        "company_id":        company_id,
        "created_by":        created_by,
        "status":            STATUS_PENDING,
        "total_rows":        total_rows,
        "processed":         0,
        "created_count":     0,
        "failed_count":      0,
        "skipped_count":     0,
        "validation_errors": [],
        "results":           [],
        "results_csv_url":   None,
        "created_at":        SERVER_TIMESTAMP,
        "updated_at":        SERVER_TIMESTAMP,
    })
    return job_id


def get_job(job_id: str) -> Optional[dict]:
    """Fetch a job document by ID. Returns None if not found."""
    db = get_db()
    if not db:
        return None
    doc = db.collection("import_jobs").document(job_id).get()
    return doc.to_dict() if doc.exists else None


def update_job(job_id: str, **fields: Any) -> None:
    """
    Partial update on a job document.
    Always stamps updated_at.
    Silently does nothing if db is unavailable.
    """
    db = get_db()
    if not db:
        return
    fields["updated_at"] = SERVER_TIMESTAMP
    try:
        db.collection("import_jobs").document(job_id).update(fields)
    except Exception as e:
        print(f"[import_jobs] update_job error for {job_id}: {e}")


def append_result(job_id: str, result_entry: dict) -> None:
    """
    Append a single result entry to the job's results array.
    Uses Firestore ArrayUnion to avoid race conditions.
    """
    db = get_db()
    if not db:
        return
    try:
        from google.cloud.firestore_v1 import ArrayUnion
        db.collection("import_jobs").document(job_id).update({
            "results":    ArrayUnion([result_entry]),
            "updated_at": SERVER_TIMESTAMP,
        })
    except Exception as e:
        print(f"[import_jobs] append_result error for {job_id}: {e}")


def flush_results(job_id: str, result_entries: List[dict]) -> None:
    """
    Append a batch of result entries at once.
    More efficient than calling append_result in a tight loop.
    """
    if not result_entries:
        return
    db = get_db()
    if not db:
        return
    try:
        from google.cloud.firestore_v1 import ArrayUnion
        db.collection("import_jobs").document(job_id).update({
            "results":    ArrayUnion(result_entries),
            "updated_at": SERVER_TIMESTAMP,
        })
    except Exception as e:
        print(f"[import_jobs] flush_results error for {job_id}: {e}")
