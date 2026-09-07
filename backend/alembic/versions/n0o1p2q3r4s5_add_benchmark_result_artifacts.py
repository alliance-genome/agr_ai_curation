"""Retain canonical result bytes; historical results remain unavailable."""

from alembic import op
import sqlalchemy as sa

revision = "n0o1p2q3r4s5"
down_revision = "m0n1o2p3q4r5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("benchmark_cells", sa.Column("result_artifact", sa.LargeBinary()))
    op.create_check_constraint(
        "ck_benchmark_cells_result_artifact", "benchmark_cells",
        "result_artifact IS NULL OR (status = 'succeeded' AND result_digest IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_benchmark_cells_result_artifact", "benchmark_cells", type_="check")
    op.drop_column("benchmark_cells", "result_artifact")
