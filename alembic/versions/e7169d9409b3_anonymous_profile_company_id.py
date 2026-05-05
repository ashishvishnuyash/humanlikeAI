"""anonymous_profile_company_id

Revision ID: e7169d9409b3
Revises: a1f3e8b92c47
Create Date: 2026-05-05 00:00:40.986135

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = 'e7169d9409b3'
down_revision: Union[str, Sequence[str], None] = 'a1f3e8b92c47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add column nullable
    op.add_column(
        "anonymous_profiles",
        sa.Column("company_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_anonymous_profiles_company_id",
        "anonymous_profiles",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_anonymous_profiles_company_id",
        "anonymous_profiles",
        ["company_id"],
    )

    # 2. Backfill company_id from the related user row
    op.execute(
        """
        UPDATE anonymous_profiles ap
        SET company_id = u.company_id
        FROM users u
        WHERE ap.user_id = u.id
          AND ap.company_id IS NULL
        """
    )

    # 3. Drop the old global-unique constraint on user_id and replace with
    #    a composite unique index on (user_id, company_id).
    op.drop_constraint(
        "anonymous_profiles_user_id_key",
        "anonymous_profiles",
        type_="unique",
    )
    op.create_index(
        "ix_anonymous_profiles_user_company",
        "anonymous_profiles",
        ["user_id", "company_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_anonymous_profiles_user_company",
        table_name="anonymous_profiles",
    )
    op.create_unique_constraint(
        "anonymous_profiles_user_id_key",
        "anonymous_profiles",
        ["user_id"],
    )
    op.drop_index(
        "ix_anonymous_profiles_company_id",
        table_name="anonymous_profiles",
    )
    op.drop_constraint(
        "fk_anonymous_profiles_company_id",
        "anonymous_profiles",
        type_="foreignkey",
    )
    op.drop_column("anonymous_profiles", "company_id")
