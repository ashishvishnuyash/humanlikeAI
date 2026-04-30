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
