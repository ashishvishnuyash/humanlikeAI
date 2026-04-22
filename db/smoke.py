"""Smoke-test script: verify we can connect to Azure Postgres.

Run with::

    python -m db.smoke

Exits 0 on success, 1 on failure. Prints the Postgres server version on success.
"""

from __future__ import annotations

import sys

from sqlalchemy import text

from db.session import get_engine


def main() -> int:
    try:
        engine = get_engine()
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version()"))
            version = result.scalar_one()
        print("OK: connected to Azure Postgres")
        print(f"Server version: {version}")
        return 0
    except Exception as exc:  # noqa: BLE001 — we want the full error surfaced
        print("FAIL: could not connect to Postgres", file=sys.stderr)
        print(f"Error: {exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
