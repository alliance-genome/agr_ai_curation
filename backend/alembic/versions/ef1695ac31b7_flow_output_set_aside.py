"""Save projected set-aside membership for output-specific detail requests."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "ef1695ac31b7"
down_revision = "d1460fa1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flow_output_set_aside",
        sa.Column("output_id", UUID(), primary_key=True),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("flow_run_id", sa.String(), nullable=False),
        sa.Column("document_id", UUID(), sa.ForeignKey("pdf_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("included_keys", JSONB(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("flow_output_set_aside")
