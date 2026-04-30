"""Pytest fixtures / module setup for the humasql test suite.

Ensures JWT_SECRET is set for tests that exercise token creation. Uses a
deterministic test-only value so tests are reproducible.
"""

from __future__ import annotations

import os

# Set a deterministic test secret BEFORE any auth module is imported.
# Real deployments read JWT_SECRET from .env; tests override here.
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-use-only")
os.environ.setdefault("JWT_ACCESS_MINUTES", "15")
os.environ.setdefault("JWT_REFRESH_DAYS", "30")
