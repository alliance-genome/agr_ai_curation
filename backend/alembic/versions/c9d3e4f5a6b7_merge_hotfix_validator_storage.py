"""Converge v0.10.6 validator storage with main's retired-tool migration.

Revision ID: c9d3e4f5a6b7
Revises: b8a2d3f4e5c6, b8f2c3d4e5a6

Both historical migrations remain immutable. Existing production and main
installations converge by applying only the missing parent branch.
"""
revision = "c9d3e4f5a6b7"
down_revision = ("b8a2d3f4e5c6", "b8f2c3d4e5a6")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
