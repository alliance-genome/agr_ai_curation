"""Database runner for package-declared persisted-flow repairs."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection

from .persisted_flow_migrations import migrate_persisted_flow_definition
from ..packages.persisted_flow_migration_loader import PersistedFlowMigration


def apply_persisted_flow_migration(
    bind: Connection,
    flow_migration: PersistedFlowMigration,
) -> None:
    """Apply one exact, guarded persisted-flow repair transactionally."""

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
            WHERE attachments.attachment ->> 'attachment_id' = ANY(:retired_attachment_ids)
            """
        ),
        {"retired_attachment_ids": retired_attachment_ids},
    ).scalar_one()
    if remaining:
        raise RuntimeError(
            f"Retired validation attachments remain after migration: {remaining}"
        )


__all__ = ["apply_persisted_flow_migration"]
