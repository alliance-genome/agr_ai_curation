"""Add envelope revision refs to validation snapshots.

Revision ID: p2q3r4s5t6u7
Revises: o1p2q3r4s5t6
Create Date: 2026-05-27 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "p2q3r4s5t6u7"
down_revision: Union[str, Sequence[str], None] = "o1p2q3r4s5t6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "validation_snapshots",
        sa.Column("envelope_id", sa.String(), nullable=True),
    )
    op.add_column(
        "validation_snapshots",
        sa.Column("envelope_revision", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_validation_snapshots_envelope_id_domain_envelopes",
        "validation_snapshots",
        "domain_envelopes",
        ["envelope_id"],
        ["envelope_id"],
    )
    op.create_check_constraint(
        "ck_validation_snapshots_envelope_revision",
        "validation_snapshots",
        "(envelope_id IS NULL AND envelope_revision IS NULL) "
        "OR (envelope_id IS NOT NULL AND envelope_revision IS NOT NULL "
        "AND envelope_revision >= 1)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_validation_snapshots_envelope_revision",
        "validation_snapshots",
        type_="check",
    )
    op.drop_constraint(
        "fk_validation_snapshots_envelope_id_domain_envelopes",
        "validation_snapshots",
        type_="foreignkey",
    )
    op.drop_column("validation_snapshots", "envelope_revision")
    op.drop_column("validation_snapshots", "envelope_id")
