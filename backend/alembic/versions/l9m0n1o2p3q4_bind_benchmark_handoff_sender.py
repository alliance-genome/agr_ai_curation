"""Bind benchmark handoff attempts to the verified initiating curator.

Historical attempts retain NULL identity; no current user may claim them.
"""

from alembic import op
import sqlalchemy as sa

revision = "l9m0n1o2p3q4"
down_revision = "k8l9m0n1o2p3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("sender_version", "sender_issuer", "sender_subject"):
        op.add_column("curation_benchmark_handoff_attempts", sa.Column(name, sa.String(), nullable=True))
    op.create_check_constraint(
        "ck_curation_benchmark_handoff_sender_identity",
        "curation_benchmark_handoff_attempts",
        "(sender_version IS NULL AND sender_issuer IS NULL AND sender_subject IS NULL) OR "
        "(sender_version IS NOT NULL AND sender_version = '1' "
        "AND sender_issuer IS NOT NULL AND sender_issuer <> '' "
        "AND sender_subject IS NOT NULL AND sender_subject <> '')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_curation_benchmark_handoff_sender_identity", "curation_benchmark_handoff_attempts", type_="check")
    for name in ("sender_subject", "sender_issuer", "sender_version"):
        op.drop_column("curation_benchmark_handoff_attempts", name)
