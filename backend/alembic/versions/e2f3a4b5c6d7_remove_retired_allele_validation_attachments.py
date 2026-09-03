"""Remove retired allele validation attachments from saved flows.

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-09-03 21:15:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op  # pyright: ignore[reportAttributeAccessIssue]
from sqlalchemy.dialects.postgresql import JSONB

from src.lib.flows.persisted_flow_migrations import migrate_persisted_flow_definition
from src.lib.packages.persisted_flow_migration_loader import (
    PersistedFlowMigration,
    load_persisted_flow_migration_catalog,
)


revision: str = "e2f3a4b5c6d7"
down_revision: str | Sequence[str] | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
FLOW_MIGRATION_ID = "2026-09-03.remove-allele-pending-envelope-validator"


def _flow_migration() -> PersistedFlowMigration:
    matches = tuple(
        migration
        for migration in load_persisted_flow_migration_catalog().migrations
        if migration.migration_id == FLOW_MIGRATION_ID
    )
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one package declaration for {FLOW_MIGRATION_ID}; "
            f"found {len(matches)}"
        )
    return matches[0]


def upgrade() -> None:
    """Apply the reviewed forward-only saved-flow repair."""

    bind = op.get_bind()
    flow_migration = _flow_migration()
    retired_attachment_ids = sorted(
        attachment.attachment_id for attachment in flow_migration.retired_attachments
    )
    rows = bind.execute(
        sa.text(
            """
            SELECT id, flow_definition
            FROM curation_flows
            WHERE jsonb_typeof(flow_definition -> 'nodes') = 'array'
              AND flow_definition::text LIKE :binding_marker
            ORDER BY id
            FOR UPDATE
            """
        ),
        {"binding_marker": f"%{flow_migration.retired_binding_id}%"},
    ).mappings()

    update_statement = sa.text(
        """
        UPDATE curation_flows
        SET flow_definition = :updated_definition,
            updated_at = now()
        WHERE id = :flow_id
          AND flow_definition = :expected_definition
        """
    ).bindparams(
        sa.bindparam("expected_definition", type_=JSONB),
        sa.bindparam("updated_definition", type_=JSONB),
    )

    for row in rows:
        result = migrate_persisted_flow_definition(
            row["flow_definition"],
            migrations=(flow_migration,),
        )
        if not result.changed:
            continue
        updated = bind.execute(
            update_statement,
            {
                "flow_id": row["id"],
                "expected_definition": row["flow_definition"],
                "updated_definition": result.definition,
            },
        )
        if updated.rowcount != 1:
            raise RuntimeError(
                f"Saved flow {row['id']} changed during retired-attachment migration"
            )

    remaining = bind.execute(
        sa.text(
            """
            SELECT count(*)
            FROM curation_flows AS flow
            CROSS JOIN LATERAL jsonb_array_elements(flow.flow_definition -> 'nodes') AS nodes(node)
            CROSS JOIN LATERAL jsonb_array_elements(
                COALESCE(nodes.node #> '{data,validation_attachments}', '[]'::jsonb)
            ) AS attachments(attachment)
            WHERE nodes.node #>> '{data,agent_id}' = :agent_id
              AND attachments.attachment ->> 'attachment_id' = ANY(:retired_attachment_ids)
            """
        ),
        {
            "agent_id": flow_migration.agent_id,
            "retired_attachment_ids": retired_attachment_ids,
        },
    ).scalar_one()
    if remaining:
        raise RuntimeError(
            f"Retired allele validation attachments remain after migration: {remaining}"
        )


def downgrade() -> None:
    """Forward-only repair; retired catalog selections are not restored."""
