"""Drop extraction-result profile and domain scope columns.

Revision ID: q2r3s4t5u6v7
Revises: 08b9c0d1e2f3, d4e5f6a7b8c9
Create Date: 2026-03-30 22:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "q2r3s4t5u6v7"
down_revision: Union[str, Sequence[str], None] = ("08b9c0d1e2f3", "d4e5f6a7b8c9")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.drop_column("extraction_results", "profile_key")
    op.drop_column("extraction_results", "domain_key")


def downgrade() -> None:
    """Downgrade schema."""

    op.add_column(
        "extraction_results",
        sa.Column("domain_key", sa.String(), nullable=True),
    )
    op.add_column(
        "extraction_results",
        sa.Column("profile_key", sa.String(), nullable=True),
    )
