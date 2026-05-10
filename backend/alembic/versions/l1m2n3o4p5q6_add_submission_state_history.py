"""Add submission target state and result history.

Revision ID: l1m2n3o4p5q6
Revises: k0l1m2n3o4p5
Create Date: 2026-05-10 17:45:00.000000
"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "l1m2n3o4p5q6"
down_revision: Union[str, Sequence[str], None] = "k0l1m2n3o4p5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JSONB = postgresql.JSONB(astext_type=sa.Text())
JSONB_EMPTY_ARRAY = sa.text("'[]'::jsonb")
JSONB_EMPTY_OBJECT = sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.add_column(
        "curation_submissions",
        sa.Column(
            "submission_state",
            JSONB,
            nullable=False,
            server_default=JSONB_EMPTY_OBJECT,
        ),
    )
    op.add_column(
        "curation_submissions",
        sa.Column(
            "target_result_history",
            JSONB,
            nullable=False,
            server_default=JSONB_EMPTY_ARRAY,
        ),
    )


def downgrade() -> None:
    op.drop_column("curation_submissions", "target_result_history")
    op.drop_column("curation_submissions", "submission_state")
