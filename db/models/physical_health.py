"""Physical health domain: check-ins, reports, medical documents, wellness events."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.models import Base


class PhysicalHealthCheckin(Base):
    __tablename__ = "physical_health_checkins"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
    )
    vitals: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    symptoms: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_phc_user_created", "user_id", "created_at"),
    )


class PhysicalHealthReport(Base):
    __tablename__ = "physical_health_reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
    )
    report: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    generated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_phr_user_generated", "user_id", "generated_at"),
    )


class MedicalDocument(Base):
    __tablename__ = "medical_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String, nullable=False)
    blob_url: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    rag_chunk_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default="{}"
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_medical_docs_user_uploaded", "user_id", "uploaded_at"),
    )


class WellnessEvent(Base):
    __tablename__ = "wellness_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str | None] = mapped_column(String, nullable=True)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_wellness_events_user_created", "user_id", "created_at"),
    )
