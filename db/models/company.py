"""Company model.

Note: ``owner_id`` FK to ``users.id`` is added post-declaration in
``db/models/__init__.py`` to avoid a circular import (User references Company
via ``company_id``, and Company references User via ``owner_id``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.models import Base


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    owner_id: Mapped[str | None] = mapped_column(String, nullable=True)
    settings: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    employee_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now()
    )
