"""Reapply the idempotent saved-flow repair after release-line reconciliation.

Revision ID: i6j7k8l9m0n1
Revises: h5c6d7e8f9a0
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

from src.lib.flows.persisted_flow_database_migrations import (
    apply_persisted_flow_migration,
)
from src.lib.packages.persisted_flow_migration_loader import (
    PersistedFlowMigration,
    load_persisted_flow_migration_catalog,
)


revision: str = "i6j7k8l9m0n1"
down_revision: str | Sequence[str] | None = "h5c6d7e8f9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
FLOW_MIGRATION_ID = "2026-09-03.remove-allele-pending-envelope-validator"


def _flow_migration() -> PersistedFlowMigration | None:
    matches = tuple(
        migration
        for migration in load_persisted_flow_migration_catalog().migrations
        if migration.migration_id == FLOW_MIGRATION_ID
    )
    if not matches:
        return None
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one package declaration for {FLOW_MIGRATION_ID}; "
            f"found {len(matches)}"
        )
    return matches[0]


def upgrade() -> None:
    """Cover databases that had already passed the colliding main revision."""

    flow_migration = _flow_migration()
    if flow_migration is not None:
        apply_persisted_flow_migration(op.get_bind(), flow_migration)


def downgrade() -> None:
    """Forward-only repair; retired catalog selections are not restored."""
