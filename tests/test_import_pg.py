"""Integration tests for migration.import_pg.

Opens a real transaction against Azure Postgres and rolls back at the end of
each test so nothing is committed. Requires DATABASE_URL set in .env.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from db.models import Company, User
from db.session import get_engine, get_session_factory
from migration.import_pg import insert_rows


@pytest.fixture
def db_session():
    SessionLocal = get_session_factory()
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def test_insert_rows_inserts_companies(db_session):
    rows = [
        {"id": uuid.uuid4(), "name": "ETL Test A", "settings": {}, "employee_count": 0},
        {"id": uuid.uuid4(), "name": "ETL Test B", "settings": {}, "employee_count": 0},
    ]
    inserted, errors = insert_rows(db_session, Company, rows)
    assert inserted == 2
    assert errors == []
    # Verify rows exist IN THIS SESSION (not committed) — sanity check.
    names = {r.name for r in db_session.query(Company).filter(Company.name.in_(["ETL Test A", "ETL Test B"])).all()}
    assert names == {"ETL Test A", "ETL Test B"}


def test_insert_rows_is_idempotent_on_conflict(db_session):
    cid = uuid.uuid4()
    row = {"id": cid, "name": "ETL Idempotent", "settings": {}, "employee_count": 0}
    inserted1, errors1 = insert_rows(db_session, Company, [row])
    assert inserted1 == 1
    # Insert the same row again — should be 0 inserted, no error.
    inserted2, errors2 = insert_rows(db_session, Company, [row])
    assert inserted2 == 0
    assert errors2 == []
