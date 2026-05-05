"""Allow candidate_deleted curation action-log events.

Revision ID: g7h8i9j0k1l2
Revises: f6a7b8c9d0e1
Create Date: 2026-05-05
"""

from alembic import op


revision = "g7h8i9j0k1l2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


OLD_ACTION_TYPES = (
    "session_created",
    "session_status_updated",
    "session_assigned",
    "candidate_created",
    "candidate_updated",
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

NEW_ACTION_TYPES = (
    "session_created",
    "session_status_updated",
    "session_assigned",
    "candidate_created",
    "candidate_deleted",
    "candidate_updated",
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


def _check_sql(values: tuple[str, ...]) -> str:
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
        _check_sql(NEW_ACTION_TYPES),
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
        _check_sql(OLD_ACTION_TYPES),
    )
