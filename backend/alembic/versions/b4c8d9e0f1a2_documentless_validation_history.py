"""Allow authenticated chat extraction history without inventing a PDF source.

Revision ID: b4c8d9e0f1a2
Revises: a3b7c8d9e0f1
"""
from alembic import op
import sqlalchemy as sa

revision = "b4c8d9e0f1a2"
down_revision = "a3b7c8d9e0f1"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("validation_artifacts", "document_id", existing_type=sa.String(), nullable=True)


def downgrade():
    raise RuntimeError("Validation history is forward-only; retained facts must not be deleted")
