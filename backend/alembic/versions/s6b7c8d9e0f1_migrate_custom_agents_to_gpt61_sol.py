"""Move custom agents off the retired GPT-6 Sol model onto GPT-6.1 Sol.

- gpt-6-sol -> gpt-6.1-sol

Every non-system row is moved, archived ones included, because the catalog no
longer lists the retired ID and an unarchived row must still validate. System
agents are owned by package ``agent.yaml`` plus the deployment's
``AGENT_*_MODEL`` environment and are re-synced at startup, so they are not
touched here.

Saved reasoning is kept when the GPT-6.1 Sol catalog entry offers it (low,
medium, high). One reviewed mapping applies:

- ``xhigh`` -> ``high``: GPT-6.1 Sol offers curators low, medium and high
  only, so an ``xhigh`` agent could not be saved or run; ``high`` is the
  closest level it offers.

``NULL`` stays ``NULL`` (the catalog default applies). A database that runs
``r5a6b7c8d9e0`` in the same upgrade reaches this step with its GPT-5.6 rows
already on ``gpt-6-sol`` and their ``minimal``/``disabled`` values already
mapped, so they continue onto GPT-6.1 Sol here.

Immutable history is not rewritten: ``agent_execution_revisions`` snapshots
are fingerprinted and protected by a trigger. A custom agent runs from its
head revision, so the editable row changed here takes effect once the agent is
saved into a new head revision. Until then, a flow step pinned to a GPT-6 Sol
revision is refused before the run with a request to re-save the agent.

Revision ID: s6b7c8d9e0f1
Revises: b09c1d2e3f4a
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]


revision: str = "s6b7c8d9e0f1"
down_revision: str | Sequence[str] | None = "b09c1d2e3f4a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Point custom agents at GPT-6.1 Sol with a reasoning level it offers."""
    op.execute(
        """
        UPDATE agents
        SET model_id = 'gpt-6.1-sol',
            model_reasoning = CASE
                WHEN lower(trim(model_reasoning)) = 'xhigh' THEN 'high'
                ELSE model_reasoning
            END,
            updated_at = now()
        WHERE model_id = 'gpt-6-sol'
          AND visibility != 'system'
        """
    )


def downgrade() -> None:
    """Keep canonical model IDs; the retired catalog entry is not restored."""
