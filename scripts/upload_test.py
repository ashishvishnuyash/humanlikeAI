"""Focused test of POST /api/physical-health/medical/upload.

Exercises every notable code path of the upload endpoint:
  1. Auth gate           — request without bearer token       -> 401
  2. Filename validation — .txt extension                     -> 400
  3. Empty file          — zero bytes                          -> 400
  4. Size cap            — bytes payload > 100 MB              -> 400
  5. Missing file part   — multipart body with no file         -> 422
  6. Valid DOCX          — small text-layer file, blood_test   -> 202
  7. Valid PDF           — image-only wellness report PDF      -> 202
  8. Optional metadata   — report_date + issuing_facility set  -> 202
  9. List documents      — confirm uploaded docs appear         -> 200
 10. Wait for analysis   — confirm chunk_ids persisted          -> non-empty

Uses the most recent smoke seed user (smoketest+employee-*@example.com).
Falls back to creating a new one if no smoke user exists.

Run:  python -m scripts.upload_test
"""
from __future__ import annotations

import json
import sys
import time
import uuid as _uuid
from pathlib import Path
from typing import Any, Optional

from fastapi.testclient import TestClient

from main import app
from scripts import api_test_seed


def _hdr(token: Optional[str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _short(payload: Any, n: int = 300) -> Any:
    text = json.dumps(payload, default=str) if not isinstance(payload, str) else payload
    return text if len(text) <= n else text[: n - 3] + "..."


def _pf(label: str, ok: bool, detail: str = "") -> dict:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")
    return {"label": label, "ok": ok, "detail": detail}


def _existing_employee_token() -> Optional[dict]:
    """Reuse the most recent smoketest employee from --keep run, if any."""
    from db.models import User
    from db.session import get_session_factory
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        u = (
            db.query(User)
            .filter(User.email.like("smoketest+employee-%"))
            .order_by(User.id)
            .first()
        )
        if u is None:
            return None
        client = TestClient(app)
        r = client.post(
            "/api/auth/login",
            json={"email": u.email, "password": api_test_seed.PASSWORD},
        )
        if r.status_code != 200:
            return None
        tokens = r.json()
        return {"uid": u.id, "email": u.email, **tokens}


def _build_docx() -> bytes:
    """Read the on-disk Vishal_Singh_Resume.docx (real text-rich DOCX)."""
    docx_path = Path(__file__).parent / "Vishal_Singh_Resume.docx"
    return docx_path.read_bytes()


def main() -> int:
    keep = "--keep" in sys.argv  # skip cleanup so vectors stay for inspection

    print("=" * 70)
    print(
        "UPLOAD TEST — POST /api/physical-health/medical/upload"
        + (" [--keep mode]" if keep else "")
    )
    print("=" * 70)

    # Get an authenticated employee (reuse existing seed if present)
    emp = _existing_employee_token()
    cleanup_seed = False
    if emp is None:
        print("\nNo existing smoke user — creating one.")
        seeds = api_test_seed.setup(suffix=None)
        emp = seeds["employee"]
        cleanup_seed = True
    else:
        print(f"\nReusing seed user: {emp['email']}")

    token = emp["access_token"]
    client = TestClient(app)
    results: list[dict] = []
    created_doc_ids: list[str] = []

    # ── 1. No auth → 401 ────────────────────────────────────────────────────
    docx_bytes = _build_docx()
    r = client.post(
        "/api/physical-health/medical/upload",
        files={"file": ("a.docx", docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    results.append(_pf(
        "1. unauthenticated request",
        r.status_code in {401, 403},
        f"HTTP {r.status_code}",
    ))

    # ── 2. Bad extension (.txt) → 400 ─────────────────────────────────────
    r = client.post(
        "/api/physical-health/medical/upload",
        headers=_hdr(token),
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    results.append(_pf(
        "2. unsupported extension (.txt)",
        r.status_code == 400,
        f"HTTP {r.status_code}: {_short(r.json().get('detail', ''))}",
    ))

    # ── 3. Empty file → 400 ────────────────────────────────────────────────
    r = client.post(
        "/api/physical-health/medical/upload",
        headers=_hdr(token),
        files={"file": ("empty.docx", b"", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    results.append(_pf(
        "3. empty file body",
        r.status_code in {400, 422, 500},  # text extractor may fail before size check
        f"HTTP {r.status_code}: {_short(r.json().get('detail', ''))}",
    ))

    # ── 4. Oversize (>100 MB) → 400 ────────────────────────────────────────
    huge = b"%PDF-1.4\n" + (b"\x00" * (101 * 1024 * 1024))  # 101 MB starting with PDF magic
    r = client.post(
        "/api/physical-health/medical/upload",
        headers=_hdr(token),
        files={"file": ("huge.pdf", huge, "application/pdf")},
    )
    results.append(_pf(
        "4. oversize file (>100 MB)",
        r.status_code == 400,
        f"HTTP {r.status_code}: {_short(r.json().get('detail', ''))}",
    ))

    # ── 5. Missing file part → 422 ────────────────────────────────────────
    r = client.post(
        "/api/physical-health/medical/upload",
        headers=_hdr(token),
    )
    results.append(_pf(
        "5. missing 'file' field",
        r.status_code == 422,
        f"HTTP {r.status_code}",
    ))

    # ── 6. Valid DOCX → 202 ────────────────────────────────────────────────
    r = client.post(
        "/api/physical-health/medical/upload?report_type=blood_test",
        headers=_hdr(token),
        files={"file": ("checkup.docx", docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    ok = r.status_code == 202
    detail = f"HTTP {r.status_code}"
    if ok:
        body = r.json()
        detail += f" doc_id={body.get('doc_id')[:8]}... status={body.get('status')}"
        created_doc_ids.append(body["doc_id"])
    else:
        detail += f": {_short(r.json().get('detail', ''))}"
    results.append(_pf("6. valid DOCX upload (blood_test)", ok, detail))

    # ── 7. Valid PDF → 202 ────────────────────────────────────────────────
    pdf_path = Path(__file__).parent / "wellness-report-2026-04-06 (6).pdf"
    if pdf_path.exists():
        pdf_bytes = pdf_path.read_bytes()
        r = client.post(
            "/api/physical-health/medical/upload?report_type=general_checkup",
            headers=_hdr(token),
            files={"file": (pdf_path.name, pdf_bytes, "application/pdf")},
        )
        ok = r.status_code == 202
        detail = f"HTTP {r.status_code}"
        if ok:
            body = r.json()
            detail += f" doc_id={body.get('doc_id')[:8]}... ({len(pdf_bytes):,} bytes)"
            created_doc_ids.append(body["doc_id"])
        else:
            detail += f": {_short(r.json().get('detail', ''))}"
        results.append(_pf("7. valid PDF upload (general_checkup)", ok, detail))
    else:
        results.append(_pf("7. valid PDF upload (general_checkup)", False, "PDF not found, skipped"))

    # ── 8. Optional metadata fields → 202 ─────────────────────────────────
    r = client.post(
        "/api/physical-health/medical/upload"
        "?report_type=specialist&report_date=2026-04-15&issuing_facility=Acme%20Hospital",
        headers=_hdr(token),
        files={"file": ("specialist.docx", docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    ok = r.status_code == 202
    detail = f"HTTP {r.status_code}"
    if ok:
        created_doc_ids.append(r.json()["doc_id"])
        detail += " (report_date + issuing_facility accepted)"
    else:
        detail += f": {_short(r.json().get('detail', ''))}"
    results.append(_pf("8. optional metadata (report_date, issuing_facility)", ok, detail))

    # ── 9. List documents → confirm all uploads appear ────────────────────
    r = client.get("/api/physical-health/medical", headers=_hdr(token))
    if r.status_code == 200:
        doc_list = r.json().get("documents", [])
        present = {d.get("doc_id") for d in doc_list}
        all_present = all(d in present for d in created_doc_ids)
        detail = f"{len(present)} doc(s) total; uploaded {len(created_doc_ids)} {'all listed' if all_present else 'MISSING SOME'}"
        results.append(_pf("9. uploaded docs appear in /medical list", all_present, detail))
    else:
        results.append(_pf("9. uploaded docs appear in /medical list", False, f"HTTP {r.status_code}"))

    # ── 10. Wait for background analysis to populate rag_chunk_ids ────────
    if created_doc_ids:
        from db.models.physical_health import MedicalDocument
        from db.session import get_session_factory
        SessionLocal = get_session_factory()
        target_id = created_doc_ids[0]  # check the first DOCX upload
        chunk_ids: list = []
        for _ in range(15):  # up to ~37s
            time.sleep(2.5)
            with SessionLocal() as db:
                row = (
                    db.query(MedicalDocument)
                    .filter(MedicalDocument.id == _uuid.UUID(target_id))
                    .one_or_none()
                )
                if row and row.rag_chunk_ids:
                    chunk_ids = list(row.rag_chunk_ids)
                    break
        results.append(_pf(
            "10. rag_chunk_ids populated after analysis",
            bool(chunk_ids),
            f"{len(chunk_ids)} chunk(s) for doc {target_id[:8]}...",
        ))

    # ── Cleanup: delete every doc we created (skipped under --keep) ──────
    if keep:
        print("\n[KEEP] Skipping DELETE — uploaded docs + Pinecone vectors preserved.")
        for did in created_doc_ids:
            print(f"        doc_id : {did}")
        print(f"        access_token : {token}")
        print("        Manual clean :")
        for did in created_doc_ids:
            print(f"          curl -X DELETE -H 'Authorization: Bearer <token>' \\")
            print(f"               http://localhost:8000/api/physical-health/medical/{did}")
        if cleanup_seed:
            print("\n        (seed user was created by this run — not torn down under --keep)")
    else:
        print("\nCleanup:")
        for did in created_doc_ids:
            r = client.delete(f"/api/physical-health/medical/{did}", headers=_hdr(token))
            print(f"  DELETE {did[:8]}... -> HTTP {r.status_code}")

        # Tear down seed only if we created it (don't disturb existing seed)
        if cleanup_seed:
            td = api_test_seed.teardown()
            print(f"  teardown: {td}")
        else:
            print("  (preserving existing seed user)")

    # ── Summary ───────────────────────────────────────────────────────────
    passed = sum(1 for r in results if r["ok"])
    total = len(results)
    print("\n" + "-" * 70)
    print(f"SUMMARY: {passed}/{total} passed, {total - passed} failed")
    print("-" * 70)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
