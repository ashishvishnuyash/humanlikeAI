"""Focused test of POST /api/physical-health/ask (RAG Q&A endpoint).

Test cases:
  1. Auth gate                — request without bearer token        -> 401
  2. Question too short       — 4 chars                              -> 422
  3. Empty question           — empty string                          -> 422
  4. Health Q (bloodwork)     — "What was my hemoglobin?"             -> 200 + grounded answer
  5. Health Q (cholesterol)   — "What are my cholesterol numbers?"    -> 200 + grounded answer
  6. Health Q (recommendation)— "What are my health recommendations?" -> 200 + grounded answer
  7. Resume Q (typing)        — "What is my typing speed?"            -> 200 (resume content reaches RAG)
  8. Off-topic Q              — "What is the capital of France?"      -> 200 (low confidence / fallback)
  9. Fresh-user Q             — new account, no docs uploaded         -> 200 + "could not find" fallback

Each test prints: status, confidence, source_doc_ids, and a snippet of the answer.

Run:  python -m scripts.ask_test
"""
from __future__ import annotations

import json
import sys
from typing import Any, Optional

from fastapi.testclient import TestClient

from main import app
from scripts import api_test_seed


def _hdr(token: Optional[str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _pf(label: str, ok: bool, detail: str = "") -> dict:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")
    return {"label": label, "ok": ok, "detail": detail}


def _ask(client: TestClient, token: str, question: str) -> Any:
    return client.post(
        "/api/physical-health/ask",
        headers=_hdr(token),
        json={"question": question},
    )


def _existing_employee_token() -> Optional[dict]:
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
        return {"uid": u.id, "email": u.email, **r.json()}


def _print_qa(label: str, q: str, body: dict) -> None:
    answer = body.get("answer", "")
    sources = body.get("source_doc_ids", [])
    conf = body.get("confidence", 0.0)
    print(f"\n   Q: {q}")
    print(f"   A: {answer[:400]}{'...' if len(answer) > 400 else ''}")
    print(f"      confidence : {conf}")
    print(f"      sources    : {[s[:8] + '...' for s in sources] if sources else '(none)'}")


def main() -> int:
    print("=" * 70)
    print("ASK TEST — POST /api/physical-health/ask  (RAG Q&A)")
    print("=" * 70)

    # ── Reuse existing seeded employee with already-uploaded docs ─────────
    emp = _existing_employee_token()
    if emp is None:
        print("\nNo existing seed user — please run upload_test --keep first.")
        return 2
    print(f"\nReusing seed user: {emp['email']}")
    print(f"            uid : {emp['uid']}")
    token = emp["access_token"]
    client = TestClient(app)

    # Snapshot how many docs the user has, so we know if we have content
    from db.models.physical_health import MedicalDocument
    from db.session import get_session_factory
    SessionLocal = get_session_factory()
    with SessionLocal() as db:
        rows = db.query(MedicalDocument).filter(MedicalDocument.user_id == emp["uid"]).all()
        total_chunks = sum(len(r.rag_chunk_ids or []) for r in rows)
        print(f"           docs : {len(rows)}  ({total_chunks} chunks total)")

    results: list[dict] = []

    # ── 1. No auth → 401 ──────────────────────────────────────────────────
    r = client.post("/api/physical-health/ask", json={"question": "What is my hemoglobin level?"})
    results.append(_pf(
        "1. unauthenticated request",
        r.status_code in {401, 403},
        f"HTTP {r.status_code}",
    ))

    # ── 2. Too short (4 chars) → 422 ──────────────────────────────────────
    r = _ask(client, token, "hi?")
    results.append(_pf(
        "2. question shorter than 5 chars",
        r.status_code == 422,
        f"HTTP {r.status_code}",
    ))

    # ── 3. Empty string → 422 ─────────────────────────────────────────────
    r = _ask(client, token, "")
    results.append(_pf(
        "3. empty question",
        r.status_code == 422,
        f"HTTP {r.status_code}",
    ))

    # ── 4. Health Q: hemoglobin (should hit checkup_report.docx) ──────────
    q = "What was my hemoglobin level?"
    r = _ask(client, token, q)
    ok = r.status_code == 200
    body = r.json() if ok else {}
    grounded = ok and bool(body.get("source_doc_ids")) and "13.9" in body.get("answer", "") + " " + " "
    grounded = grounded or (ok and any(k in body.get("answer", "").lower() for k in ["hemoglobin", "13.", "14.", "g/dl"]))
    detail = f"HTTP {r.status_code}, conf={body.get('confidence')}, sources={len(body.get('source_doc_ids', []))}"
    results.append(_pf("4. health Q (hemoglobin)", ok and grounded, detail))
    if ok:
        _print_qa("hemoglobin", q, body)

    # ── 5. Health Q: cholesterol ──────────────────────────────────────────
    q = "What are my cholesterol numbers?"
    r = _ask(client, token, q)
    ok = r.status_code == 200
    body = r.json() if ok else {}
    grounded = ok and any(k in body.get("answer", "").lower() for k in ["cholesterol", "ldl", "hdl", "178", "lipid"])
    detail = f"HTTP {r.status_code}, conf={body.get('confidence')}, sources={len(body.get('source_doc_ids', []))}"
    results.append(_pf("5. health Q (cholesterol)", ok and grounded, detail))
    if ok:
        _print_qa("cholesterol", q, body)

    # ── 6. Health Q: recommendations ──────────────────────────────────────
    q = "What are my health recommendations?"
    r = _ask(client, token, q)
    ok = r.status_code == 200
    body = r.json() if ok else {}
    grounded = ok and bool(body.get("source_doc_ids"))
    detail = f"HTTP {r.status_code}, conf={body.get('confidence')}, sources={len(body.get('source_doc_ids', []))}"
    results.append(_pf("6. health Q (recommendations)", ok and grounded, detail))
    if ok:
        _print_qa("recommendations", q, body)

    # ── 7. Resume Q: typing speed (resume content was uploaded) ──────────
    q = "What is the typing speed mentioned in my documents?"
    r = _ask(client, token, q)
    ok = r.status_code == 200
    body = r.json() if ok else {}
    has_67 = ok and "67" in body.get("answer", "")
    detail = f"HTTP {r.status_code}, conf={body.get('confidence')}, sources={len(body.get('source_doc_ids', []))}"
    results.append(_pf("7. resume Q (typing speed) finds '67 WPM'", ok and has_67, detail))
    if ok:
        _print_qa("typing speed", q, body)

    # ── 8. Off-topic question → low conf or fallback ─────────────────────
    q = "What is the capital of France?"
    r = _ask(client, token, q)
    ok = r.status_code == 200
    body = r.json() if ok else {}
    detail = f"HTTP {r.status_code}, conf={body.get('confidence')}, sources={len(body.get('source_doc_ids', []))}"
    results.append(_pf("8. off-topic Q returns 200 (graceful)", ok, detail))
    if ok:
        _print_qa("off-topic", q, body)

    # ── 9. Fresh user with no docs → fallback message ───────────────────
    print("\n  Creating a fresh employee with no documents…")
    fresh_seed = api_test_seed.setup(suffix=None)
    fresh_token = fresh_seed["employee"]["access_token"]
    q = "What is my hemoglobin level?"
    r = _ask(client, fresh_token, q)
    ok = r.status_code == 200
    body = r.json() if ok else {}
    is_fallback = (
        ok
        and not body.get("source_doc_ids")
        and ("could not find" in body.get("answer", "").lower()
             or "no relevant" in body.get("answer", "").lower())
    )
    detail = f"HTTP {r.status_code}, sources={body.get('source_doc_ids')}"
    results.append(_pf("9. fresh user (no docs) returns fallback", ok and is_fallback, detail))
    if ok:
        _print_qa("fresh-user fallback", q, body)
    # Clean up the fresh seed
    api_test_seed.teardown()

    # ── Summary ──────────────────────────────────────────────────────────
    passed = sum(1 for r in results if r["ok"])
    total = len(results)
    print("\n" + "-" * 70)
    print(f"SUMMARY: {passed}/{total} passed, {total - passed} failed")
    print("-" * 70)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
