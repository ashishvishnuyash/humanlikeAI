"""Models for activity / cost / audit / gamification tracking.

These mirror the Firestore collections introduced by the admin-panel work:
- ``usage_logs``         — one row per LLM call (tokens, cost, latency)
- ``audit_logs``         — one row per admin/employer mutation
- ``company_credits``    — per-company monthly spend + alert state
- ``gamification_events``— immutable point-award log

``user_gamification`` already exists in ``community.py`` — the new code uses
its existing ``extras`` JSONB column for the extended fields (longest_streak,
last_check_in, weekly_goal, etc.).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, Float, ForeignKey, Index, Integer, Numeric, String,
    Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.models import Base


class UsageLog(Base):
    """One record per LLM call."""

    __tablename__ = "usage_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
    )
    feature: Mapped[str] = mapped_column(String, nullable=False)  # chat / report / recommendation / etc.
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    provider: Mapped[str | None] = mapped_column(String, nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    estimated_cost_usd: Mapped[float] = mapped_column(
        Numeric(14, 8), nullable=False, server_default="0"
    )
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_usage_logs_company_created", "company_id", "created_at"),
        Index("ix_usage_logs_user_created", "user_id", "created_at"),
        Index("ix_usage_logs_feature_created", "feature", "created_at"),
    )


class AuditLog(Base):
    """One record per admin/employer mutation."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    actor_uid: Mapped[str | None] = mapped_column(String, nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    target_uid: Mapped[str | None] = mapped_column(String, nullable=True)
    target_type: Mapped[str] = mapped_column(String, nullable=False, server_default="'user'")
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
    )
    audit_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_audit_logs_company_created", "company_id", "created_at"),
        Index("ix_audit_logs_actor_created", "actor_uid", "created_at"),
        Index("ix_audit_logs_action_created", "action", "created_at"),
    )


class CompanyCredit(Base):
    """Per-company monthly spend cap + alert state."""

    __tablename__ = "company_credits"

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    company_name: Mapped[str | None] = mapped_column(String, nullable=True)
    plan_tier: Mapped[str] = mapped_column(String, nullable=False, server_default="'free'")
    credit_limit_usd: Mapped[float] = mapped_column(
        Numeric(12, 4), nullable=False, server_default="10"
    )
    credits_consumed_mtd: Mapped[float] = mapped_column(
        Numeric(14, 8), nullable=False, server_default="0"
    )
    credits_remaining: Mapped[float] = mapped_column(
        Numeric(14, 8), nullable=False, server_default="10"
    )
    warning_threshold_pct: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="80"
    )
    alert_status: Mapped[str] = mapped_column(String, nullable=False, server_default="'normal'")
    total_lifetime_spend_usd: Mapped[float] = mapped_column(
        Numeric(14, 8), nullable=False, server_default="0"
    )
    last_reset_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
    last_warning_sent_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_critical_sent_at: Mapped[datetime | None] = mapped_column(nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now()
    )


class GamificationEvent(Base):
    """Immutable point-award event log."""

    __tablename__ = "gamification_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    event_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_gamification_events_user_created", "user_id", "created_at"),
        Index("ix_gamification_events_company_created", "company_id", "created_at"),
    )
