"""Community & gamification domain: posts, replies, anonymous profiles,
user gamification stats, wellness challenges."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.models import Base


class AnonymousProfile(Base):
    __tablename__ = "anonymous_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    handle: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    avatar: Mapped[str | None] = mapped_column(String, nullable=True)
    extras: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )


class CommunityPost(Base):
    __tablename__ = "community_posts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
    )
    anonymous_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("anonymous_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    likes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    replies: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    is_approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    extras: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "ix_community_posts_company_created", "company_id", "created_at"
        ),
    )


class CommunityReply(Base):
    __tablename__ = "community_replies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    post_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("community_posts.id", ondelete="CASCADE"),
        nullable=False,
    )
    anonymous_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("anonymous_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    extras: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "ix_community_replies_post_approved",
            "post_id",
            "is_approved",
            "created_at",
        ),
    )


class UserGamification(Base):
    __tablename__ = "user_gamification"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
    )
    points: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    level: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    badges: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default="{}"
    )
    streak: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    extras: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now()
    )


class WellnessChallenge(Base):
    __tablename__ = "wellness_challenges"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    starts_at: Mapped[datetime | None] = mapped_column(nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "ix_wellness_challenges_company_active", "company_id", "is_active"
        ),
    )
