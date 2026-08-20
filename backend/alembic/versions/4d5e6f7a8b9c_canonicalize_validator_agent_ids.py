"""Canonicalize the four original validator agent identities.

Revision ID: 4d5e6f7a8b9c
Revises: 3c4d5e6f7a8b
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op  # pyright: ignore[reportAttributeAccessIssue]


revision: str = "4d5e6f7a8b9c"
down_revision: str | Sequence[str] | None = "3c4d5e6f7a8b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CANONICAL_CASE = """
CASE {value}
    WHEN 'gene' THEN 'gene_validation'
    WHEN 'allele' THEN 'allele_validation'
    WHEN 'disease' THEN 'disease_validation'
    WHEN 'chemical' THEN 'chemical_validation'
END
"""
_ALIASES = "'gene', 'allele', 'disease', 'chemical'"


def upgrade() -> None:
    """Move persisted agent references before retiring the public aliases."""

    bind = op.get_bind()

    for column_name in ("template_source", "group_rules_component"):
        bind.execute(
            sa.text(
                f"""
                UPDATE agents
                SET {column_name} = {_CANONICAL_CASE.format(value=column_name)}
                WHERE {column_name} IN ({_ALIASES})
                """
            )
        )

    bind.execute(
        sa.text(
            f"""
            UPDATE curation_flows
            SET flow_definition = jsonb_set(
                flow_definition,
                '{{nodes}}',
                (
                    SELECT jsonb_agg(
                        CASE
                            WHEN node #>> '{{data,agent_id}}' IN ({_ALIASES})
                            THEN jsonb_set(
                                node,
                                '{{data,agent_id}}',
                                to_jsonb(
                                    {_CANONICAL_CASE.format(value="node #>> '{data,agent_id}'")}
                                ),
                                false
                            )
                            ELSE node
                        END
                        ORDER BY ordinal
                    )
                    FROM jsonb_array_elements(flow_definition -> 'nodes')
                        WITH ORDINALITY AS nodes(node, ordinal)
                ),
                false
            )
            WHERE jsonb_typeof(flow_definition -> 'nodes') = 'array'
              AND EXISTS (
                  SELECT 1
                  FROM jsonb_array_elements(flow_definition -> 'nodes') AS nodes(node)
                  WHERE node #>> '{{data,agent_id}}' IN ({_ALIASES})
              )
            """
        )
    )

    # Rename prompt versions when that exact canonical version is absent. If it
    # already exists, retain the legacy row for audit references but deactivate it.
    bind.execute(
        sa.text(
            f"""
            UPDATE prompt_templates AS legacy
            SET agent_name = {_CANONICAL_CASE.format(value="legacy.agent_name")}
            WHERE legacy.agent_name IN ({_ALIASES})
              AND NOT EXISTS (
                  SELECT 1
                  FROM prompt_templates AS canonical
                  WHERE canonical.agent_name =
                        {_CANONICAL_CASE.format(value="legacy.agent_name")}
                    AND canonical.prompt_type = legacy.prompt_type
                    AND canonical.version = legacy.version
                    AND canonical.group_id IS NOT DISTINCT FROM legacy.group_id
              )
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            UPDATE prompt_templates
            SET is_active = false
            WHERE agent_name IN ({_ALIASES})
              AND is_active = true
            """
        )
    )

    bind.execute(
        sa.text(
            f"""
            UPDATE prompt_execution_log
            SET agent_name = {_CANONICAL_CASE.format(value="agent_name")}
            WHERE agent_name IN ({_ALIASES})
            """
        )
    )

    # Reuse the existing system row when no canonical row exists, preserving its
    # UUID and audit references. A duplicate alias is retained but made inert.
    bind.execute(
        sa.text(
            f"""
            UPDATE agents AS legacy
            SET agent_key = {_CANONICAL_CASE.format(value="legacy.agent_key")}
            WHERE legacy.visibility = 'system'
              AND legacy.agent_key IN ({_ALIASES})
              AND NOT EXISTS (
                  SELECT 1
                  FROM agents AS canonical
                  WHERE canonical.agent_key =
                        {_CANONICAL_CASE.format(value="legacy.agent_key")}
              )
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            UPDATE agents
            SET is_active = false,
                supervisor_enabled = false
            WHERE visibility = 'system'
              AND agent_key IN ({_ALIASES})
            """
        )
    )


def downgrade() -> None:
    """Keep canonical identities; retired public aliases are not restored."""
