"""Add normalized_payload column to curation candidates.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-03-21 16:30:00.000000
"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JSONB = postgresql.JSONB(astext_type=sa.Text())
JSONB_EMPTY_OBJECT = sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.add_column(
        "curation_candidates",
        sa.Column(
            "normalized_payload",
            JSONB,
            nullable=False,
            server_default=JSONB_EMPTY_OBJECT,
        ),
    )

    op.execute(
        """
        UPDATE curation_candidates
        SET normalized_payload = COALESCE(metadata -> 'normalized_payload', '{}'::jsonb),
            metadata = metadata - 'normalized_payload'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE curation_candidates
        SET metadata = jsonb_set(
                COALESCE(metadata, '{}'::jsonb),
                '{normalized_payload}',
                COALESCE(normalized_payload, '{}'::jsonb),
                true
            )
        WHERE normalized_payload IS NOT NULL
        """
    )
    op.drop_column("curation_candidates", "normalized_payload")
