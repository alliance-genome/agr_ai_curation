"""Append-only discovered review coverage; existing quotas remain immutable.

Revision ID: d6e0f1a2b3c4
Revises: c5d9e0f1a2b3
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "d6e0f1a2b3c4"
down_revision = "c5d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "validation_review_coverage",
        sa.Column("artifact_id", sa.String(36), primary_key=True),
        sa.Column("episode_id", sa.String(36), nullable=False, unique=True),
        sa.Column(
            "scope_id",
            sa.String(36),
            sa.ForeignKey("validation_review_scopes.id"),
            nullable=False,
        ),
        sa.Column("targets", JSONB, nullable=False),
        sa.Column("allowances", JSONB, nullable=False),
        sa.ForeignKeyConstraint(
            ["artifact_id", "episode_id"],
            ["validation_facts.artifact_id", "validation_facts.id"],
        ),
    )
    op.create_index(
        "ix_validation_review_coverage_scope_id",
        "validation_review_coverage",
        ["scope_id"],
    )
    op.execute("""
        CREATE FUNCTION protect_validation_review_coverage() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Review coverage is append-only';
        END $$;
        CREATE TRIGGER protect_validation_review_coverage
            BEFORE UPDATE OR DELETE ON validation_review_coverage
            FOR EACH ROW EXECUTE FUNCTION protect_validation_review_coverage();
    """)


def downgrade():
    raise RuntimeError("Review history is retained; use a forward recovery migration")
