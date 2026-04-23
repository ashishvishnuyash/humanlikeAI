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
