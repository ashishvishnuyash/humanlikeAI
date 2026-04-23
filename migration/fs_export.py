"""Firestore collection iterator for migration.

Wraps ``firebase_admin.firestore.client()`` (already initialised by
``firebase_config.get_db()``) and yields ``(doc_id, doc_dict)`` tuples.
"""

from __future__ import annotations

from typing import Iterator, Tuple


def _get_fs_client():
    """Return the initialised Firestore client.

    Indirection exists so tests can monkeypatch this without importing
    firebase_admin. At runtime we go through ``firebase_config.get_db``.
    """
    from firebase_config import get_db
    client = get_db()
    if client is None:
        raise RuntimeError(
            "Firestore client not initialised. Check FIREBASE_CREDENTIALS_PATH in .env."
        )
    return client


def iter_collection(name: str) -> Iterator[Tuple[str, dict]]:
    """Yield ``(doc_id, doc_dict)`` for every document in the named Firestore collection.

    Documents whose ``to_dict()`` returns ``None`` are skipped (Firestore returns
    None for fully-pruned documents).
    """
    client = _get_fs_client()
    for doc in client.collection(name).stream():
        data = doc.to_dict()
        if data is None:
            continue
        yield doc.id, data


def count_collection(name: str) -> int:
    """Return the total number of documents in a collection. Used for verification."""
    client = _get_fs_client()
    n = 0
    for _ in client.collection(name).stream():
        n += 1
    return n
