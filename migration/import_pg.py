"""Bulk-insert transformed rows into Postgres with re-run safety.

``insert_rows(session, model_cls, rows)`` inserts using SQLAlchemy's
``postgresql.insert(...).on_conflict_do_nothing(index_elements=['id'])`` so
re-running the ETL won't fail on previously-inserted rows.

The caller is responsible for committing the session. This function does NOT
commit — that way the orchestrator can group multiple collections into a
single transaction or (default) commit per collection.
"""

from __future__ import annotations

from typing import List, Tuple, Type

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from migration.transform import jsonable


def _prepare_row(row: dict) -> dict:
    """Coerce JSONB-column values (dicts / lists) so they're JSON-serializable.

    Firestore's ``DatetimeWithNanoseconds`` subclass of ``datetime`` is valid
    for TIMESTAMPTZ columns (psycopg2 adapts it), but inside a JSONB payload
    the standard JSON encoder chokes on it. ``jsonable`` recursively coerces.
    """
    out = {}
    for k, v in row.items():
        if isinstance(v, (dict, list)):
            out[k] = jsonable(v)
        else:
            out[k] = v
    return out


# Azure Postgres can drop connections on very large single-statement inserts
# (the total SQL text gets too big, or exceeds server limits). Batch size is
# read from ETL_BATCH_SIZE env var (default 100) so large-JSONB collections
# like chat_sessions can use smaller batches.
import os as _os
_BATCH_SIZE = int(_os.environ.get("ETL_BATCH_SIZE", "100"))


def insert_rows(
    session: Session,
    model_cls: Type,
    rows: List[dict],
) -> Tuple[int, List[dict]]:
    """Insert ``rows`` into ``model_cls.__table__`` with ON CONFLICT DO NOTHING.

    Rows are batched at ``_BATCH_SIZE`` to avoid Azure Postgres dropping the
    connection on very large single-statement inserts.

    Returns ``(inserted_count, errors)`` where errors is a list of
    ``{"row": row_dict, "error": str(exc)}`` for rows that triggered a
    non-conflict SQL error.
    """
    if not rows:
        return (0, [])

    table = model_cls.__table__
    total_inserted = 0
    all_errors: List[dict] = []

    for i in range(0, len(rows), _BATCH_SIZE):
        batch = [_prepare_row(r) for r in rows[i : i + _BATCH_SIZE]]

        # Try the fast path first: one bulk insert with ON CONFLICT DO NOTHING.
        savepoint = session.begin_nested()
        try:
            stmt = pg_insert(table).values(batch).on_conflict_do_nothing(
                index_elements=["id"]
            )
            result = session.execute(stmt)
            savepoint.commit()
            total_inserted += (result.rowcount or 0)
            continue
        except SQLAlchemyError:
            savepoint.rollback()

        # Fallback: insert one-by-one to isolate bad rows within this batch.
        for row in batch:
            row_sp = session.begin_nested()
            try:
                stmt = pg_insert(table).values([row]).on_conflict_do_nothing(
                    index_elements=["id"]
                )
                result = session.execute(stmt)
                row_sp.commit()
                total_inserted += (result.rowcount or 0)
            except IntegrityError:
                # Unique-constraint conflicts on columns OTHER than id (e.g.
                # anonymous_profiles.user_id) mean the row is effectively a
                # re-run duplicate. Silently skip — keeps ETL idempotent.
                row_sp.rollback()
            except SQLAlchemyError as exc:
                row_sp.rollback()
                all_errors.append({"row": row, "error": str(exc)})

    return (total_inserted, all_errors)
