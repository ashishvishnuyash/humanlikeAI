"""Alembic migration environment.

Reads ``DATABASE_URL`` from the environment (loaded by ``dotenv`` at import time
via ``db.session``) and uses ``db.models.Base.metadata`` as the target metadata.

We bypass ``alembic.ini``'s ``sqlalchemy.url`` entirely because ConfigParser
interprets ``%`` as interpolation syntax — which collides with URL-encoded
passwords (``%40`` for ``@``).
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

# Load .env side-effect: importing db.session calls dotenv.load_dotenv().
from db.session import get_engine  # noqa: F401 — triggers env load
from db import models  # noqa: F401 — side-effect import so Base.metadata is populated
from db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_DATABASE_URL = os.environ.get("DATABASE_URL")
if not _DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set in the environment / .env")


def run_migrations_offline() -> None:
    """Emit SQL without connecting to the DB."""
    context.configure(
        url=_DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect to the DB and apply migrations."""
    connectable = create_engine(_DATABASE_URL, poolclass=pool.NullPool, future=True)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
