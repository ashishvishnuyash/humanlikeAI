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
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session


def insert_rows(
    session: Session,
    model_cls: Type,
    rows: List[dict],
) -> Tuple[int, List[dict]]:
    """Insert ``rows`` into ``model_cls.__table__`` with ON CONFLICT DO NOTHING.

    Returns ``(inserted_count, errors)`` where errors is a list of
    ``{"row": row_dict, "error": str(exc)}`` for rows that triggered a
    non-conflict SQL error.
    """
    if not rows:
        return (0, [])

    table = model_cls.__table__
    errors: List[dict] = []
    # Try the fast path first: one bulk insert with ON CONFLICT DO NOTHING.
    try:
        stmt = pg_insert(table).values(rows).on_conflict_do_nothing(
            index_elements=["id"]
        )
        result = session.execute(stmt)
        # rowcount is the actual count of rows inserted (excludes conflicts).
        return (result.rowcount or 0, [])
    except SQLAlchemyError:
        session.rollback()

    # Fallback: insert one-by-one to isolate bad rows.
    inserted = 0
    for row in rows:
        savepoint = session.begin_nested()
        try:
            stmt = pg_insert(table).values([row]).on_conflict_do_nothing(
                index_elements=["id"]
            )
            result = session.execute(stmt)
            savepoint.commit()
            inserted += (result.rowcount or 0)
        except SQLAlchemyError as exc:
            savepoint.rollback()
            errors.append({"row": row, "error": str(exc)})
    return (inserted, errors)
