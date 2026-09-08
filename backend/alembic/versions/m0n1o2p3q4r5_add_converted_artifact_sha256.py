"""Record exact downloaded converted-artifact identity; historical values stay NULL."""

from alembic import op
import sqlalchemy as sa

revision = "m0n1o2p3q4r5"
down_revision = "l9m0n1o2p3q4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pdf_documents",
        sa.Column("source_converted_artifact_sha256", sa.String(64), nullable=True),
    )
    op.create_check_constraint(
        "ck_pdf_documents_converted_artifact_sha256",
        "pdf_documents",
        "source_converted_artifact_sha256 IS NULL OR "
        "source_converted_artifact_sha256 ~ '^[0-9a-f]{64}$'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_pdf_documents_converted_artifact_sha256", "pdf_documents", type_="check",
    )
    op.drop_column("pdf_documents", "source_converted_artifact_sha256")
