"""Remove retired resolver tools from editable configuration, preserving history.

Revision ID: b8f2c3d4e5a6
Revises: a7f1c2e3d4b5

Leaving these IDs on agents causes startup validation to persistently deactivate
otherwise valid custom agents. Remove only the deliberately retired IDs, retain
all other tools in their original order, and never change activity or ownership.
Immutable executable snapshots and flow pins remain untouched: affected owners
must explicitly save a replacement revision and select it in their flows. This
is not a promise that historical executable revisions become runnable.
"""
from alembic import op
import sqlalchemy as sa

revision = "b8f2c3d4e5a6"
down_revision = "a7f1c2e3d4b5"
branch_labels = None
depends_on = None

RETIRED_TOOLS = (
    "search_domain_field_terms", "inspect_ontology_term", "resolve_domain_field_term",
)


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("""
        UPDATE agents a
        SET tool_ids = (
            SELECT COALESCE(jsonb_agg(tool ORDER BY position), '[]'::jsonb)
            FROM jsonb_array_elements(a.tool_ids) WITH ORDINALITY AS saved(tool, position)
            WHERE NOT COALESCE((tool #>> '{}') = ANY(CAST(:retired AS text[])), false)
        ), updated_at = now()
        WHERE jsonb_typeof(a.tool_ids) = 'array'
          AND a.tool_ids ?| CAST(:retired AS text[])
    """), {"retired": list(RETIRED_TOOLS)})
    connection.execute(sa.text("""
        DELETE FROM tool_policies WHERE tool_key = ANY(CAST(:retired AS text[]))
    """), {"retired": list(RETIRED_TOOLS)})


def downgrade() -> None:
    """Forward-only retirement: do not restore unavailable executable tools."""
