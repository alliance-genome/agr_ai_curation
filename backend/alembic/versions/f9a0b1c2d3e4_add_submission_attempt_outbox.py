"""Add durable idempotent direct-submission attempts.

Revision ID: f9a0b1c2d3e4
Revises: e7f8a9b0c1d2
Create Date: 2026-07-11 18:30:00.000000
"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f9a0b1c2d3e4"
down_revision: Union[str, Sequence[str], None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.drop_constraint(
        "ck_curation_submissions_status",
        "curation_submissions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_curation_submissions_status",
        "curation_submissions",
        "status IN ('preview_ready', 'export_ready', 'pending', 'queued', 'accepted', "
        "'validation_errors', 'conflict', 'manual_review_required', 'failed')",
    )
    op.add_column("curation_submissions", sa.Column("idempotency_key", sa.String(), nullable=True))
    op.add_column("curation_submissions", sa.Column("attempt_state", sa.String(), nullable=True))
    op.add_column(
        "curation_submissions",
        sa.Column(
            "attempt_state_history",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "curation_submissions",
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint(
        "uq_curation_submissions_idempotency_key",
        "curation_submissions",
        ["idempotency_key"],
    )
    op.create_index(
        "ix_submissions_retention",
        "curation_submissions",
        ["retention_expires_at"],
        unique=False,
    )
    op.create_check_constraint(
        "ck_curation_submissions_attempt_state",
        "curation_submissions",
        "attempt_state IS NULL OR attempt_state IN "
        "('pending', 'sending', 'succeeded', 'failed', 'unknown')",
    )
    op.execute(
        sa.text(
            """
            UPDATE curation_submissions
            SET idempotency_key = 'migrated:' || id::text,
                attempt_state = CASE
                    WHEN status IN ('accepted', 'queued', 'manual_review_required')
                        THEN 'succeeded'
                    ELSE 'failed'
                END,
                attempt_state_history = jsonb_build_array(
                    jsonb_build_object(
                        'state', CASE
                            WHEN status IN ('accepted', 'queued', 'manual_review_required')
                                THEN 'succeeded'
                            ELSE 'failed'
                        END,
                        'occurred_at', COALESCE(completed_at, requested_at),
                        'message', 'Backfilled from the pre-outbox submission record.'
                    )
                )
            WHERE mode = 'direct_submit'
            """
        )
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_curation_submissions_status",
        "curation_submissions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_curation_submissions_status",
        "curation_submissions",
        "status IN ('preview_ready', 'export_ready', 'queued', 'accepted', "
        "'validation_errors', 'conflict', 'manual_review_required', 'failed')",
    )
    op.drop_constraint(
        "ck_curation_submissions_attempt_state",
        "curation_submissions",
        type_="check",
    )
    op.drop_index("ix_submissions_retention", table_name="curation_submissions")
    op.drop_constraint(
        "uq_curation_submissions_idempotency_key",
        "curation_submissions",
        type_="unique",
    )
    op.drop_column("curation_submissions", "retention_expires_at")
    op.drop_column("curation_submissions", "attempt_state_history")
    op.drop_column("curation_submissions", "attempt_state")
    op.drop_column("curation_submissions", "idempotency_key")
