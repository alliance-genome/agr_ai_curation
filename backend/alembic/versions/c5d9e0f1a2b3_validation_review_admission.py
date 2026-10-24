"""Durable supervisor admission and recoverable work.

Revision ID: c5d9e0f1a2b3
Revises: b4c8d9e0f1a2
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "c5d9e0f1a2b3"
down_revision = "b4c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "validation_review_scopes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("operation_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column(
            "parent_id", sa.String(36), sa.ForeignKey("validation_review_scopes.id")
        ),
        sa.Column("contract", JSONB, nullable=False),
        sa.Column("charged", JSONB, nullable=False),
        sa.UniqueConstraint(
            "owner_id", "operation_id", name="uq_review_scope_operation"
        ),
    )
    op.create_table(
        "validation_review_episodes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("artifact_id", sa.String(36), nullable=False),
        sa.Column(
            "scope_id",
            sa.String(36),
            sa.ForeignKey("validation_review_scopes.id"),
            nullable=False,
        ),
        sa.Column(
            "predecessor_id",
            sa.String(36),
            sa.ForeignKey("validation_review_episodes.id"),
        ),
        sa.Column("policy", JSONB, nullable=False),
        sa.Column("allowances", JSONB, nullable=False),
        sa.Column("charged", JSONB, nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["artifact_id", "id"],
            ["validation_facts.artifact_id", "validation_facts.id"],
        ),
    )
    op.create_table(
        "validation_review_reservations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "episode_id",
            sa.String(36),
            sa.ForeignKey("validation_review_episodes.id"),
            nullable=False,
        ),
        sa.Column("operation_id", sa.String(), nullable=False),
        sa.Column("request", JSONB, nullable=False),
        sa.Column("charges", JSONB, nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("actual", JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "episode_id", "operation_id", name="uq_review_reservation_operation"
        ),
    )
    op.create_table(
        "validation_review_work",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "episode_id",
            sa.String(36),
            sa.ForeignKey("validation_review_episodes.id"),
            nullable=False,
        ),
        sa.Column("operation_id", sa.String(), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("outcome", JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "episode_id", "operation_id", name="uq_review_work_operation"
        ),
    )

    # Durable identities and policy cannot be reset by deleting indexes or
    # mutating the frozen part of a reservation/work row.
    for table, mutable in {
        "validation_review_scopes": ["charged"],
        "validation_review_episodes": ["charged", "state", "started_at"],
        "validation_review_reservations": ["state", "actual"],
        "validation_review_work": ["state", "lease_token", "lease_until", "outcome"],
    }.items():
        fields = ",".join("'" + field + "'" for field in mutable)
        op.execute(f"""
            CREATE FUNCTION protect_{table}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'Review admission records must be retained';
                END IF;
                IF TG_TABLE_NAME = 'validation_review_episodes' AND
                   to_jsonb(OLD)->>'started_at' IS NOT NULL AND
                   to_jsonb(OLD)->'started_at' IS DISTINCT FROM to_jsonb(NEW)->'started_at' THEN
                    RAISE EXCEPTION 'Review deadline cannot be reset';
                END IF;
                IF (to_jsonb(OLD) - ARRAY[{fields}]) IS DISTINCT FROM
                   (to_jsonb(NEW) - ARRAY[{fields}]) THEN
                    RAISE EXCEPTION 'Frozen review admission identity cannot change';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER protect_{table} BEFORE UPDATE OR DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION protect_{table}();
        """)


def downgrade():
    raise RuntimeError("Review history is retained; use a forward recovery migration")
