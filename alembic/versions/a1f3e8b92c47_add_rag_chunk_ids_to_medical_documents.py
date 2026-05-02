"""add rag_chunk_ids to medical_documents

Revision ID: a1f3e8b92c47
Revises: 3871cf2d1668
Create Date: 2026-05-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'a1f3e8b92c47'
down_revision: Union[str, Sequence[str], None] = '3871cf2d1668'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'medical_documents',
        sa.Column(
            'rag_chunk_ids',
            postgresql.ARRAY(sa.String()),
            server_default='{}',
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column('medical_documents', 'rag_chunk_ids')
