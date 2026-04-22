"""bcrypt password hashing / verification.

Uses ``bcrypt`` directly to avoid noisy ``passlib`` version-detection warnings
against ``bcrypt`` 4.x. Cost factor is 12 rounds — a balance between login
latency (~300ms) and brute-force resistance.
"""

from __future__ import annotations

import bcrypt

_ROUNDS = 12


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of ``plain`` (string form, UTF-8 encoded internally)."""
    hashed = bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=_ROUNDS))
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if ``plain`` matches ``hashed``. False on any parse / compare error."""
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False
