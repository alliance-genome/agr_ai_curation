"""Remove repair history event kinds from domain envelopes.

Revision ID: n1o2p3q4r5s6
Revises: m1n2o3p4q5r6
Create Date: 2026-05-16
"""

from collections.abc import Sequence
from typing import Union

from alembic import op


revision: str = "n1o2p3q4r5s6"
down_revision: Union[str, Sequence[str], None] = "m1n2o3p4q5r6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PREVIOUS_HISTORY_EVENT_KINDS = (
    "created",
    "object_extracted",
    "object_updated",
    "field_updated",
    "curator_field_patch_accepted",
    "curator_field_patch_rejected",
    "validation_finding_added",
    "repair_requested",
    "repair_patch_accepted",
    "repair_patch_rejected",
    "validation_rerun_requested",
    "repair_final_classified",
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


def _check_sql(values: Sequence[str]) -> str:
    quoted_values = ", ".join(f"'{value}'" for value in values)
    return f"event_type IN ({quoted_values})"


def upgrade() -> None:
    # ALL-430/ALL-476 superseded repair history with curator field patch events;
    # normalize existing indexed rows before tightening the event_type constraint.
    op.execute(
        """
        UPDATE domain_envelope_history
        SET
            event_type = CASE event_type
                WHEN 'repair_patch_accepted' THEN 'curator_field_patch_accepted'
                WHEN 'repair_patch_rejected' THEN 'curator_field_patch_rejected'
            END,
            event_json = jsonb_set(
                jsonb_set(
                    event_json,
                    '{event_type}',
                    to_jsonb((
                        CASE event_type
                            WHEN 'repair_patch_accepted'
                                THEN 'curator_field_patch_accepted'
                            WHEN 'repair_patch_rejected'
                                THEN 'curator_field_patch_rejected'
                        END
                    )::text),
                    true
                ),
                '{details}',
                COALESCE(event_json->'details', '{}'::jsonb)
                    || jsonb_build_object('legacy_repair_event_type', event_type),
                true
            )
        WHERE event_type IN ('repair_patch_accepted', 'repair_patch_rejected')
        """
    )
    op.execute(
        """
        DELETE FROM domain_envelope_history
        WHERE event_type IN ('repair_requested', 'repair_final_classified')
        """
    )
    op.drop_constraint(
        "ck_domain_envelope_history_event_type",
        "domain_envelope_history",
        type_="check",
    )
    op.create_check_constraint(
        "ck_domain_envelope_history_event_type",
        "domain_envelope_history",
        _check_sql(CURRENT_HISTORY_EVENT_KINDS),
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_domain_envelope_history_event_type",
        "domain_envelope_history",
        type_="check",
    )
    op.create_check_constraint(
        "ck_domain_envelope_history_event_type",
        "domain_envelope_history",
        _check_sql(PREVIOUS_HISTORY_EVENT_KINDS),
    )
