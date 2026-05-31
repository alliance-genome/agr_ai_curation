"""Remove the chemical_extractor system agent from the unified agents table.

The standalone ``chemical_extractor`` placeholder agent has been removed from the
package (``packages/alliance/agents/chemical_extractor`` and the
``chemical_condition`` domain pack are deleted). The historical seed migrations
``f7a8b9c0d1e2_add_chemical_extractor_system_agent`` and
``v4w5x6y7z8a9_seed_unified_agents`` are left untouched; this forward migration
removes the seeded ``agents`` row and the seeded ``prompt_templates`` rows so the
database matches the package after bootstrap.

This is the delete-on-removal counterpart to the package.yaml -> agents sync
(``sync_system_agents``), which deactivates agents that no longer have a current
package source. Because the chemical_extractor bundle is fully removed (not just
disabled), this migration deletes the system agent row and every
``chemical_extractor`` prompt template (system + group), regardless of which seed
migration created them, so no dangling chemical_extractor records remain.

Revision ID: r4s5t6u7v8w9
Revises: q3r4s5t6u7v8
Create Date: 2026-05-31
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "r4s5t6u7v8w9"
down_revision = "q3r4s5t6u7v8"
branch_labels = None
depends_on = None


_AGENT_KEY = "chemical_extractor"


def upgrade() -> None:
    connection = op.get_bind()

    connection.execute(
        sa.text(
            """
            DELETE FROM agents
            WHERE visibility = 'system'
              AND agent_key = :agent_key
            """
        ),
        {"agent_key": _AGENT_KEY},
    )

    connection.execute(
        sa.text(
            """
            DELETE FROM prompt_templates
            WHERE agent_name = :agent_name
            """
        ),
        {"agent_name": _AGENT_KEY},
    )


def downgrade() -> None:
    # The chemical_extractor agent bundle and chemical_condition domain pack are
    # deleted from the package, so the seed cannot be reconstructed here. The
    # historical f7a8b9c0d1e2 seed migration already skips gracefully when the
    # bundle is absent, so this downgrade is intentionally a no-op.
    pass
