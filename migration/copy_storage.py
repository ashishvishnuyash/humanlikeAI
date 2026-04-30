"""Copy Firebase Storage blobs → Azure Blob Storage, rewriting DB URLs.

Usage::

    python -m migration.copy_storage

Iterates the ``medical_reports/`` and ``import_results/`` prefixes in the
Firebase Storage bucket (read from ``FIREBASE_STORAGE_BUCKET`` env var or
``firebase_config.firebaseConfig['storageBucket']``), re-uploads each blob to
the matching Azure container, and updates ``medical_documents.blob_url`` and
``import_jobs.blob_url`` rows whose old URL matches.

Idempotent: if an Azure blob already exists at the target key, it's
overwritten (cheap; small number of blobs). The DB URL rewrite is guarded by a
``WHERE blob_url LIKE 'https://firebasestorage%'`` filter so already-migrated
rows are skipped.
"""

from __future__ import annotations

import sys
from typing import Dict

from db.models import ImportJob, MedicalDocument
from db.session import get_session_factory
from storage.blob import (
    EMPLOYEE_IMPORTS_CONTAINER,
    MEDICAL_DOCUMENTS_CONTAINER,
    upload_bytes,
)


def _get_firebase_bucket():
    """Return the Firebase Storage bucket handle."""
    from firebase_admin import storage
    try:
        from firebase_config import firebaseConfig
        return storage.bucket(firebaseConfig["storageBucket"])
    except Exception:
        import os
        return storage.bucket(os.environ["FIREBASE_STORAGE_BUCKET"])


def _copy_prefix(
    fb_bucket,
    fb_prefix: str,
    az_container: str,
) -> Dict[str, str]:
    """Copy every blob under ``fb_prefix`` in Firebase → ``az_container`` in Azure.

    Returns ``{old_firebase_url: new_azure_url}`` mapping.
    """
    mapping: Dict[str, str] = {}
    for blob in fb_bucket.list_blobs(prefix=fb_prefix):
        # Strip fb_prefix from the key so azure key doesn't duplicate the prefix.
        if blob.name.startswith(fb_prefix):
            azure_key = blob.name[len(fb_prefix):].lstrip("/")
        else:
            azure_key = blob.name
        if not azure_key:
            continue
        data = blob.download_as_bytes()
        content_type = blob.content_type or "application/octet-stream"
        new_url = upload_bytes(
            container=az_container,
            key=azure_key,
            data=data,
            content_type=content_type,
        )
        old_url = blob.public_url or f"gs://{fb_bucket.name}/{blob.name}"
        mapping[old_url] = new_url
        # Also map the gs:// form so both URL shapes get rewritten.
        mapping[f"gs://{fb_bucket.name}/{blob.name}"] = new_url
        print(f"  {az_container}/{azure_key}  ←  {blob.name}")
    return mapping


def _rewrite_medical_document_urls(mapping: Dict[str, str]) -> int:
    """Update medical_documents rows whose blob_url matches an old Firebase URL."""
    SessionLocal = get_session_factory()
    rewritten = 0
    with SessionLocal() as session:
        docs = session.query(MedicalDocument).filter(
            MedicalDocument.blob_url.like("https://firebasestorage%")
            | MedicalDocument.blob_url.like("gs://%")
        ).all()
        for d in docs:
            if d.blob_url in mapping:
                d.blob_url = mapping[d.blob_url]
                rewritten += 1
        session.commit()
    return rewritten


def _rewrite_import_job_urls(mapping: Dict[str, str]) -> int:
    SessionLocal = get_session_factory()
    rewritten = 0
    with SessionLocal() as session:
        jobs = session.query(ImportJob).filter(
            ImportJob.blob_url.like("https://firebasestorage%")
            | ImportJob.blob_url.like("gs://%")
        ).all()
        for j in jobs:
            if j.blob_url in mapping:
                j.blob_url = mapping[j.blob_url]
                rewritten += 1
        session.commit()
    return rewritten


def run() -> int:
    fb_bucket = _get_firebase_bucket()

    print("Copying medical_reports/ → medical-documents ...")
    med_map = _copy_prefix(fb_bucket, "medical_reports/", MEDICAL_DOCUMENTS_CONTAINER)
    print(f"  copied {len(med_map) // 2} files")

    print("Copying import_results/ → employee-imports ...")
    imp_map = _copy_prefix(fb_bucket, "import_results/", EMPLOYEE_IMPORTS_CONTAINER)
    print(f"  copied {len(imp_map) // 2} files")

    combined = {**med_map, **imp_map}

    print("Rewriting medical_documents.blob_url ...")
    n1 = _rewrite_medical_document_urls(combined)
    print(f"  rewrote {n1} rows")

    print("Rewriting import_jobs.blob_url ...")
    n2 = _rewrite_import_job_urls(combined)
    print(f"  rewrote {n2} rows")

    print("STORAGE COPY COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(run())
