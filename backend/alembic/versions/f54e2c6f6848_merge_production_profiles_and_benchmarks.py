"""Join production profile history and main benchmark history.

Revision ID: f54e2c6f6848
Revises: 7c9e2a4b6d80, p3e4f5a6b7c8

The three production revision-label collisions were disambiguated without
changing their DDL bodies. Existing terminal stamps are not rewritten. See
docs/developer/guides/PRODUCTION_MAIN_MIGRATION_CONVERGENCE.md.
"""

revision = "f54e2c6f6848"
down_revision = ("7c9e2a4b6d80", "p3e4f5a6b7c8")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Both parent branches have completed; no additional DDL is required."""


def downgrade() -> None:
    """Separate the graph heads without changing either branch's data."""
