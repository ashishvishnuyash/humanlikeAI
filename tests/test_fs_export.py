"""Unit tests for migration.fs_export — mocked Firestore client."""

from __future__ import annotations

from unittest.mock import MagicMock

from migration.fs_export import iter_collection


def test_iter_collection_yields_id_and_dict(monkeypatch):
    fake_doc_a = MagicMock()
    fake_doc_a.id = "user-123"
    fake_doc_a.to_dict.return_value = {"email": "a@example.com", "role": "employee"}
    fake_doc_b = MagicMock()
    fake_doc_b.id = "user-456"
    fake_doc_b.to_dict.return_value = {"email": "b@example.com", "role": "hr"}

    fake_client = MagicMock()
    fake_coll = MagicMock()
    fake_coll.stream.return_value = iter([fake_doc_a, fake_doc_b])
    fake_client.collection.return_value = fake_coll

    monkeypatch.setattr("migration.fs_export._get_fs_client", lambda: fake_client)

    result = list(iter_collection("users"))
    assert result == [
        ("user-123", {"email": "a@example.com", "role": "employee"}),
        ("user-456", {"email": "b@example.com", "role": "hr"}),
    ]
    fake_client.collection.assert_called_once_with("users")


def test_iter_collection_skips_docs_whose_to_dict_returns_none(monkeypatch):
    fake_doc = MagicMock()
    fake_doc.id = "ghost"
    fake_doc.to_dict.return_value = None  # Firestore returns None for fully-pruned docs

    fake_client = MagicMock()
    fake_coll = MagicMock()
    fake_coll.stream.return_value = iter([fake_doc])
    fake_client.collection.return_value = fake_coll

    monkeypatch.setattr("migration.fs_export._get_fs_client", lambda: fake_client)

    assert list(iter_collection("anything")) == []
