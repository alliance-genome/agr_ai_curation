"""Move remaining custom agents onto the supported GPT-5.6 catalog.

The original GPT-5.6 data migration ran while GPT-5.5 and GPT-5.4 Mini
remained curator-visible. Custom agents could therefore select those retired
IDs after that revision had already been applied. Reconcile them again before
the compatibility catalog entries are removed.

Revision ID: 0f1e2d3c4b5a
Revises: e8f9a0b1c2d3
Create Date: 2026-08-14 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]


revision: str = "0f1e2d3c4b5a"
down_revision: str | Sequence[str] | None = "e8f9a0b1c2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Reconcile post-rollout custom rows without changing reasoning settings."""
    op.execute(
        """
        UPDATE agents
        SET model_id = 'gpt-5.6-sol',
            updated_at = now()
        WHERE model_id = 'gpt-5.5'
          AND visibility != 'system'
        """
    )
    op.execute(
        """
        UPDATE agents
        SET model_id = 'gpt-5.6-terra',
            updated_at = now()
        WHERE model_id = 'gpt-5.4-mini'
          AND visibility != 'system'
        """
    )


def downgrade() -> None:
    """Keep canonical model IDs; the retired catalog is not restored."""
