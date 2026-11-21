"""Stored structural check results for provider files (KANBAN-1976 / ALL-1460, P6).

Creates the empty ``document_file_assessments`` table. Nothing existing is
read or changed, so live data cannot make it fail.

Revision ID: a1460f6b2c3d
Revises: d7e8f9a0b1c2
Create Date: 2026-11-21
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "a1460f6b2c3d"
down_revision: Union[str, Sequence[str], None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_file_assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_file_id", sa.String(64), nullable=False),
        sa.Column("md5", sa.String(64), nullable=False),
        sa.Column("check_version", sa.String(128), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("measured_value", sa.Integer(), nullable=True),
        sa.Column("limit_value", sa.Integer(), nullable=True),
        sa.Column("few_characters_count", sa.Integer(), nullable=True),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "provider", "provider_file_id", "md5", "check_version",
            name="uq_document_file_assessments_identity",
        ),
        sa.CheckConstraint("result IN ('ok', 'refused')", name="ck_document_file_assessments_result"),
        sa.CheckConstraint(
            "(result = 'refused') = (reason_code IS NOT NULL)",
            name="ck_document_file_assessments_reason",
        ),
        sa.CheckConstraint(
            "few_characters_count IS NULL OR few_characters_count >= 0",
            name="ck_document_file_assessments_few_characters",
        ),
    )


def downgrade() -> None:
    raise RuntimeError(
        "File check results are forward-only; recover by restoring the pre-release backup"
    )
