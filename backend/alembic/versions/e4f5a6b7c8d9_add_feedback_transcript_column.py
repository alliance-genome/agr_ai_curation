"""Add conversation_transcript column to feedback_reports.

Revision ID: e4f5a6b7c8d9
Revises: z9a0b1c2d3e4
Create Date: 2026-04-22 17:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, Sequence[str], None] = "z9a0b1c2d3e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - add durable feedback transcript storage."""

    op.add_column(
        "feedback_reports",
        sa.Column(
            "conversation_transcript",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Downgrade schema - remove durable feedback transcript storage."""

    op.drop_column("feedback_reports", "conversation_transcript")
