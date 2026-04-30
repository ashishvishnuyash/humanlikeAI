"""Live smoke runner for the Diltak API.

Hits a representative subset of endpoints across every role gate using the
in-process FastAPI ``TestClient``. Captures actual status codes + response
payloads so the static endpoint reference can be cross-checked against
real-world behavior.

Flow
----
1. Call ``api_test_seed.setup()`` to obtain three logged-in users (super_admin,
   employer, employee) with valid access tokens.
2. Walk a hard-coded list of test cases. Each case declares the endpoint, an
   optional auth role, and an expected status set. A response is recorded as
   ``PASS`` if the actual status is in the expected set, ``FAIL`` otherwise.
3. ALWAYS call ``api_test_seed.teardown()`` at exit, even on failure.
4. Emit results to stdout as JSON and to ``docs/smoke_results.json`` so the
   compiler can include them in the final report.

Run
---
    python -m scripts.api_smoke
"""

from __future__ import annotations

import json
import sys
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from fastapi.testclient import TestClient

from main import app
from scripts import api_test_seed


@dataclass
class TestCase:
    name: str
    method: str
    path: str
    role: Optional[str]  # None for unauth, "super_admin" / "employer" / "employee"
    expected_status: set[int]
    json_body: Optional[dict] = None
    query: Optional[dict] = None
    note: str = ""


@dataclass
class TestResult:
    name: str
    method: str
    path: str
    role: Optional[str]
    expected_status: list[int]
    actual_status: int
    outcome: str  # PASS | FAIL | ERROR
    response_excerpt: Any = None
    error: Optional[str] = None
    note: str = ""


def _truncate(value: Any, limit: int = 600) -> Any:
    """Trim large responses for the report — keep keys/shape, snip values."""
    if isinstance(value, dict):
        out = {}
        for k, v in list(value.items())[:25]:
            out[k] = _truncate(v, limit)
        return out
    if isinstance(value, list):
        return [_truncate(v, limit) for v in value[:3]] + (
            [f"<+{len(value) - 3} more>"] if len(value) > 3 else []
        )
    if isinstance(value, str):
        return value if len(value) <= limit else value[: limit - 3] + "..."
    return value


def _cases(employer: dict, employee: dict) -> list[TestCase]:
    employer_uid = employer["uid"]
    employee_uid = employee["uid"]
    company_id = employer["company_id"]

    return [
        # ── Public / health ──────────────────────────────────────────────────
        TestCase("health", "GET", "/health", None, {200}),
        TestCase("anonymous chat", "POST", "/chat", None, {200, 500},
                 json_body={"message": "hi"},
                 note="Returns 500 if OPENAI_API_KEY missing or LLM call fails."),

        # ── Auth flow ────────────────────────────────────────────────────────
        TestCase("login wrong password", "POST", "/api/auth/login", None, {401},
                 json_body={"email": employer["email"], "password": "wrong"}),
        TestCase("register duplicate email", "POST", "/api/auth/register", None, {409},
                 json_body={"email": employer["email"], "password": api_test_seed.PASSWORD,
                            "company_name": "x"}),
        TestCase("me as employer", "GET", "/api/auth/me", "employer", {200}),
        TestCase("profile as employer", "GET", "/api/auth/profile", "employer", {200}),
        TestCase("me without token", "GET", "/api/auth/me", None, {401, 403}),

        # ── Super admin ──────────────────────────────────────────────────────
        TestCase("admin /me", "GET", "/api/admin/me", "super_admin", {200}),
        TestCase("admin /stats", "GET", "/api/admin/stats", "super_admin", {200}),
        TestCase("admin list employers", "GET", "/api/admin/employers", "super_admin", {200}),
        TestCase("admin get employer", "GET", f"/api/admin/employers/{employer_uid}",
                 "super_admin", {200}),
        TestCase("admin list employees", "GET", "/api/admin/employees", "super_admin", {200}),
        TestCase("admin list companies", "GET", "/api/admin/companies", "super_admin", {200}),
        TestCase("admin role gate (employer hits admin)", "GET", "/api/admin/employers",
                 "employer", {403}),

        # ── Employer CRUD ────────────────────────────────────────────────────
        TestCase("employer profile", "GET", "/api/employer/profile", "employer", {200}),
        TestCase("employer company", "GET", "/api/employer/company", "employer", {200}),
        TestCase("employer company stats", "GET", "/api/employer/company/stats",
                 "employer", {200}),

        # ── Employees (CRUD) ─────────────────────────────────────────────────
        TestCase("list employees", "GET", "/api/employees", "employer", {200}),
        TestCase("get employee", "GET", f"/api/employees/{employee_uid}",
                 "employer", {200}),
        TestCase("employee role gate (employee hits employer route)", "GET",
                 "/api/employees", "employee", {403}),

        # ── Employer team dashboard (require ?company_id=...) ────────────────
        TestCase("wellness-index", "GET", "/api/employer/wellness-index",
                 "employer", {200, 500}, query={"company_id": company_id},
                 note="Requires ?company_id= even though JWT carries it."),
        TestCase("burnout-trend", "GET", "/api/employer/burnout-trend",
                 "employer", {200, 500}, query={"company_id": company_id}),
        TestCase("engagement-signals", "GET", "/api/employer/engagement-signals",
                 "employer", {200, 500}, query={"company_id": company_id}),
        TestCase("early-warnings", "GET", "/api/employer/early-warnings",
                 "employer", {200, 500}, query={"company_id": company_id}),

        # ── Employer org analytics ───────────────────────────────────────────
        TestCase("org wellness-trend", "GET", "/api/employer/org/wellness-trend",
                 "employer", {200, 500}, query={"company_id": company_id}),
        TestCase("org department-comparison", "GET",
                 "/api/employer/org/department-comparison", "employer", {200, 500},
                 query={"company_id": company_id}),
        TestCase("org retention-risk", "GET", "/api/employer/org/retention-risk",
                 "employer", {200, 422, 500}, query={"company_id": company_id},
                 note="Returns 422 with {error:'insufficient_cohort', suppressed:true} "
                      "when team size is below the k-anonymity threshold."),

        # ── Employer insights ────────────────────────────────────────────────
        TestCase("insights predictive-trends", "GET",
                 "/api/employer/insights/predictive-trends", "employer",
                 {200, 500}, query={"company_id": company_id}),
        TestCase("insights benchmarks", "GET",
                 "/api/employer/insights/benchmarks", "employer", {200, 500},
                 query={"company_id": company_id}),
        TestCase("insights cohorts", "GET",
                 "/api/employer/insights/cohorts", "employer", {200, 500},
                 query={"company_id": company_id}),

        # ── Admin metrics (separate from super_admin) ────────────────────────
        TestCase("admin metrics overview", "GET", "/api/admin/overview",
                 "super_admin", {200, 500}),
        TestCase("admin metrics usage", "GET", "/api/admin/usage",
                 "super_admin", {200, 500}),

        # ── Physical health (employee context) ───────────────────────────────
        TestCase("physical-health check-ins", "GET",
                 "/api/physical-health/check-ins", "employee", {200, 500}),
        TestCase("physical-health score", "GET",
                 "/api/physical-health/score", "employee", {200, 500}),

        # ── Reports / escalation (camelCase param name!) ─────────────────────
        TestCase("reports recent", "GET", "/api/reports/recent",
                 "employer", {200, 500}, query={"companyId": company_id},
                 note="Note: this endpoint uses camelCase 'companyId' query param "
                      "while most other endpoints use snake_case 'company_id'."),

        # ── Recommendations ──────────────────────────────────────────────────
        TestCase("recommendations generate (employee)", "POST",
                 "/api/recommendations/generate", "employee", {200, 500},
                 json_body={
                     "employee_id": employee_uid,
                     "company_id": company_id,
                     "current_mood": 5,
                     "current_stress": 6,
                     "current_energy": 4,
                     "time_available": 15,
                 },
                 note="Allows 500 because OpenAI call may transiently fail."),
    ]


def _headers_for(role: Optional[str], tokens: dict[str, str]) -> dict:
    if role is None:
        return {}
    token = tokens.get(role)
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def run() -> dict:
    print("[smoke] Setting up seed accounts...", file=sys.stderr)
    seed = api_test_seed.setup(suffix="smoke01")
    tokens = {
        "super_admin": seed["super_admin"]["access_token"],
        "employer": seed["employer"]["access_token"],
        "employee": seed["employee"]["access_token"],
    }

    client = TestClient(app)
    cases = _cases(seed["employer"], seed["employee"])
    results: list[TestResult] = []

    for case in cases:
        headers = _headers_for(case.role, tokens)
        try:
            req = client.request(
                case.method,
                case.path,
                headers=headers,
                json=case.json_body,
                params=case.query,
            )
            actual = req.status_code
            try:
                body = req.json()
            except Exception:
                body = req.text[:300]
            outcome = "PASS" if actual in case.expected_status else "FAIL"
            results.append(TestResult(
                name=case.name,
                method=case.method,
                path=case.path,
                role=case.role,
                expected_status=sorted(case.expected_status),
                actual_status=actual,
                outcome=outcome,
                response_excerpt=_truncate(body),
                note=case.note,
            ))
            print(f"  [{outcome}] {case.method:6} {case.path}  -> {actual}",
                  file=sys.stderr)
        except Exception as exc:
            results.append(TestResult(
                name=case.name,
                method=case.method,
                path=case.path,
                role=case.role,
                expected_status=sorted(case.expected_status),
                actual_status=0,
                outcome="ERROR",
                error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                note=case.note,
            ))
            print(f"  [ERROR] {case.method:6} {case.path}  -> {exc}",
                  file=sys.stderr)

    summary = {
        "total":  len(results),
        "passed": sum(1 for r in results if r.outcome == "PASS"),
        "failed": sum(1 for r in results if r.outcome == "FAIL"),
        "errored": sum(1 for r in results if r.outcome == "ERROR"),
    }
    return {
        "summary": summary,
        "seed": {
            "super_admin_email": seed["super_admin"]["email"],
            "employer_email":    seed["employer"]["email"],
            "employee_email":    seed["employee"]["email"],
            "company_id":        seed["employer"]["company_id"],
        },
        "results": [asdict(r) for r in results],
    }


def main() -> int:
    out: dict = {}
    rc = 0
    try:
        out = run()
    except Exception as exc:
        out = {"error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
        rc = 1
    finally:
        print("[smoke] Tearing down seed accounts...", file=sys.stderr)
        try:
            td = api_test_seed.teardown()
            out["teardown"] = td
        except Exception as exc:
            out["teardown_error"] = f"{type(exc).__name__}: {exc}"

    out_path = Path(__file__).resolve().parent.parent / "docs" / "smoke_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\n[smoke] Wrote {out_path}", file=sys.stderr)
    print(json.dumps(out.get("summary", out), indent=2))
    return rc


if __name__ == "__main__":
    sys.exit(main())
