"""Review sessions are private to their creator: drop assignment and scopes.

Drops ``curation_review_sessions.assigned_curator_id`` and its index, removes
the ``curator`` sort from saved views, and strips the retired
``inventory_scope`` and ``curator_ids`` keys from stored saved-view filters so
those views keep loading under the strict filter contract (ALL-1547).

Revision ID: b5c6d7e8f9a0
Revises: a2b3c4d5e6f7
Create Date: 2026-11-11
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "b5c6d7e8f9a0"
down_revision: Union[str, Sequence[str], None] = "a2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PREVIOUS_SORT_FIELDS = (
    "prepared_at",
    "last_worked_at",
    "status",
    "document_title",
    "adapter",
    "candidate_count",
    "validation",
    "evidence",
    "curator",
)
CURRENT_SORT_FIELDS = tuple(field for field in PREVIOUS_SORT_FIELDS if field != "curator")
RETIRED_FILTER_KEYS = ("inventory_scope", "curator_ids")
SORT_CHECK = "ck_curation_saved_views_sort_by"


def _replace_sort_check(values: Sequence[str]) -> None:
    op.drop_constraint(SORT_CHECK, "curation_saved_views", type_="check")
    quoted = ", ".join(f"'{value}'" for value in values)
    op.create_check_constraint(SORT_CHECK, "curation_saved_views", f"sort_by IN ({quoted})")


def upgrade() -> None:
    op.execute(
        "UPDATE curation_saved_views SET sort_by = 'prepared_at' WHERE sort_by = 'curator'"
    )
    _replace_sort_check(CURRENT_SORT_FIELDS)
    retired_keys = ", ".join(f"'{key}'" for key in RETIRED_FILTER_KEYS)
    op.execute(
        "UPDATE curation_saved_views "
        f"SET filters = filters - ARRAY[{retired_keys}]::text[] "
        f"WHERE filters ?| ARRAY[{retired_keys}]::text[]"
    )
    op.drop_index("ix_curation_sessions_assigned_curator", table_name="curation_review_sessions")
    op.drop_column("curation_review_sessions", "assigned_curator_id")


def downgrade() -> None:
    # Restores the schema only: former assignments and the stripped filter
    # keys are not recoverable, and the restored filter defaults apply.
    op.add_column(
        "curation_review_sessions",
        sa.Column("assigned_curator_id", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_curation_sessions_assigned_curator",
        "curation_review_sessions",
        ["assigned_curator_id"],
    )
    _replace_sort_check(PREVIOUS_SORT_FIELDS)
