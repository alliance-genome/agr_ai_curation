"""Record explicit curator consent for exact extraction-only configurations."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = "q4f5a6b7c8d9"
down_revision = "p3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "validation_acknowledgments",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("fingerprint", sa.String(71), primary_key=True),
        sa.Column("scope", postgresql.JSONB(), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("validation_acknowledgments")
