"""Fix file_hash column length from varchar(32) to varchar(64)

The file_hash column stores SHA-256 hashes which are 64 characters in hex format.
The original migration created it as varchar(32) which was too short.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2025-11-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Extend file_hash column to varchar(64) for SHA-256 hashes."""
    op.alter_column(
        'pdf_documents',
        'file_hash',
        existing_type=sa.String(32),
        type_=sa.String(64),
        existing_nullable=False
    )


def downgrade() -> None:
    """Revert file_hash column to varchar(32).

    WARNING: This will truncate any existing hashes longer than 32 characters.
    """
    op.alter_column(
        'pdf_documents',
        'file_hash',
        existing_type=sa.String(64),
        type_=sa.String(32),
        existing_nullable=False
    )
