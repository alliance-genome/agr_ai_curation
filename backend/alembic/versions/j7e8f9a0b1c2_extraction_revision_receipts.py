"""Retain immutable execution identity on persisted extraction results.

Revision ID: j7e8f9a0b1c2
Revises: i6d7e8f9a0b1
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "j7e8f9a0b1c2"
down_revision = "i6d7e8f9a0b1"
branch_labels = None
depends_on = None


def upgrade():
    # Historical results cannot truthfully be assigned the agent's current head.
    op.add_column("extraction_results", sa.Column("agent_revision_id", UUID(as_uuid=True), nullable=True))
    op.add_column("extraction_results", sa.Column("execution_receipt", JSONB, nullable=True))
    op.create_foreign_key("fk_extraction_results_agent_revision", "extraction_results",
                          "agent_execution_revisions", ["agent_revision_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_extraction_results_agent_revision_id", "extraction_results", ["agent_revision_id"])
    op.execute("""
        CREATE FUNCTION check_extraction_execution_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE expected jsonb;
        BEGIN
          IF TG_OP = 'UPDATE' AND OLD.agent_revision_id IS NOT NULL AND (
            OLD.agent_revision_id IS DISTINCT FROM NEW.agent_revision_id
            OR OLD.execution_receipt IS DISTINCT FROM NEW.execution_receipt
            OR OLD.agent_key IS DISTINCT FROM NEW.agent_key
          ) THEN
            RAISE EXCEPTION 'Persisted extraction execution identity cannot be changed';
          END IF;
          IF NEW.agent_revision_id IS NULL AND NEW.execution_receipt IS NULL THEN
            RETURN NEW;
          END IF;
          expected := flow_execution_receipt(NEW.agent_revision_id);
          IF expected IS NULL OR expected IS DISTINCT FROM NEW.execution_receipt
             OR expected->>'agent_key' IS DISTINCT FROM NEW.agent_key THEN
            RAISE EXCEPTION 'Extraction receipt must match its immutable producing revision';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER check_extraction_execution_receipt
          BEFORE INSERT OR UPDATE OF agent_key, agent_revision_id, execution_receipt ON extraction_results
          FOR EACH ROW EXECUTE FUNCTION check_extraction_execution_receipt();
    """)


def downgrade():
    op.execute("DROP TRIGGER check_extraction_execution_receipt ON extraction_results")
    op.execute("DROP FUNCTION check_extraction_execution_receipt()")
    op.drop_index("ix_extraction_results_agent_revision_id", table_name="extraction_results")
    op.drop_constraint("fk_extraction_results_agent_revision", "extraction_results", type_="foreignkey")
    op.drop_column("extraction_results", "execution_receipt")
    op.drop_column("extraction_results", "agent_revision_id")
