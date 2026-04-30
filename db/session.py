"""SQLAlchemy engine, session factory, and FastAPI dependency.

Reads ``DATABASE_URL`` from the environment (loaded via ``python-dotenv``).
Azure Postgres requires SSL — the URL must include ``?sslmode=require``.

Usage in FastAPI::

    from fastapi import Depends
    from sqlalchemy.orm import Session
    from db.session import get_session

    @router.get("/items")
    def list_items(db: Session = Depends(get_session)):
        return db.query(Item).all()
"""

from __future__ import annotations

import os
from typing import Iterator

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()

_DATABASE_URL = os.environ.get("DATABASE_URL")

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Return the process-wide SQLAlchemy engine, creating it on first call."""
    global _engine
    if _engine is None:
        if not _DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL is not set. Copy .env.example to .env and fill it in."
            )
        _engine = create_engine(
            _DATABASE_URL,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=10,
            future=True,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the process-wide session factory, creating it on first call."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            future=True,
        )
    return _SessionLocal


def get_session() -> Iterator[Session]:
    """FastAPI dependency that yields a session and guarantees close."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
