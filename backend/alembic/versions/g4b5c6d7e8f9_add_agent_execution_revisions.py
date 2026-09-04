"""Add canonical-agent executable revisions and protected head references.

Revision ID: g4b5c6d7e8f9
Revises: f3a4b5c6d7e8
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "g4b5c6d7e8f9"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_execution_revisions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "creator_id",
            sa.Integer(),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(71), nullable=False),
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("output_state", sa.String(30), nullable=False),
        sa.Column("output_mode", sa.String(30)),
        sa.Column("output_schema_key", sa.String(100)),
        sa.Column("profile_revision_id", UUID(as_uuid=True)),
        sa.Column("profile_fingerprint", sa.String(71)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("agent_id", "revision", name="uq_agent_execution_revision"),
        sa.UniqueConstraint("agent_id", "id", name="uq_agent_execution_revision_owner"),
        sa.CheckConstraint("revision > 0", name="ck_agent_execution_revision_positive"),
        sa.CheckConstraint(
            "fingerprint ~ '^sha256:[a-f0-9]{64}$'",
            name="ck_agent_execution_fingerprint",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(snapshot) = 'object'",
            name="ck_agent_execution_snapshot_object",
        ),
        sa.CheckConstraint(
            "(output_state = 'none' AND output_mode IS NULL AND output_schema_key IS NULL AND profile_revision_id IS NULL AND profile_fingerprint IS NULL) OR "
            "(output_state = 'structured_extraction' AND output_mode IS NOT NULL AND ("
            "(output_mode = 'domain' AND output_schema_key IS NOT NULL AND length(trim(output_schema_key)) > 0 AND profile_revision_id IS NULL AND profile_fingerprint IS NULL) OR "
            "(output_mode = 'profile_bound_generic' AND output_schema_key IS NULL AND profile_revision_id IS NOT NULL AND profile_fingerprint IS NOT NULL) OR "
            "(output_mode = 'unprofiled_generic' AND output_schema_key IS NULL AND profile_revision_id IS NULL AND profile_fingerprint IS NULL)))",
            name="ck_agent_execution_output_contract",
        ),
        sa.CheckConstraint(
            "(snapshot #>> '{output_contract,output_state}') IS NOT DISTINCT FROM output_state AND "
            "(snapshot #>> '{output_contract,output_mode}') IS NOT DISTINCT FROM output_mode AND "
            "(snapshot #>> '{output_contract,output_schema_key}') IS NOT DISTINCT FROM output_schema_key AND "
            "(snapshot #>> '{output_contract,generic_profile_ref,profile_revision_id}') IS NOT DISTINCT FROM profile_revision_id::text AND "
            "(snapshot #>> '{output_contract,generic_profile_ref,fingerprint}') IS NOT DISTINCT FROM profile_fingerprint",
            name="ck_agent_execution_snapshot_output_identity",
        ),
        sa.ForeignKeyConstraint(
            ["profile_revision_id", "profile_fingerprint"],
            [
                "generic_extraction_profile_revisions.id",
                "generic_extraction_profile_revisions.fingerprint",
            ],
            name="fk_agent_execution_profile_identity",
            ondelete="RESTRICT",
            match="FULL",
        ),
    )
    op.create_index(
        "ix_agent_execution_profile_revision",
        "agent_execution_revisions",
        ["profile_revision_id"],
    )
    op.add_column("agents", sa.Column("execution_revision_id", UUID(as_uuid=True)))
    op.create_foreign_key(
        "fk_agent_execution_head",
        "agents",
        "agent_execution_revisions",
        ["id", "execution_revision_id"],
        ["agent_id", "id"],
        deferrable=True,
        initially="DEFERRED",
        ondelete="NO ACTION",
    )
    op.execute("""
        CREATE FUNCTION reject_agent_execution_revision_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Agent execution revisions are immutable';
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER agent_execution_revision_immutable
        BEFORE UPDATE ON agent_execution_revisions
        FOR EACH ROW EXECUTE FUNCTION reject_agent_execution_revision_update();
    """)
    # Current heads are the only rows with complete settings. Historical
    # custom_agent_versions remain prompt-only audit records, never executable
    # revisions synthesized with today's model/tools/template configuration.
    from sqlalchemy.orm import Session
    from src.lib.agent_studio.execution_revision_service import (
        baseline_current_execution_heads,
    )

    with Session(bind=op.get_bind(), join_transaction_mode="rollback_only") as db:
        baseline_current_execution_heads(db)
        db.flush()


def downgrade():
    op.drop_constraint("fk_agent_execution_head", "agents", type_="foreignkey")
    op.drop_column("agents", "execution_revision_id")
    op.drop_table("agent_execution_revisions")
    op.execute("DROP FUNCTION reject_agent_execution_revision_update()")
