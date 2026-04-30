"""Unit tests for storage.blob.

The real Azure Blob calls are exercised via scripts/blob_smoke.py. These unit
tests verify pure logic (URL parsing, argument composition) using a fake
BlobServiceClient so they run without network access.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from storage.blob import (
    MEDICAL_DOCUMENTS_CONTAINER,
    EMPLOYEE_IMPORTS_CONTAINER,
    _parse_blob_url,
    delete_by_url,
    generate_signed_get_url,
    upload_bytes,
)


def test_container_constants():
    assert MEDICAL_DOCUMENTS_CONTAINER == "medical-documents"
    assert EMPLOYEE_IMPORTS_CONTAINER == "employee-imports"


def test_parse_blob_url_extracts_container_and_key():
    url = "https://acct.blob.core.windows.net/medical-documents/user-123/doc-456/file.pdf"
    container, key = _parse_blob_url(url)
    assert container == "medical-documents"
    assert key == "user-123/doc-456/file.pdf"


def test_parse_blob_url_with_sas_token_strips_query_string():
    url = "https://acct.blob.core.windows.net/employee-imports/c1/j1.csv?sv=2022-11-02&sig=abc"
    container, key = _parse_blob_url(url)
    assert container == "employee-imports"
    assert key == "c1/j1.csv"


def test_parse_blob_url_rejects_non_https():
    with pytest.raises(ValueError):
        _parse_blob_url("gs://wrong-scheme/bucket/key")


def test_upload_bytes_calls_blob_client(monkeypatch):
    mock_client = MagicMock()
    mock_bc = MagicMock()
    mock_client.get_blob_client.return_value = mock_bc
    mock_bc.url = "https://acct.blob.core.windows.net/medical-documents/u/d/x.pdf"

    monkeypatch.setattr("storage.blob._service_client", lambda: mock_client)

    url = upload_bytes(
        container="medical-documents",
        key="u/d/x.pdf",
        data=b"hello",
        content_type="application/pdf",
    )
    assert url == "https://acct.blob.core.windows.net/medical-documents/u/d/x.pdf"
    mock_client.get_blob_client.assert_called_once_with(
        container="medical-documents", blob="u/d/x.pdf"
    )
    mock_bc.upload_blob.assert_called_once()
    _, kwargs = mock_bc.upload_blob.call_args
    assert kwargs.get("overwrite") is True


def test_delete_by_url_parses_and_deletes(monkeypatch):
    mock_client = MagicMock()
    mock_bc = MagicMock()
    mock_client.get_blob_client.return_value = mock_bc
    monkeypatch.setattr("storage.blob._service_client", lambda: mock_client)

    delete_by_url("https://acct.blob.core.windows.net/medical-documents/u/d/x.pdf")

    mock_client.get_blob_client.assert_called_once_with(
        container="medical-documents", blob="u/d/x.pdf"
    )
    mock_bc.delete_blob.assert_called_once()


def test_delete_by_url_silently_ignores_missing_blob(monkeypatch):
    from azure.core.exceptions import ResourceNotFoundError
    mock_client = MagicMock()
    mock_bc = MagicMock()
    mock_bc.delete_blob.side_effect = ResourceNotFoundError()
    mock_client.get_blob_client.return_value = mock_bc
    monkeypatch.setattr("storage.blob._service_client", lambda: mock_client)

    # Should not raise — idempotent delete.
    delete_by_url("https://acct.blob.core.windows.net/medical-documents/missing.pdf")


def test_generate_signed_get_url_returns_url_with_sas(monkeypatch):
    monkeypatch.setattr(
        "storage.blob.generate_blob_sas",
        lambda **kwargs: "sv=2022-11-02&sig=FAKEFAKE",
    )
    url = generate_signed_get_url(
        "https://acct.blob.core.windows.net/medical-documents/u/d/x.pdf",
        expires_seconds=3600,
    )
    assert url.startswith("https://acct.blob.core.windows.net/medical-documents/u/d/x.pdf?")
    assert "sig=FAKEFAKE" in url
