"""One-shot helper: migrate chat_sessions with small batches + progress.

Separate from run_all because chat_sessions docs can be huge (long message
arrays) and the default 100-row bulk insert can exceed Azure Postgres's
statement size ceiling and drop the connection. Here we commit every 20 rows
and print progress.
"""

from __future__ import annotations

import sys

from db.models import ChatSession
from db.session import get_session_factory
from migration.fs_export import iter_collection
from migration.import_pg import insert_rows
from migration.transform import transform_chat_session


BATCH = 20


def main() -> int:
    SessionLocal = get_session_factory()
    total_read = 0
    total_inserted = 0
    total_errors = 0
    buffer: list[dict] = []

    for doc_id, doc in iter_collection("chat_sessions"):
        total_read += 1
        try:
            buffer.append(transform_chat_session(doc_id, doc))
        except Exception as e:
            print(f"  [skip] chat_sessions/{doc_id}: {e}", file=sys.stderr)
            total_errors += 1
            continue

        if len(buffer) >= BATCH:
            with SessionLocal() as session:
                inserted, errs = insert_rows(session, ChatSession, buffer)
                session.commit()
                total_inserted += inserted
                total_errors += len(errs)
                for e in errs[:2]:
                    print(f"  [insert-error] {e['error'][:200]}", file=sys.stderr)
            print(f"  progress: read={total_read} inserted={total_inserted} errors={total_errors}")
            buffer = []

    # Flush remaining.
    if buffer:
        with SessionLocal() as session:
            inserted, errs = insert_rows(session, ChatSession, buffer)
            session.commit()
            total_inserted += inserted
            total_errors += len(errs)
        print(f"  final flush: read={total_read} inserted={total_inserted} errors={total_errors}")

    print(f"DONE: read={total_read} inserted={total_inserted} errors={total_errors}")
    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
