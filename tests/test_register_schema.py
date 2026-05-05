"""Verify RegisterRequest accepts the frontend's exact field set
(camelCase aliases) and stores parts in the right destinations."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from routers.auth import RegisterRequest


def test_register_accepts_camelcase_aliases():
    req = RegisterRequest(
        email="founder@example.com",
        password="password123",
        firstName="Ada",
        lastName="Lovelace",
        companyName="Analytical Engines",
        companySize="11-50",
        industry="Software",
    )
    assert req.email == "founder@example.com"
    assert req.first_name == "Ada"
    assert req.last_name == "Lovelace"
    assert req.company_name == "Analytical Engines"
    assert req.company_size == "11-50"
    assert req.industry == "Software"


def test_register_accepts_snake_case_too():
    req = RegisterRequest(
        email="a@b.co",
        password="password123",
        first_name="Ada",
        last_name="Lovelace",
        company_name="X",
    )
    assert req.first_name == "Ada"
    assert req.company_size is None
    assert req.industry is None


def test_register_requires_first_name():
    with pytest.raises(ValidationError):
        RegisterRequest(
            email="a@b.co", password="password123",
            lastName="B", companyName="X",
        )


def test_register_requires_last_name():
    with pytest.raises(ValidationError):
        RegisterRequest(
            email="a@b.co", password="password123",
            firstName="A", companyName="X",
        )


def test_register_requires_company_name():
    with pytest.raises(ValidationError):
        RegisterRequest(
            email="a@b.co", password="password123",
            firstName="A", lastName="B",
        )


@pytest.mark.parametrize("field", ["firstName", "lastName", "companyName"])
def test_register_rejects_empty_string_for_required_field(field):
    data = {
        "email": "a@b.co", "password": "password123",
        "firstName": "A", "lastName": "B", "companyName": "X",
    }
    data[field] = ""
    with pytest.raises(ValidationError):
        RegisterRequest(**data)


def test_register_password_min_length_8():
    with pytest.raises(ValidationError):
        RegisterRequest(
            email="a@b.co",
            password="short",  # 5 chars
            firstName="A",
            lastName="B",
            companyName="X",
        )


def test_register_request_full_name_derives_from_parts():
    """Schema-level sanity check: first_name and last_name are stored
    separately so the register endpoint can compose them into full_name."""
    req = RegisterRequest(
        email="a@b.co",
        password="password123",
        firstName="Grace",
        lastName="Hopper",
        companyName="USN",
    )
    assert f"{req.first_name} {req.last_name}" == "Grace Hopper"
