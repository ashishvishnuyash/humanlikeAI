"""user_gamification_extra_fields

Revision ID: 15e3c8607748
Revises: e7169d9409b3
Create Date: 2026-05-05 00:23:53.149446

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '15e3c8607748'
down_revision: Union[str, Sequence[str], None] = 'e7169d9409b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_gamification",
        sa.Column("longest_streak", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "user_gamification",
        sa.Column("challenges_completed", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "user_gamification",
        sa.Column("weekly_goal", sa.Integer(), nullable=False, server_default="5"),
    )
    op.add_column(
        "user_gamification",
        sa.Column("monthly_goal", sa.Integer(), nullable=False, server_default="20"),
    )

    # Backfill longest_streak from current streak
    op.execute("UPDATE user_gamification SET longest_streak = streak")


def downgrade() -> None:
    op.drop_column("user_gamification", "monthly_goal")
    op.drop_column("user_gamification", "weekly_goal")
    op.drop_column("user_gamification", "challenges_completed")
    op.drop_column("user_gamification", "longest_streak")
