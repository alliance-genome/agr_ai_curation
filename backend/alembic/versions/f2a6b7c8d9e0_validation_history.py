"""Add immutable validation history, independently of current envelope projections.

Revision ID: f2a6b7c8d9e0
Revises: e1f5a6b7c8d9
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "f2a6b7c8d9e0"
down_revision = "e1f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "validation_bodies",
        sa.Column("sha256", sa.String(64), primary_key=True),
        sa.Column("content", JSONB, nullable=False),
    )
    op.create_table(
        "validation_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String, nullable=False),
        sa.Column("document_id", sa.String, nullable=False),
        sa.Column("session_id", sa.String, nullable=False),
        sa.Column("producer_id", sa.String, nullable=False),
        sa.Column("subject_kind", sa.String, nullable=False),
        sa.Column(
            "baseline_sha",
            sa.String(64),
            sa.ForeignKey("validation_bodies.sha256"),
            nullable=False,
        ),
        sa.Column("provenance", JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "owner_id", "session_id", "producer_id", name="uq_validation_producer"
        ),
    )
    op.create_table(
        "validation_facts",
        sa.Column("sequence", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "artifact_id",
            sa.String(36),
            sa.ForeignKey("validation_artifacts.id"),
            nullable=False,
        ),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("operation_id", sa.String, nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("target_id", sa.String(36)),
        sa.Column("parent_id", sa.String(36)),
        sa.Column(
            "body_sha",
            sa.String(64),
            sa.ForeignKey("validation_bodies.sha256"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("artifact_id", "id", name="uq_validation_fact_id"),
        sa.UniqueConstraint(
            "artifact_id", "operation_id", name="uq_validation_fact_operation"
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id", "parent_id"],
            ["validation_facts.artifact_id", "validation_facts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id", "target_id"],
            ["validation_facts.artifact_id", "validation_facts.id"],
        ),
    )
    op.create_index(
        "ix_validation_history_target",
        "validation_facts",
        ["artifact_id", "target_id", "sequence"],
    )
    op.create_index("ix_validation_history_sequence", "validation_facts", ["artifact_id", "sequence"])
    op.create_table(
        "validation_heads",
        sa.Column("artifact_id", sa.String(36), primary_key=True),
        sa.Column("target_id", sa.String(36), primary_key=True),
        sa.Column("version", sa.BigInteger, nullable=False),
        sa.Column("fact_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(
            ["artifact_id", "target_id"],
            ["validation_facts.artifact_id", "validation_facts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id", "fact_id"],
            ["validation_facts.artifact_id", "validation_facts.id"],
        ),
    )
    op.execute("""CREATE FUNCTION reject_validation_history_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Immutable validation history cannot be updated or deleted'; END; $$""")
    for table in ("validation_bodies", "validation_artifacts", "validation_facts"):
        op.execute(
            f"CREATE TRIGGER immutable_validation_history BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_validation_history_mutation()"
        )


def downgrade():
    raise RuntimeError(
        "Validation history is forward-only; retiring records must not delete audit data"
    )
