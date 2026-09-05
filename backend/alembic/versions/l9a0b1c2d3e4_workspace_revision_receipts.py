"""Normalized per-candidate and multi-source session execution identity.

Revision ID: l9a0b1c2d3e4
Revises: k8f9a0b1c2d3
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "l9a0b1c2d3e4"
down_revision = "k8f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("curation_session_agent_revisions",
        sa.Column("session_id", UUID(as_uuid=True), sa.ForeignKey("curation_review_sessions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("agent_revision_id", UUID(as_uuid=True), sa.ForeignKey("agent_execution_revisions.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("execution_receipt", JSONB, nullable=False))
    op.add_column("curation_candidates", sa.Column("agent_revision_id", UUID(as_uuid=True), nullable=True))
    op.add_column("curation_candidates", sa.Column("execution_receipt", JSONB, nullable=True))
    op.create_foreign_key("fk_curation_candidates_agent_revision", "curation_candidates", "agent_execution_revisions",
                          ["agent_revision_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_curation_candidates_agent_revision_id", "curation_candidates", ["agent_revision_id"])
    op.execute("""
      CREATE FUNCTION check_session_execution_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF flow_execution_receipt(NEW.agent_revision_id) IS DISTINCT FROM NEW.execution_receipt THEN
          RAISE EXCEPTION 'Session reference must retain the exact execution receipt';
        END IF;
        IF TG_OP = 'UPDATE' AND (OLD.session_id IS DISTINCT FROM NEW.session_id
            OR OLD.agent_revision_id IS DISTINCT FROM NEW.agent_revision_id
            OR OLD.execution_receipt IS DISTINCT FROM NEW.execution_receipt) THEN
          RAISE EXCEPTION 'Session execution references cannot be retargeted';
        END IF;
        RETURN NEW;
      END;
      $$;
      CREATE TRIGGER check_session_execution_receipt BEFORE INSERT OR UPDATE ON curation_session_agent_revisions
        FOR EACH ROW EXECUTE FUNCTION check_session_execution_receipt();

      CREATE FUNCTION check_candidate_execution_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE expected jsonb; source_receipt jsonb;
      BEGIN
        IF NEW.envelope_id IS NOT NULL THEN
          SELECT execution_receipt INTO source_receipt FROM domain_envelopes WHERE envelope_id=NEW.envelope_id;
        ELSIF NEW.extraction_result_id IS NOT NULL THEN
          SELECT execution_receipt INTO source_receipt FROM extraction_results WHERE id=NEW.extraction_result_id;
        END IF;
        IF (NEW.envelope_id IS NOT NULL OR NEW.extraction_result_id IS NOT NULL)
           AND source_receipt IS DISTINCT FROM NEW.execution_receipt THEN
          RAISE EXCEPTION 'Candidate must retain its source execution receipt';
        END IF;
        IF TG_OP = 'UPDATE' AND OLD.agent_revision_id IS NOT NULL AND (
          OLD.agent_revision_id IS DISTINCT FROM NEW.agent_revision_id OR OLD.execution_receipt IS DISTINCT FROM NEW.execution_receipt
        ) THEN RAISE EXCEPTION 'Candidate execution receipt cannot be changed'; END IF;
        IF NEW.agent_revision_id IS NULL AND NEW.execution_receipt IS NULL THEN RETURN NEW; END IF;
        expected := flow_execution_receipt(NEW.agent_revision_id);
        IF expected IS NULL OR expected IS DISTINCT FROM NEW.execution_receipt THEN
          RAISE EXCEPTION 'Candidate execution receipt must match its normalized reference';
        END IF;
        INSERT INTO curation_session_agent_revisions(session_id,agent_revision_id,execution_receipt)
          VALUES (NEW.session_id,NEW.agent_revision_id,NEW.execution_receipt) ON CONFLICT DO NOTHING;
        RETURN NEW;
      END;
      $$;
      CREATE TRIGGER check_candidate_execution_receipt BEFORE INSERT OR UPDATE ON curation_candidates
        FOR EACH ROW EXECUTE FUNCTION check_candidate_execution_receipt();

      CREATE FUNCTION retain_envelope_session_execution_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NEW.session_id IS NOT NULL AND NEW.agent_revision_id IS NOT NULL THEN
          INSERT INTO curation_session_agent_revisions(session_id,agent_revision_id,execution_receipt)
            VALUES (NEW.session_id,NEW.agent_revision_id,NEW.execution_receipt) ON CONFLICT DO NOTHING;
        END IF;
        RETURN NEW;
      END;
      $$;
      CREATE TRIGGER retain_envelope_session_execution_receipt BEFORE INSERT OR UPDATE ON domain_envelopes
        FOR EACH ROW EXECUTE FUNCTION retain_envelope_session_execution_receipt();

      -- Only copy existing durable identities. Never consult an agent head.
      INSERT INTO curation_session_agent_revisions(session_id,agent_revision_id,execution_receipt)
        SELECT DISTINCT session_id,agent_revision_id,execution_receipt FROM domain_envelopes
        WHERE session_id IS NOT NULL AND agent_revision_id IS NOT NULL ON CONFLICT DO NOTHING;
      UPDATE curation_candidates c SET agent_revision_id=e.agent_revision_id, execution_receipt=e.execution_receipt
        FROM domain_envelopes e WHERE c.envelope_id=e.envelope_id AND e.agent_revision_id IS NOT NULL;
      UPDATE curation_candidates c SET agent_revision_id=r.agent_revision_id, execution_receipt=r.execution_receipt
        FROM extraction_results r WHERE c.envelope_id IS NULL AND c.extraction_result_id=r.id AND r.agent_revision_id IS NOT NULL;
    """)
    for table in ("curation_candidates", "domain_envelopes"):
        op.create_foreign_key(f"fk_{table}_session_execution", table, "curation_session_agent_revisions",
            ["session_id", "agent_revision_id"], ["session_id", "agent_revision_id"], ondelete="RESTRICT")


def downgrade():
    for table in ("curation_candidates", "domain_envelopes"):
        op.drop_constraint(f"fk_{table}_session_execution", table, type_="foreignkey")
    op.execute("DROP TRIGGER retain_envelope_session_execution_receipt ON domain_envelopes")
    op.execute("DROP FUNCTION retain_envelope_session_execution_receipt()")
    op.execute("DROP TRIGGER check_candidate_execution_receipt ON curation_candidates")
    op.execute("DROP FUNCTION check_candidate_execution_receipt()")
    op.execute("DROP TRIGGER check_session_execution_receipt ON curation_session_agent_revisions")
    op.execute("DROP FUNCTION check_session_execution_receipt()")
    op.drop_index("ix_curation_candidates_agent_revision_id", table_name="curation_candidates")
    op.drop_constraint("fk_curation_candidates_agent_revision", "curation_candidates", type_="foreignkey")
    op.drop_column("curation_candidates", "execution_receipt")
    op.drop_column("curation_candidates", "agent_revision_id")
    op.drop_table("curation_session_agent_revisions")
