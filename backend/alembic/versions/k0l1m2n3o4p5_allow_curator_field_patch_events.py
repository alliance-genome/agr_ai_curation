"""Allow curator field-patch envelope history and action-log events.

Revision ID: k0l1m2n3o4p5
Revises: j9k0l1m2n3o4
Create Date: 2026-05-10 12:45:00.000000
"""

from collections.abc import Sequence
from typing import Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "k0l1m2n3o4p5"
down_revision: Union[str, Sequence[str], None] = "j9k0l1m2n3o4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PREVIOUS_HISTORY_EVENT_KINDS = (
    "created",
    "object_extracted",
    "object_updated",
    "field_updated",
    "validation_finding_added",
    "status_changed",
    "exported",
    "submitted",
)
CURRENT_HISTORY_EVENT_KINDS = (
    "created",
    "object_extracted",
    "object_updated",
    "field_updated",
    "curator_field_patch_accepted",
    "curator_field_patch_rejected",
    "validation_finding_added",
    "validation_rerun_requested",
    "status_changed",
    "exported",
    "submitted",
)

PREVIOUS_ACTION_TYPES = (
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
CURRENT_ACTION_TYPES = (
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


def _check_sql(column_name: str, values: Sequence[str]) -> str:
    quoted_values = ", ".join(f"'{value}'" for value in values)
    return f"{column_name} IN ({quoted_values})"


def upgrade() -> None:
    op.drop_constraint(
        "ck_domain_envelope_history_event_type",
        "domain_envelope_history",
        type_="check",
    )
    op.create_check_constraint(
        "ck_domain_envelope_history_event_type",
        "domain_envelope_history",
        _check_sql("event_type", CURRENT_HISTORY_EVENT_KINDS),
    )
    op.drop_constraint(
        "ck_curation_action_log_action_type",
        "curation_action_log",
        type_="check",
    )
    op.create_check_constraint(
        "ck_curation_action_log_action_type",
        "curation_action_log",
        _check_sql("action_type", CURRENT_ACTION_TYPES),
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
        _check_sql("action_type", PREVIOUS_ACTION_TYPES),
    )
    op.drop_constraint(
        "ck_domain_envelope_history_event_type",
        "domain_envelope_history",
        type_="check",
    )
    op.create_check_constraint(
        "ck_domain_envelope_history_event_type",
        "domain_envelope_history",
        _check_sql("event_type", PREVIOUS_HISTORY_EVENT_KINDS),
    )
