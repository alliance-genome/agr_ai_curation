"""Add persistent latest-intent fencing for current candidate selection.

Revision ID: a0b1c2d3e4f5
Revises: f9a0b1c2d3e4
Create Date: 2026-07-12
"""

# pyright: reportAttributeAccessIssue=false

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "a0b1c2d3e4f5"
down_revision: Union[str, Sequence[str], None] = "f9a0b1c2d3e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "curation_review_sessions",
        sa.Column("current_candidate_intent_owner", sa.String(), nullable=True),
    )
    op.add_column(
        "curation_review_sessions",
        sa.Column("current_candidate_intent_generation", sa.BigInteger(), nullable=True),
    )
    op.create_check_constraint(
        "ck_curation_sessions_candidate_intent_pair",
        "curation_review_sessions",
        "(current_candidate_intent_owner IS NULL) = "
        "(current_candidate_intent_generation IS NULL) AND "
        "(current_candidate_intent_generation IS NULL OR "
        "current_candidate_intent_generation >= 1)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_curation_sessions_candidate_intent_pair",
        "curation_review_sessions",
        type_="check",
    )
    op.drop_column("curation_review_sessions", "current_candidate_intent_generation")
    op.drop_column("curation_review_sessions", "current_candidate_intent_owner")
