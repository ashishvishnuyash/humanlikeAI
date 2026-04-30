"""SQLAlchemy declarative base + model registry.

Importing this package imports every model module so that ``Base.metadata``
contains every table. Alembic autogenerate reads ``Base.metadata``.

Import order matters for FK resolution:
  1. Base
  2. Company  (referenced by User.company_id)
  3. User     (referenced by everything else)
  4. Everything else

The ``companies.owner_id`` FK to ``users.id`` is declared via
``ForeignKeyConstraint`` below to avoid a circular import at module load time.
"""

from __future__ import annotations

from sqlalchemy import ForeignKeyConstraint
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


# Model imports — order matters.
from db.models.company import Company  # noqa: E402
from db.models.user import RefreshToken, User  # noqa: E402
from db.models.mental_health import (  # noqa: E402
    AIRecommendation,
    ChatSession,
    CheckIn,
    EscalationTicket,
    Intervention,
    MentalHealthReport,
    Session as MHSession,
)
from db.models.physical_health import (  # noqa: E402
    MedicalDocument,
    PhysicalHealthCheckin,
    PhysicalHealthReport,
    WellnessEvent,
)
from db.models.community import (  # noqa: E402
    AnonymousProfile,
    CommunityPost,
    CommunityReply,
    UserGamification,
    WellnessChallenge,
)
from db.models.calls import Call, CallSession  # noqa: E402
from db.models.imports import ImportJob  # noqa: E402
from db.models.audit_usage import (  # noqa: E402
    AuditLog,
    CompanyCredit,
    GamificationEvent,
    UsageLog,
)


# Post-declare companies.owner_id -> users.id to avoid circular imports.
Company.__table__.append_constraint(
    ForeignKeyConstraint(
        ["owner_id"],
        ["users.id"],
        ondelete="SET NULL",
        name="fk_companies_owner_id_users",
        # use_alter=True emits this FK as a separate ALTER TABLE after both
        # tables exist — necessary because Company is created before User in
        # FK-resolution order, but references User.
        use_alter=True,
    )
)


__all__ = [
    "Base",
    "Company",
    "User",
    "RefreshToken",
    "CheckIn",
    "MHSession",
    "MentalHealthReport",
    "ChatSession",
    "AIRecommendation",
    "Intervention",
    "EscalationTicket",
    "PhysicalHealthCheckin",
    "PhysicalHealthReport",
    "MedicalDocument",
    "WellnessEvent",
    "AnonymousProfile",
    "CommunityPost",
    "CommunityReply",
    "UserGamification",
    "WellnessChallenge",
    "Call",
    "CallSession",
    "ImportJob",
    "UsageLog",
    "AuditLog",
    "CompanyCredit",
    "GamificationEvent",
]
