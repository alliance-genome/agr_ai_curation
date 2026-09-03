"""Remove retired allele validation selections from active saved flows.

Revision ID: i6d7e8f9a0b1
Revises: h5c6d7e8f9a0

The update audit trigger retains the exact pre-migration row for recovery. The
repair uses the same versioned definition migration as API hydration and flow
execution, and every write is guarded by its complete JSONB preimage.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
import json

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from src.lib.flows.persisted_definition_migrations import (
    migrate_persisted_flow_definition,
)


revision: str = "i6d7e8f9a0b1"
down_revision: str | Sequence[str] | None = "h5c6d7e8f9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _migrate(bind: sa.engine.Connection) -> Counter[str]:
    bind.execute(
        sa.text(
            "SET LOCAL application_name = "
            "'alembic:i6d7e8f9a0b1:retired-allele-flow-selections'"
        )
    )
    audit_trigger_enabled = bind.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_trigger
                WHERE tgrelid = 'curation_flows'::regclass
                  AND tgname = 'audit_curation_flows_update'
                  AND NOT tgisinternal
                  AND tgenabled IN ('O', 'A')
            )
            """
        )
    ).scalar_one()
    if not audit_trigger_enabled:
        raise RuntimeError(
            "Refusing saved-flow repair because audit_curation_flows_update "
            "is absent or disabled"
        )

    bind.execute(sa.text("LOCK TABLE curation_flows IN SHARE ROW EXCLUSIVE MODE"))
    rows = bind.execute(
        sa.text(
            """
            SELECT id, flow_definition
            FROM curation_flows
            WHERE is_active = true
            ORDER BY id
            FOR UPDATE
            """
        )
    ).mappings().all()
    update_statement = sa.text(
        """
        UPDATE curation_flows
        SET flow_definition = :new_definition,
            updated_at = now()
        WHERE id = :flow_id
          AND is_active = true
          AND flow_definition = :old_definition
        """
    ).bindparams(
        sa.bindparam("new_definition", type_=JSONB),
        sa.bindparam("old_definition", type_=JSONB),
    )

    counts: Counter[str] = Counter()
    for row in rows:
        counts["active_examined"] += 1
        old_definition = row["flow_definition"]
        if not isinstance(old_definition, Mapping):
            raise RuntimeError("Active saved flow has a non-object definition")
        result = migrate_persisted_flow_definition(old_definition)
        if not result.applied_versions:
            continue
        counts["active_matched_before"] += 1
        update_result = bind.execute(
            update_statement,
            {
                "flow_id": row["id"],
                "old_definition": dict(old_definition),
                "new_definition": result.definition,
            },
        )
        if update_result.rowcount != 1:
            raise RuntimeError("Saved flow changed concurrently during guarded repair")
        counts["active_repaired"] += 1

    remaining_rows = bind.execute(
        sa.text(
            """
            SELECT flow_definition
            FROM curation_flows
            WHERE is_active = true
            ORDER BY id
            """
        )
    ).mappings().all()
    for row in remaining_rows:
        result = migrate_persisted_flow_definition(row["flow_definition"])
        if result.applied_versions:
            counts["active_matched_after"] += 1
    if counts["active_matched_after"]:
        raise RuntimeError("Retired allele saved-flow selections remain after repair")

    print("RETIRED_ALLELE_FLOW_REPAIR_SUMMARY=" + json.dumps(dict(sorted(counts.items()))))
    return counts


def upgrade() -> None:
    _migrate(op.get_bind())


def downgrade() -> None:
    """Forward-only repair; audit_log retains each exact pre-migration row."""
