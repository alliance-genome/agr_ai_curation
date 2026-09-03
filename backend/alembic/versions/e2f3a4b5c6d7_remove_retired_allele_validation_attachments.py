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

from src.lib.flows.persisted_flow_migrations import (
    RETIRED_ALLELE_PENDING_VALIDATOR_ATTACHMENT_IDS,
    migrate_persisted_flow_definition,
)


revision: str = "e2f3a4b5c6d7"
down_revision: str | Sequence[str] | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the reviewed forward-only saved-flow repair."""

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT id, flow_definition
            FROM curation_flows
            WHERE jsonb_typeof(flow_definition -> 'nodes') = 'array'
              AND flow_definition::text LIKE '%allele_pending_envelope_validator%'
            ORDER BY id
            FOR UPDATE
            """
        )
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
        result = migrate_persisted_flow_definition(row["flow_definition"])
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
            WHERE nodes.node #>> '{data,agent_id}' = 'allele_extractor'
              AND attachments.attachment ->> 'attachment_id' = ANY(:retired_attachment_ids)
            """
        ),
        {"retired_attachment_ids": sorted(RETIRED_ALLELE_PENDING_VALIDATOR_ATTACHMENT_IDS)},
    ).scalar_one()
    if remaining:
        raise RuntimeError(
            f"Retired allele validation attachments remain after migration: {remaining}"
        )


def downgrade() -> None:
    """Forward-only repair; retired catalog selections are not restored."""
