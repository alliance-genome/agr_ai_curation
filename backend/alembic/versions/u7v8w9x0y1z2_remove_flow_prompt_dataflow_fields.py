"""Remove flow prompt dataflow fields from saved definitions.

Revision ID: u7v8w9x0y1z2
Revises: t6u7v8w9x0y1
Create Date: 2026-06-06 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "u7v8w9x0y1z2"
down_revision: Union[str, Sequence[str], None] = "t6u7v8w9x0y1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Normalize stored flow nodes away from prompt-level dataflow routing."""

    op.execute(
        sa.text(
            """
            UPDATE curation_flows
            SET
                flow_definition = jsonb_set(
                    flow_definition,
                    '{nodes}',
                    (
                        SELECT jsonb_agg(
                            CASE
                                WHEN jsonb_typeof(node -> 'data') = 'object'
                                THEN jsonb_set(
                                    node,
                                    '{data}',
                                    (node -> 'data') - 'input_source' - 'custom_input',
                                    true
                                )
                                ELSE node
                            END
                            ORDER BY ordinality
                        )
                        FROM jsonb_array_elements(flow_definition -> 'nodes')
                            WITH ORDINALITY AS nodes(node, ordinality)
                    ),
                    true
                ),
                updated_at = now()
            WHERE jsonb_typeof(flow_definition -> 'nodes') = 'array'
              AND EXISTS (
                  SELECT 1
                  FROM jsonb_array_elements(flow_definition -> 'nodes') AS nodes(node)
                  WHERE jsonb_typeof(node -> 'data') = 'object'
                    AND (
                        (node -> 'data') ? 'input_source'
                        OR (node -> 'data') ? 'custom_input'
                    )
              )
            """
        )
    )


def downgrade() -> None:
    """Forward-only normalization; legacy prompt routing fields are not restored."""

