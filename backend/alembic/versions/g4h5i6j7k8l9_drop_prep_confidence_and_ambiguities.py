"""Drop prep confidence and ambiguity columns from curation candidates.

Revision ID: g4h5i6j7k8l9
Revises: f2a3b4c5d6e7
Create Date: 2026-03-28 19:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = "g4h5i6j7k8l9"
down_revision: Union[str, Sequence[str], None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JSONB_EMPTY_ARRAY = sa.text("'[]'::jsonb")


def upgrade() -> None:
    """Upgrade schema."""

    op.drop_constraint(
        "ck_curation_candidates_confidence",
        "curation_candidates",
        type_="check",
    )
    op.drop_column("curation_candidates", "confidence")
    op.drop_column("curation_candidates", "unresolved_ambiguities")


def downgrade() -> None:
    """Downgrade schema."""

    op.add_column(
        "curation_candidates",
        sa.Column(
            "unresolved_ambiguities",
            JSONB,
            nullable=False,
            server_default=JSONB_EMPTY_ARRAY,
        ),
    )
    op.add_column(
        "curation_candidates",
        sa.Column("confidence", sa.Float(), nullable=True),
    )
    op.create_check_constraint(
        "ck_curation_candidates_confidence",
        "curation_candidates",
        "confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)",
    )
