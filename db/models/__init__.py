"""SQLAlchemy declarative models.

Import all model modules here once they exist so ``Base.metadata`` sees every
table when Alembic autogenerates migrations. Keep this file's imports lazy-safe
— never import from application code that would create a circular dependency.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


__all__ = ["Base"]
