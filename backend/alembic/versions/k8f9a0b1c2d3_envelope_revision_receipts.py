"""Keep envelope checkpoints tied to their producing executable revision.

Revision ID: k8f9a0b1c2d3
Revises: j7e8f9a0b1c2
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "k8f9a0b1c2d3"
down_revision = "j7e8f9a0b1c2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("domain_envelopes", sa.Column("agent_revision_id", UUID(as_uuid=True), nullable=True))
    op.add_column("domain_envelopes", sa.Column("execution_receipt", JSONB, nullable=True))
    op.create_foreign_key("fk_domain_envelopes_agent_revision", "domain_envelopes", "agent_execution_revisions",
                          ["agent_revision_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_domain_envelopes_agent_revision_id", "domain_envelopes", ["agent_revision_id"])
    op.execute("""
        CREATE FUNCTION check_envelope_execution_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE expected jsonb;
        BEGIN
          IF TG_OP = 'UPDATE' AND OLD.agent_revision_id IS NOT NULL AND (
            OLD.agent_revision_id IS DISTINCT FROM NEW.agent_revision_id
            OR OLD.execution_receipt IS DISTINCT FROM NEW.execution_receipt
          ) THEN RAISE EXCEPTION 'Envelope execution identity cannot be changed'; END IF;
          IF NEW.agent_revision_id IS NULL AND NEW.execution_receipt IS NULL THEN
            IF NEW.envelope_json #>> '{metadata,execution_receipt}' IS NOT NULL THEN
              RAISE EXCEPTION 'Envelope receipt requires a normalized revision reference';
            END IF;
            RETURN NEW;
          END IF;
          expected := flow_execution_receipt(NEW.agent_revision_id);
          IF expected IS NULL OR expected IS DISTINCT FROM NEW.execution_receipt
            OR expected IS DISTINCT FROM NEW.envelope_json #> '{metadata,execution_receipt}' THEN
            RAISE EXCEPTION 'Envelope receipt must match its immutable producing revision';
          END IF;
          IF EXISTS (SELECT 1 FROM extraction_results r WHERE r.id::text = NEW.source_extraction_result_id
                     AND r.execution_receipt IS DISTINCT FROM expected) THEN
            RAISE EXCEPTION 'Envelope receipt must match its source extraction result';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER check_envelope_execution_receipt BEFORE INSERT OR UPDATE ON domain_envelopes
          FOR EACH ROW EXECUTE FUNCTION check_envelope_execution_receipt();
    """)


def downgrade():
    op.execute("DROP TRIGGER check_envelope_execution_receipt ON domain_envelopes")
    op.execute("DROP FUNCTION check_envelope_execution_receipt()")
    op.drop_index("ix_domain_envelopes_agent_revision_id", table_name="domain_envelopes")
    op.drop_constraint("fk_domain_envelopes_agent_revision", "domain_envelopes", type_="foreignkey")
    op.drop_column("domain_envelopes", "execution_receipt")
    op.drop_column("domain_envelopes", "agent_revision_id")
