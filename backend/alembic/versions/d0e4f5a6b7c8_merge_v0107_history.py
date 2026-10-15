"""Converge v0.10.7 durable history with the existing main migration head.

Historical migrations remain immutable; each installation applies its missing branch.
"""
revision = "d0e4f5a6b7c8"
down_revision = ("c9d3e4f5a6b7", "c9b3e4f5a6d7")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
