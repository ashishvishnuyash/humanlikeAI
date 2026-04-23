"""ETL orchestrator CLI.

Flags:
    --dry-run           Read from Firestore, transform, but skip Postgres writes.
    --apply             Full run: write everything to Postgres.
    --collection NAME   Run only the specified collection (can repeat).
    --storage-only      Skip DB; run only the Firebase->Azure Blob copy.

One of ``--dry-run``, ``--apply``, ``--storage-only`` is required. When
``--apply`` is used, each collection runs in its own transaction.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from typing import Callable, Dict, List, Tuple

from db.models import (
    AIRecommendation,
    AnonymousProfile,
    Call,
    CallSession,
    ChatSession,
    CheckIn,
    Company,
    CommunityPost,
    CommunityReply,
    EscalationTicket,
    ImportJob,
    Intervention,
    MedicalDocument,
    MentalHealthReport,
    MHSession,
    PhysicalHealthCheckin,
    PhysicalHealthReport,
    User,
    UserGamification,
    WellnessChallenge,
    WellnessEvent,
)
from db.session import get_session_factory
from migration.fs_export import iter_collection
from migration.import_pg import insert_rows
from migration.transform import (
    MissingRequiredField,
    transform_ai_recommendation,
    transform_anonymous_profile,
    transform_call,
    transform_call_session,
    transform_chat_session,
    transform_check_in,
    transform_community_post,
    transform_community_reply,
    transform_company,
    transform_escalation_ticket,
    transform_import_job,
    transform_intervention,
    transform_medical_document,
    transform_mental_health_report,
    transform_mh_session,
    transform_physical_health_checkin,
    transform_physical_health_report,
    transform_user,
    transform_user_gamification,
    transform_wellness_challenge,
    transform_wellness_event,
)


# (firestore_collection, model_cls, transform_fn)
# Order is FK-safe.
PIPELINE: List[Tuple[str, type, Callable]] = [
    ("companies", Company, transform_company),
    ("users", User, transform_user),
    ("anonymous_profiles", AnonymousProfile, transform_anonymous_profile),
    ("chat_sessions", ChatSession, transform_chat_session),
    ("check_ins", CheckIn, transform_check_in),
    ("sessions", MHSession, transform_mh_session),
    ("mental_health_reports", MentalHealthReport, transform_mental_health_report),
    ("ai_recommendations", AIRecommendation, transform_ai_recommendation),
    ("interventions", Intervention, transform_intervention),
    ("escalation_tickets", EscalationTicket, transform_escalation_ticket),
    ("physical_health_checkins", PhysicalHealthCheckin, transform_physical_health_checkin),
    ("physical_health_reports", PhysicalHealthReport, transform_physical_health_report),
    ("medical_documents", MedicalDocument, transform_medical_document),
    ("wellness_events", WellnessEvent, transform_wellness_event),
    ("user_gamification", UserGamification, transform_user_gamification),
    ("wellness_challenges", WellnessChallenge, transform_wellness_challenge),
    ("community_posts", CommunityPost, transform_community_post),
    ("community_replies", CommunityReply, transform_community_reply),
    ("calls", Call, transform_call),
    ("callSessions", CallSession, transform_call_session),
    ("import_jobs", ImportJob, transform_import_job),
]


def _run_collection(
    fs_name: str,
    model_cls: type,
    transform_fn: Callable,
    dry_run: bool,
) -> Dict[str, int]:
    """Read, transform, insert one collection. Returns {read, transformed, inserted, errors}."""
    read = 0
    transformed_rows: List[dict] = []
    transform_errors = 0

    for doc_id, doc in iter_collection(fs_name):
        read += 1
        try:
            row = transform_fn(doc_id, doc)
            transformed_rows.append(row)
        except MissingRequiredField as e:
            transform_errors += 1
            print(f"  [skip] {fs_name}/{doc_id}: {e}", file=sys.stderr)
        except Exception as e:
            transform_errors += 1
            print(f"  [error] {fs_name}/{doc_id}: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    inserted = 0
    insert_errors = 0
    if not dry_run and transformed_rows:
        SessionLocal = get_session_factory()
        with SessionLocal() as session:
            inserted, errs = insert_rows(session, model_cls, transformed_rows)
            insert_errors = len(errs)
            if errs:
                for err in errs[:5]:
                    print(f"  [insert-error] {err['error']}", file=sys.stderr)
            session.commit()

    return {
        "read": read,
        "transformed": len(transformed_rows),
        "transform_errors": transform_errors,
        "inserted": inserted,
        "insert_errors": insert_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Transform but don't insert.")
    group.add_argument("--apply", action="store_true", help="Full ETL run with inserts.")
    group.add_argument(
        "--storage-only",
        action="store_true",
        help="Skip DB; only copy Firebase Storage -> Azure Blob.",
    )
    parser.add_argument(
        "--collection",
        action="append",
        default=[],
        help="Limit to this collection (may repeat). Default: all.",
    )
    args = parser.parse_args()

    if args.storage_only:
        from migration.copy_storage import run as run_copy_storage
        return run_copy_storage()

    dry_run = args.dry_run
    selected = set(args.collection) if args.collection else None

    print("=" * 70)
    print(f"{'DRY-RUN' if dry_run else 'APPLY'} — Firestore -> Postgres ETL")
    print("=" * 70)

    totals = {"read": 0, "transformed": 0, "transform_errors": 0, "inserted": 0, "insert_errors": 0}

    for fs_name, model_cls, transform_fn in PIPELINE:
        if selected is not None and fs_name not in selected:
            continue
        print(f"\n{fs_name}:")
        result = _run_collection(fs_name, model_cls, transform_fn, dry_run)
        print(
            f"  read={result['read']}  "
            f"transformed={result['transformed']} (errors={result['transform_errors']})  "
            f"inserted={result['inserted']} (errors={result['insert_errors']})"
        )
        for k in totals:
            totals[k] += result[k]

    print("\n" + "=" * 70)
    print("TOTALS:", totals)
    print("=" * 70)

    if totals["transform_errors"] > 0 or totals["insert_errors"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
