"""Allow curator validation override action-log events.

Revision ID: m1n2o3p4q5r6
Revises: l1m2n3o4p5q6
Create Date: 2026-05-14
"""

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "m1n2o3p4q5r6"
down_revision: Union[str, Sequence[str], None] = "l1m2n3o4p5q6"
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


def _check_sql(values: Sequence[str]) -> str:
    quoted_values = ", ".join(f"'{value}'" for value in values)
    return f"action_type IN ({quoted_values})"


def upgrade() -> None:
    op.drop_constraint(
        "ck_curation_action_log_action_type",
        "curation_action_log",
        type_="check",
    )
    op.create_check_constraint(
        "ck_curation_action_log_action_type",
        "curation_action_log",
        _check_sql(CURRENT_ACTION_TYPES),
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_curation_action_log_action_type",
        "curation_action_log",
        type_="check",
    )
    op.create_check_constraint(
        "ck_curation_action_log_action_type",
        "curation_action_log",
        _check_sql(PREVIOUS_ACTION_TYPES),
    )
