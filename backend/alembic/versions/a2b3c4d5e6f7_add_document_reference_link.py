"""Add a curator reference link to documents, and its action-log events.

Revision ID: a2b3c4d5e6f7
Revises: f4a8b0c2d3e5
Create Date: 2026-11-05
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, Sequence[str], None] = "f4a8b0c2d3e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PREVIOUS_ACTION_TYPES = (
    "session_created",
    "session_status_updated",
    "session_assigned",
    "candidate_created",
    "candidate_deleted",
    "candidate_updated",
    "envelope_field_patched",
    "curator_validation_override",
    "candidate_accepted",
    "candidate_rejected",
    "candidate_reset",
    "validation_requested",
    "validation_completed",
    "evidence_recomputed",
    "evidence_manual_added",
    "submission_previewed",
    "submission_executed",
    "submission_retried",
)
CURRENT_ACTION_TYPES = (
    *PREVIOUS_ACTION_TYPES,
    "document_reference_linked",
    "document_reference_unlinked",
)


def _check_sql(values: Sequence[str]) -> str:
    quoted_values = ", ".join(f"'{value}'" for value in values)
    return f"action_type IN ({quoted_values})"


def _replace_action_type_check(values: Sequence[str]) -> None:
    op.drop_constraint(
        "ck_curation_action_log_action_type",
        "curation_action_log",
        type_="check",
    )
    op.create_check_constraint(
        "ck_curation_action_log_action_type",
        "curation_action_log",
        _check_sql(values),
    )


def upgrade() -> None:
    op.add_column(
        "pdf_documents",
        sa.Column("linked_reference_provider", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "pdf_documents",
        sa.Column("linked_reference_curie", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "pdf_documents",
        sa.Column("linked_reference_at", sa.DateTime(timezone=True), nullable=True),
    )
    _replace_action_type_check(CURRENT_ACTION_TYPES)


def downgrade() -> None:
    op.execute(
        "DELETE FROM curation_action_log WHERE action_type IN "
        "('document_reference_linked', 'document_reference_unlinked')"
    )
    _replace_action_type_check(PREVIOUS_ACTION_TYPES)
    op.drop_column("pdf_documents", "linked_reference_at")
    op.drop_column("pdf_documents", "linked_reference_curie")
    op.drop_column("pdf_documents", "linked_reference_provider")
