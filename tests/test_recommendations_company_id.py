"""Verify recommendations endpoint logs a warning when company_id is not
a valid UUID (Firebase-style ID). It must still continue (silent fallback
preserved) so existing callers don't break."""

from __future__ import annotations

import logging


def test_non_uuid_company_id_logs_warning(caplog):
    from routers import recommendations as rec

    with caplog.at_level(logging.WARNING, logger=rec.__name__):
        result = rec._coerce_company_uuid("company_abcdef123")

    assert result is None
    assert any(
        "non-uuid company_id" in r.message.lower() or "company_id" in r.message.lower()
        for r in caplog.records
    ), f"expected warning log, got: {[r.message for r in caplog.records]}"


def test_valid_uuid_company_id_no_warning(caplog):
    import uuid as _uuid
    from routers import recommendations as rec

    valid = str(_uuid.uuid4())
    with caplog.at_level(logging.WARNING, logger=rec.__name__):
        result = rec._coerce_company_uuid(valid)

    assert result is not None
    assert str(result) == valid
    assert len(caplog.records) == 0
