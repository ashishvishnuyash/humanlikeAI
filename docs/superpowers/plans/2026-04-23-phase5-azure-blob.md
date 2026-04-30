# Phase 5 — Azure Blob Storage Swap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Firebase Storage with Azure Blob Storage across the two remaining call sites (`routers/physical_health.py`, `routers/employee_import.py`). After this phase no router calls `firebase_admin.storage` — all file uploads, downloads, and deletes go through Azure Blob.

**Architecture:** One thin wrapper module `storage/blob.py` exposes `upload_bytes`, `delete_by_url`, and `generate_signed_get_url`. Container names are fixed constants. Connection string comes from `AZURE_STORAGE_CONNECTION_STRING` (already in `.env.example` from Phase 1). Callers store the returned HTTPS URL in `MedicalDocument.blob_url` / `ImportJob.blob_url` just as they do today — shape unchanged.

**Tech Stack:** `azure-storage-blob` 12.x (already installed in Phase 1), `azure.storage.blob.BlobServiceClient`, SAS tokens for signed GET URLs.

**Spec reference:** `docs/superpowers/specs/2026-04-22-postgres-migration-design.md` — Section 6 and Section 8, Phase 5.

---

## Prerequisite (User Action — do BEFORE Task 1)

You need an Azure Storage account and two containers. If you already have a storage account provisioned under your Azure subscription, skip to step C.

A. **Create a storage account** in Azure Portal:
   - Portal → "Storage accounts" → **Create**
   - Resource group: any existing or new (e.g. `humasql-rg`)
   - Storage account name: globally unique, lowercase, 3-24 chars (e.g. `humasqlstg`)
   - Region: same region as your Azure Postgres server (reduces latency + egress cost)
   - Performance: **Standard**, Redundancy: **LRS** (cheapest; fine for dev)
   - Click **Review + create**, then **Create**. Wait ~60s.

B. **Get the connection string**:
   - Open the storage account → **Security + networking** → **Access keys**
   - Copy the **Connection string** under `key1` (starts with `DefaultEndpointsProtocol=https;AccountName=...`)
   - Save it — you'll put it into `.env` in Task 0 below.

C. **Create two containers**:
   - Storage account → **Data storage** → **Containers** → **+ Container**
   - Name: `medical-documents`, Public access level: **Private** → Create
   - Name: `employee-imports`, Public access level: **Private** → Create

D. Report back with the connection string so Task 0 can add it to `.env`.

---

## File Structure

| Path | Responsibility |
|---|---|
| `.env` (gitignored) | Add `AZURE_STORAGE_CONNECTION_STRING=...` |
| `storage/blob.py` | `upload_bytes(container, key, data, content_type) -> str`, `delete_by_url(url)`, `generate_signed_get_url(url, expires_seconds)`, container name constants |
| `tests/test_blob.py` | Unit tests (mocked BlobServiceClient — no real Azure calls in CI) |
| `scripts/blob_smoke.py` | End-to-end smoke test against real Azure Blob: upload → signed URL fetch → delete |
| `routers/physical_health.py` | Replace 2 Firebase Storage blocks with `blob.upload_bytes` / `blob.delete_by_url` |
| `utils/import_jobs.py` | Replace Firebase Storage upload in `_upload_results_csv` with `blob.upload_bytes` + signed URL |

Note: The location of the results-CSV uploader is `utils/import_jobs.py` — verify during Task 3 that that's the actual location of the existing Firebase Storage call. If it's still in `routers/employee_import.py`, swap there instead.

---

## Task 0: Add Azure Storage Connection String to Local Env

**Files:**
- Modify: `.env` (gitignored — do NOT commit)

- [ ] **Step 1: Verify prerequisite is complete**

Confirm the user has:
1. Created an Azure Storage account
2. Created `medical-documents` and `employee-imports` containers
3. Provided the connection string

If not, stop and ask for these.

- [ ] **Step 2: Append the connection string to `.env`**

```bash
# Replace <PASTE_CONNECTION_STRING> with the actual value from the user.
if ! grep -q "^AZURE_STORAGE_CONNECTION_STRING=" d:/bai/humasql/.env; then
    echo 'AZURE_STORAGE_CONNECTION_STRING=<PASTE_CONNECTION_STRING>' >> d:/bai/humasql/.env
fi
```

- [ ] **Step 3: Verify it's readable**

```bash
cd d:/bai/humasql && source venv/Scripts/activate && python -c "import os; from dotenv import load_dotenv; load_dotenv(); print('OK' if os.environ.get('AZURE_STORAGE_CONNECTION_STRING', '').startswith('DefaultEndpointsProtocol=') else 'MISSING')"
```

Expected: `OK`.

- [ ] **Step 4: No commit** — `.env` is gitignored.

---

## Task 1: `storage/blob.py` Module (TDD)

**Files:**
- Create: `storage/blob.py`, `tests/test_blob.py`

- [ ] **Step 1: Write failing tests**

Create `d:/bai/humasql/tests/test_blob.py`:

```python
"""Unit tests for storage.blob.

The real Azure Blob calls are exercised via scripts/blob_smoke.py. These unit
tests verify pure logic (URL parsing, argument composition) using a fake
BlobServiceClient so they run without network access.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from storage.blob import (
    MEDICAL_DOCUMENTS_CONTAINER,
    EMPLOYEE_IMPORTS_CONTAINER,
    _parse_blob_url,
    delete_by_url,
    generate_signed_get_url,
    upload_bytes,
)


def test_container_constants():
    assert MEDICAL_DOCUMENTS_CONTAINER == "medical-documents"
    assert EMPLOYEE_IMPORTS_CONTAINER == "employee-imports"


def test_parse_blob_url_extracts_container_and_key():
    url = "https://acct.blob.core.windows.net/medical-documents/user-123/doc-456/file.pdf"
    container, key = _parse_blob_url(url)
    assert container == "medical-documents"
    assert key == "user-123/doc-456/file.pdf"


def test_parse_blob_url_with_sas_token_strips_query_string():
    url = "https://acct.blob.core.windows.net/employee-imports/c1/j1.csv?sv=2022-11-02&sig=abc"
    container, key = _parse_blob_url(url)
    assert container == "employee-imports"
    assert key == "c1/j1.csv"


def test_parse_blob_url_rejects_non_https():
    with pytest.raises(ValueError):
        _parse_blob_url("gs://wrong-scheme/bucket/key")


def test_upload_bytes_calls_blob_client(monkeypatch):
    mock_client = MagicMock()
    mock_bc = MagicMock()
    mock_client.get_blob_client.return_value = mock_bc
    mock_bc.url = "https://acct.blob.core.windows.net/medical-documents/u/d/x.pdf"

    monkeypatch.setattr("storage.blob._service_client", lambda: mock_client)

    url = upload_bytes(
        container="medical-documents",
        key="u/d/x.pdf",
        data=b"hello",
        content_type="application/pdf",
    )
    assert url == "https://acct.blob.core.windows.net/medical-documents/u/d/x.pdf"
    mock_client.get_blob_client.assert_called_once_with(
        container="medical-documents", blob="u/d/x.pdf"
    )
    mock_bc.upload_blob.assert_called_once()
    # overwrite=True is required so re-uploads don't throw 409.
    _, kwargs = mock_bc.upload_blob.call_args
    assert kwargs.get("overwrite") is True


def test_delete_by_url_parses_and_deletes(monkeypatch):
    mock_client = MagicMock()
    mock_bc = MagicMock()
    mock_client.get_blob_client.return_value = mock_bc
    monkeypatch.setattr("storage.blob._service_client", lambda: mock_client)

    delete_by_url("https://acct.blob.core.windows.net/medical-documents/u/d/x.pdf")

    mock_client.get_blob_client.assert_called_once_with(
        container="medical-documents", blob="u/d/x.pdf"
    )
    mock_bc.delete_blob.assert_called_once()


def test_delete_by_url_silently_ignores_missing_blob(monkeypatch):
    from azure.core.exceptions import ResourceNotFoundError
    mock_client = MagicMock()
    mock_bc = MagicMock()
    mock_bc.delete_blob.side_effect = ResourceNotFoundError()
    mock_client.get_blob_client.return_value = mock_bc
    monkeypatch.setattr("storage.blob._service_client", lambda: mock_client)

    # Should not raise — idempotent delete.
    delete_by_url("https://acct.blob.core.windows.net/medical-documents/missing.pdf")


def test_generate_signed_get_url_returns_url_with_sas(monkeypatch):
    # generate_signed_get_url should return a string starting with the input URL
    # and containing a `?sig=` or similar SAS query parameter. We stub the low-level
    # generate_blob_sas to return a known fake SAS token.
    monkeypatch.setattr(
        "storage.blob.generate_blob_sas",
        lambda **kwargs: "sv=2022-11-02&sig=FAKEFAKE",
    )
    url = generate_signed_get_url(
        "https://acct.blob.core.windows.net/medical-documents/u/d/x.pdf",
        expires_seconds=3600,
    )
    assert url.startswith("https://acct.blob.core.windows.net/medical-documents/u/d/x.pdf?")
    assert "sig=FAKEFAKE" in url
```

- [ ] **Step 2: Run tests — should fail**

```bash
cd d:/bai/humasql && source venv/Scripts/activate && pytest tests/test_blob.py -v 2>&1 | tail -10
```

Expected: `ModuleNotFoundError: No module named 'storage.blob'`.

- [ ] **Step 3: Implement `storage/blob.py`**

Create `d:/bai/humasql/storage/blob.py`:

```python
"""Azure Blob Storage wrapper.

Thin facade over the Azure SDK so router code never talks to
``azure.storage.blob`` directly. Three operations are exposed:

* ``upload_bytes(container, key, data, content_type) -> str``
  Uploads raw bytes and returns the HTTPS URL of the blob.
* ``delete_by_url(url)`` -- idempotent; swallows 404s.
* ``generate_signed_get_url(url, expires_seconds=3600) -> str``
  Returns the input URL with a short-lived SAS token appended.

Connection string is read from ``AZURE_STORAGE_CONNECTION_STRING`` (loaded via
``dotenv``). Container names are constants exported from this module so
callers don't hard-code them.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Tuple
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions

# ── Container names ───────────────────────────────────────────────────────────

MEDICAL_DOCUMENTS_CONTAINER = "medical-documents"
EMPLOYEE_IMPORTS_CONTAINER = "employee-imports"


# ── Client ────────────────────────────────────────────────────────────────────

_client: BlobServiceClient | None = None


def _service_client() -> BlobServiceClient:
    """Return the process-wide BlobServiceClient, creating it on first call."""
    global _client
    if _client is None:
        conn = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
        if not conn:
            raise RuntimeError(
                "AZURE_STORAGE_CONNECTION_STRING is not set. "
                "Add it to .env with the value from Azure Portal → Storage → Access keys."
            )
        _client = BlobServiceClient.from_connection_string(conn)
    return _client


# ── URL parsing ───────────────────────────────────────────────────────────────


def _parse_blob_url(url: str) -> Tuple[str, str]:
    """Split ``https://<acct>.blob.core.windows.net/<container>/<key>`` into (container, key).

    Strips any SAS-token query string. Raises ``ValueError`` for non-HTTPS URLs or
    URLs without a container/blob path.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Expected https:// blob URL, got {url!r}")
    path = parsed.path.lstrip("/")
    if "/" not in path:
        raise ValueError(f"Blob URL missing key path: {url!r}")
    container, key = path.split("/", 1)
    return container, key


# ── Operations ────────────────────────────────────────────────────────────────


def upload_bytes(container: str, key: str, data: bytes, content_type: str) -> str:
    """Upload ``data`` to ``container/key`` and return the blob's HTTPS URL.

    Overwrites any existing blob at that key. Returns the ``.url`` attribute on
    the BlobClient (canonical form without SAS token).
    """
    from azure.storage.blob import ContentSettings

    client = _service_client()
    blob_client = client.get_blob_client(container=container, blob=key)
    blob_client.upload_blob(
        data,
        overwrite=True,
        content_settings=ContentSettings(content_type=content_type),
    )
    return blob_client.url


def delete_by_url(url: str) -> None:
    """Delete the blob referenced by ``url``. Idempotent — silently returns on 404."""
    container, key = _parse_blob_url(url)
    client = _service_client()
    blob_client = client.get_blob_client(container=container, blob=key)
    try:
        blob_client.delete_blob()
    except ResourceNotFoundError:
        return


def generate_signed_get_url(url: str, expires_seconds: int = 3600) -> str:
    """Return ``url`` with a read-only SAS token valid for ``expires_seconds``."""
    container, key = _parse_blob_url(url)
    client = _service_client()
    # Account key comes off the underlying credential.
    account_key = client.credential.account_key
    expires = datetime.now(timezone.utc) + timedelta(seconds=expires_seconds)
    sas = generate_blob_sas(
        account_name=client.account_name,
        container_name=container,
        blob_name=key,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=expires,
    )
    return f"{url}?{sas}"
```

- [ ] **Step 4: Run tests — should pass**

```bash
cd d:/bai/humasql && source venv/Scripts/activate && pytest tests/test_blob.py -v 2>&1 | tail -15
```

Expected: all 8 tests pass.

- [ ] **Step 5: Commit**

```bash
cd d:/bai/humasql && git add storage/blob.py tests/test_blob.py && git commit -m "Add Azure Blob Storage wrapper with unit tests"
```

---

## Task 2: End-to-End Blob Smoke Script

**Files:**
- Create: `scripts/blob_smoke.py`

- [ ] **Step 1: Write `scripts/blob_smoke.py`**

Create `d:/bai/humasql/scripts/blob_smoke.py`:

```python
"""End-to-end smoke test against the real Azure Blob Storage account.

Uploads a small test file, fetches it through a signed URL, verifies bytes
match, then deletes the blob and verifies the signed URL now 404s.

Exits 0 on success, 1 on failure.
Run: python -m scripts.blob_smoke
"""

from __future__ import annotations

import secrets
import sys

import requests

from storage.blob import (
    MEDICAL_DOCUMENTS_CONTAINER,
    delete_by_url,
    generate_signed_get_url,
    upload_bytes,
)


def main() -> int:
    key = f"smoke/{secrets.token_hex(6)}.txt"
    data = b"hello from blob smoke test"

    print(f"Uploading to {MEDICAL_DOCUMENTS_CONTAINER}/{key} ...")
    url = upload_bytes(
        container=MEDICAL_DOCUMENTS_CONTAINER,
        key=key,
        data=data,
        content_type="text/plain",
    )
    print(f"  uploaded: {url}")

    print("Generating signed GET URL ...")
    signed = generate_signed_get_url(url, expires_seconds=300)
    print(f"  signed: {signed[:80]}...")

    print("Fetching signed URL ...")
    r = requests.get(signed, timeout=30)
    print(f"  HTTP {r.status_code}, {len(r.content)} bytes")
    if r.status_code != 200 or r.content != data:
        print("FAIL: signed GET did not return expected bytes", file=sys.stderr)
        return 1

    print("Deleting blob ...")
    delete_by_url(url)
    print("  deleted.")

    print("Re-fetching signed URL (should 404) ...")
    r = requests.get(signed, timeout=30)
    print(f"  HTTP {r.status_code}")
    if r.status_code != 404:
        print(f"FAIL: expected 404 after delete, got {r.status_code}", file=sys.stderr)
        return 1

    print("BLOB SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run the smoke test**

```bash
cd d:/bai/humasql && source venv/Scripts/activate && python -m scripts.blob_smoke 2>&1 | tail -10
```

Expected: final line `BLOB SMOKE TEST PASSED`. Exit code 0.

If this fails with "AZURE_STORAGE_CONNECTION_STRING is not set" — Task 0 wasn't completed.
If this fails with a 403/AuthenticationFailed — the connection string is wrong or the storage account's access keys were rotated.
If this fails with a "container not found" — Task 0's Prerequisite step C wasn't done.

- [ ] **Step 3: Commit**

```bash
cd d:/bai/humasql && git add scripts/blob_smoke.py && git commit -m "Add Azure Blob end-to-end smoke test script"
```

---

## Task 3: Swap Storage Calls in `routers/physical_health.py`

**Files:**
- Modify: `routers/physical_health.py` (2 blocks: lines ~434-447 and ~608-618)

The file already has `# TODO: Phase 5 - Azure Blob` comments marking the spots. Replace both blocks with `storage.blob` calls.

- [ ] **Step 1: Replace the upload block (around lines 434-447)**

Find the block that uploads medical documents to Firebase Storage:

```python
    # Upload to Firebase Storage  # TODO: Phase 5 - Azure Blob
    storage_path = f"medical_reports/{uid}/{doc_id_str}/{filename}"
    blob_url = ""
    try:
        from firebase_config import firebaseConfig
        import firebase_admin.storage as fb_storage  # TODO: Phase 5 - Azure Blob
        bucket = fb_storage.bucket(firebaseConfig["storageBucket"])  # TODO: Phase 5 - Azure Blob
        blob = bucket.blob(storage_path)  # TODO: Phase 5 - Azure Blob
        blob.upload_from_string(file_bytes, content_type=file.content_type or "application/octet-stream")  # TODO: Phase 5 - Azure Blob
        blob_url = blob.public_url or storage_path
    except Exception as e:
        # Non-fatal: store the doc and process even if Storage upload fails
        print(f"[physical_health] Firebase Storage upload error for {doc_id_str}: {e}")
        blob_url = storage_path  # fallback: store path as blob_url
```

Replace with:

```python
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

```

- [ ] **Step 2: Replace the delete block (around lines 608-618)**

Find the block that deletes medical documents from Firebase Storage:

```python
    # 1. Delete from Firebase Storage  # TODO: Phase 5 - Azure Blob
    blob_url = row.blob_url or ""
    if blob_url:
        try:
            from firebase_config import firebaseConfig
            import firebase_admin.storage as fb_storage  # TODO: Phase 5 - Azure Blob
            bucket = fb_storage.bucket(firebaseConfig["storageBucket"])  # TODO: Phase 5 - Azure Blob
            blob = bucket.blob(blob_url)  # TODO: Phase 5 - Azure Blob
            blob.delete()  # TODO: Phase 5 - Azure Blob
        except Exception as e:
            errors.append(f"storage: {e}")
```

Replace with:

```python
    # 1. Delete from Azure Blob Storage
    blob_url = row.blob_url or ""
    if blob_url.startswith("https://"):
        try:
            from storage.blob import delete_by_url
            delete_by_url(blob_url)
        except Exception as e:
            errors.append(f"storage: {e}")
```

Note the `startswith("https://")` guard — some rows may have the fallback `storage_key` path (no scheme) stored from a failed upload; skip those.

- [ ] **Step 3: Verify imports still clean**

```bash
cd d:/bai/humasql && source venv/Scripts/activate && python -c "from routers.physical_health import router; from main import app; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Verify no Firebase Storage residue**

```bash
cd d:/bai/humasql && grep -n "firebase_admin.storage\|fb_storage\|firebaseConfig\[" routers/physical_health.py; echo "exit=$?"
```

Expected: `exit=1` (no matches).

- [ ] **Step 5: Commit**

```bash
cd d:/bai/humasql && git add routers/physical_health.py && git commit -m "Swap Firebase Storage for Azure Blob in physical_health router"
```

---

## Task 4: Swap Storage Calls in `utils/import_jobs.py`

**Files:**
- Modify: `utils/import_jobs.py` (the `_upload_results_csv` function, around lines 665-692)

- [ ] **Step 1: Confirm the call site location**

```bash
cd d:/bai/humasql && grep -n "firebase_admin import storage\|storage.bucket\|FIREBASE_STORAGE_BUCKET" utils/import_jobs.py routers/employee_import.py
```

Expected: matches in `utils/import_jobs.py` lines ~670-690. (If matches are in `routers/employee_import.py` instead, perform the swap there — same body of code.)

- [ ] **Step 2: Replace the Firebase Storage upload block**

Find this block:

```python
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
```

Replace with:

```python
    # Upload to Azure Blob Storage
    try:
        from storage.blob import (
            EMPLOYEE_IMPORTS_CONTAINER,
            generate_signed_get_url,
            upload_bytes,
        )

        key = f"{company_id}/{job_id}.csv"
        blob_url = upload_bytes(
            container=EMPLOYEE_IMPORTS_CONTAINER,
            key=key,
            data=csv_bytes,
            content_type="text/csv",
        )
        # Signed URL valid for 7 days (604800 seconds).
        return generate_signed_get_url(blob_url, expires_seconds=7 * 24 * 3600)
    except Exception as e:
        print(f"[import_job] Azure Blob upload error: {e}")
        return None
```

- [ ] **Step 3: If the old code imported `datetime as dt` only for the Firebase block, remove that import if it's now unused**

```bash
cd d:/bai/humasql && grep -n "import datetime as dt\|dt\." utils/import_jobs.py | head -10
```

If `dt.` is not referenced anywhere else in the function or file, remove the `import datetime as dt` line.

- [ ] **Step 4: Verify imports clean**

```bash
cd d:/bai/humasql && source venv/Scripts/activate && python -c "from utils.import_jobs import create_job; print('ok')"
```

Expected: `ok`.

- [ ] **Step 5: Verify no Firebase Storage residue in utils/**

```bash
cd d:/bai/humasql && grep -n "firebase_admin import storage\|firebase_admin.storage\|FIREBASE_STORAGE_BUCKET" utils/; echo "exit=$?"
```

Expected: `exit=1` (no matches).

- [ ] **Step 6: Commit**

```bash
cd d:/bai/humasql && git add utils/import_jobs.py && git commit -m "Swap Firebase Storage for Azure Blob in utils/import_jobs results-CSV upload"
```

---

## Task 5: Full App Regression Check

**Files:**
- None (runtime checks only)

- [ ] **Step 1: Run full test suite**

```bash
cd d:/bai/humasql && source venv/Scripts/activate && pytest 2>&1 | tail -5
```

Expected: all tests pass (19 from Phases 3-4 + 8 new from Task 1 = 27 tests).

- [ ] **Step 2: Run the Blob smoke test**

```bash
cd d:/bai/humasql && source venv/Scripts/activate && python -m scripts.blob_smoke 2>&1 | tail -5
```

Expected: `BLOB SMOKE TEST PASSED`.

- [ ] **Step 3: Run the auth smoke test**

```bash
cd d:/bai/humasql && source venv/Scripts/activate && python -m scripts.auth_smoke 2>&1 | tail -3
```

Expected: `ALL AUTH SMOKE TESTS PASSED`.

- [ ] **Step 4: Start the app and verify boot**

```bash
cd d:/bai/humasql && source venv/Scripts/activate && uvicorn main:app --host 127.0.0.1 --port 8765 --log-level warning &
sleep 15
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://127.0.0.1:8765/health
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://127.0.0.1:8765/docs
```

Expected: two `HTTP 200` lines. Kill the background uvicorn afterward.

- [ ] **Step 5: Confirm NO `firebase_admin.storage` imports remain anywhere**

```bash
cd d:/bai/humasql && grep -rn "firebase_admin.storage\|firebase_admin import storage" routers/ utils/ physical_health_agent.py 2>/dev/null --include="*.py"; echo "exit=$?"
```

Expected: `exit=1` (no matches). This is the Phase 5 success criterion.

- [ ] **Step 6: No commit** — regression check only.

---

## Phase 5 Exit Criteria

All boxes must be checked:

- [ ] Azure Storage account and both containers exist; connection string is in `.env`.
- [ ] `storage/blob.py` exists with `upload_bytes`, `delete_by_url`, `generate_signed_get_url`.
- [ ] `pytest tests/test_blob.py` passes (8 tests).
- [ ] `python -m scripts.blob_smoke` passes end-to-end.
- [ ] `routers/physical_health.py` has zero `firebase_admin.storage` references.
- [ ] `utils/import_jobs.py` has zero `firebase_admin.storage` references.
- [ ] Full test suite green (27 tests).
- [ ] `python -m scripts.auth_smoke` still passes.
- [ ] `grep -rn "firebase_admin.storage" routers/ utils/` returns no matches.
- [ ] At least 4 commits from this phase on the branch.

When all boxes are checked, report back and I'll write the Phase 6 plan (ETL — Firestore export → Postgres import, Firebase Storage → Azure Blob copy).
