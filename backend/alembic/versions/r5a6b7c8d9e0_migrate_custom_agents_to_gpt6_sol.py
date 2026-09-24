"""Move custom agents off the retired GPT-5.6 models onto GPT-6 Sol.

Both retired models map to one model:

- gpt-5.6-sol   -> gpt-6-sol
- gpt-5.6-terra -> gpt-6-sol

Every non-system row is moved, archived ones included, because the catalog no
longer lists either retired ID and an unarchived row must still validate.
System agents are owned by package ``agent.yaml`` plus the deployment's
``AGENT_*_MODEL`` environment and are re-synced at startup, so they are not
touched here.

Saved reasoning is kept when the GPT-6 Sol catalog entry offers it (low,
medium, high, xhigh; verified live 2026-09-24). Two reviewed mappings apply:

- ``minimal`` -> ``low``: the API rejects ``minimal`` for GPT-6 Sol.
- ``disabled`` -> ``medium``: not a catalog level, so saving the agent onto
  GPT-6 Sol fails authoring validation. This preserves observed runtime
  behavior: ``disabled`` was never sent, so the provider default medium
  applied.

``NULL`` stays ``NULL`` (the catalog default applies).

Immutable history is not rewritten: ``agent_execution_revisions`` snapshots
are fingerprinted and protected by a trigger. A custom agent runs from its
head revision, so the editable row changed here takes effect once the agent is
saved into a new head revision.

Revision ID: r5a6b7c8d9e0
Revises: q4f5a6b7c8d9
Create Date: 2026-09-24
"""

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]


revision = "r5a6b7c8d9e0"
down_revision = "q4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Point custom agents at GPT-6 Sol with a reasoning level it accepts."""
    op.execute(
        """
        UPDATE agents
        SET model_id = 'gpt-6-sol',
            model_reasoning = CASE
                WHEN lower(trim(model_reasoning)) = 'minimal' THEN 'low'
                WHEN lower(trim(model_reasoning)) = 'disabled' THEN 'medium'
                ELSE model_reasoning
            END,
            updated_at = now()
        WHERE model_id IN ('gpt-5.6-sol', 'gpt-5.6-terra')
          AND visibility != 'system'
        """
    )


def downgrade() -> None:
    """Keep canonical model IDs; the retired catalog entries are not restored."""
