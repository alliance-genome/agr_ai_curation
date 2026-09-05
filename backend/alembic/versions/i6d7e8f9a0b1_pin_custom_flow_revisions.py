"""Pin legacy custom nodes once and maintain their normalized revision references.

Revision ID: i6d7e8f9a0b1
Revises: h5c6d7e8f9a0
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "i6d7e8f9a0b1"
down_revision = "h5c6d7e8f9a0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("curation_flow_agent_revisions",
        sa.Column("flow_id", UUID(as_uuid=True), sa.ForeignKey("curation_flows.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("node_id", sa.String(50), primary_key=True),
        sa.Column("agent_revision_id", UUID(as_uuid=True), sa.ForeignKey("agent_execution_revisions.id", ondelete="RESTRICT"), nullable=False),
    )
    op.create_index("ix_curation_flow_agent_revisions_agent_revision_id",
                    "curation_flow_agent_revisions", ["agent_revision_id"])
    op.execute("""
        CREATE FUNCTION flow_execution_receipt(revision_id uuid) RETURNS jsonb
        LANGUAGE sql STABLE AS $$
          SELECT jsonb_build_object(
            'agent_id', a.id, 'agent_key', a.agent_key,
            'agent_revision_id', r.id, 'revision', r.revision,
            'fingerprint', r.fingerprint, 'output_contract', r.snapshot->'output_contract'
          ) FROM agent_execution_revisions r JOIN agents a ON a.id = r.agent_id
          WHERE r.id = revision_id AND starts_with(a.agent_key, 'ca_')
        $$;
        CREATE FUNCTION sync_curation_flow_agent_revisions() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE node jsonb; pin uuid; expected jsonb;
        BEGIN
          DELETE FROM curation_flow_agent_revisions WHERE flow_id = NEW.id;
          FOR node IN SELECT jsonb_array_elements(COALESCE(NEW.flow_definition->'nodes', '[]'::jsonb)) LOOP
            pin := (node #>> '{data,agent_revision_id}')::uuid;
            IF NOT starts_with(COALESCE(node #>> '{data,agent_id}', ''), 'ca_') THEN
              IF pin IS NOT NULL OR node #>> '{data,execution_receipt}' IS NOT NULL THEN
                RAISE EXCEPTION 'System flow nodes cannot carry custom execution pins';
              END IF;
              CONTINUE;
            END IF;
            IF pin IS NULL THEN
              IF node #>> '{data,execution_receipt}' IS NOT NULL THEN
                RAISE EXCEPTION 'A flow receipt requires its executable revision';
              END IF;
              -- Unresolved legacy nodes remain readable, but the runtime rejects
              -- missing pins. No head lookup or invented output state here.
              CONTINUE;
            END IF;
            expected := flow_execution_receipt(pin);
            IF expected IS NULL OR expected->>'agent_key' IS DISTINCT FROM node #>> '{data,agent_id}'
               OR expected IS DISTINCT FROM node #> '{data,execution_receipt}' THEN
              RAISE EXCEPTION 'Flow node execution receipt does not match its immutable revision';
            END IF;
            INSERT INTO curation_flow_agent_revisions(flow_id, node_id, agent_revision_id)
              VALUES (NEW.id, node->>'id', pin);
          END LOOP;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER sync_curation_flow_agent_revisions
          AFTER INSERT OR UPDATE OF flow_definition ON curation_flows
          FOR EACH ROW EXECUTE FUNCTION sync_curation_flow_agent_revisions();

        CREATE FUNCTION check_curation_flow_agent_references() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE target uuid; definition jsonb;
        BEGIN
          IF TG_OP = 'UPDATE' AND OLD.flow_id IS DISTINCT FROM NEW.flow_id THEN
            RAISE EXCEPTION 'Generated flow references cannot move between flows';
          END IF;
          target := CASE WHEN TG_OP = 'DELETE' THEN OLD.flow_id ELSE NEW.flow_id END;
          SELECT flow_definition INTO definition FROM curation_flows WHERE id = target;
          IF NOT FOUND THEN RETURN NULL; END IF;
          IF EXISTS (
            SELECT 1 FROM jsonb_array_elements(definition->'nodes') node
            WHERE node #>> '{data,agent_revision_id}' IS NOT NULL AND NOT EXISTS (
              SELECT 1 FROM curation_flow_agent_revisions ref
              WHERE ref.flow_id = target AND ref.node_id = node->>'id'
                AND ref.agent_revision_id::text = node #>> '{data,agent_revision_id}'
            )
          ) OR EXISTS (
            SELECT 1 FROM curation_flow_agent_revisions ref WHERE ref.flow_id = target
            AND NOT EXISTS (
              SELECT 1 FROM jsonb_array_elements(definition->'nodes') node
              WHERE node->>'id' = ref.node_id
                AND node #>> '{data,agent_revision_id}' = ref.agent_revision_id::text
            )
          ) THEN RAISE EXCEPTION 'Flow node requires its normalized revision reference'; END IF;
          RETURN NULL;
        END;
        $$;
        CREATE CONSTRAINT TRIGGER curation_flow_agent_references_complete
          AFTER INSERT OR UPDATE OR DELETE ON curation_flow_agent_revisions
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
          EXECUTE FUNCTION check_curation_flow_agent_references();
    """)
    # One-time baseline only: use ALL-1030's complete current executable revision,
    # never prompt_version and never manufacture a historical profile. Existing
    # pins are left unchanged. This runs inside the Alembic transaction.
    op.execute("""
        UPDATE curation_flows f SET flow_definition = jsonb_set(f.flow_definition, '{nodes}', (
          SELECT COALESCE(jsonb_agg(
            CASE WHEN node #>> '{data,agent_revision_id}' IS NULL
                  AND node #>> '{data,execution_receipt}' IS NULL AND r.id IS NOT NULL
              THEN jsonb_set(node, '{data}', (node->'data') || jsonb_build_object(
                'agent_revision_id', r.id, 'execution_receipt', flow_execution_receipt(r.id)))
              ELSE node END ORDER BY ordinal
          ), '[]'::jsonb)
          FROM jsonb_array_elements(f.flow_definition->'nodes') WITH ORDINALITY AS n(node, ordinal)
          LEFT JOIN agents a ON a.agent_key = node #>> '{data,agent_id}' AND starts_with(a.agent_key, 'ca_')
          LEFT JOIN agent_execution_revisions r ON r.id = a.execution_revision_id AND r.agent_id = a.id
        )) WHERE jsonb_typeof(f.flow_definition->'nodes') = 'array';
    """)


def downgrade():
    op.execute("DROP TRIGGER sync_curation_flow_agent_revisions ON curation_flows")
    op.execute("DROP TRIGGER curation_flow_agent_references_complete ON curation_flow_agent_revisions")
    op.execute("DROP FUNCTION check_curation_flow_agent_references()")
    op.execute("DROP FUNCTION sync_curation_flow_agent_revisions()")
    op.execute("DROP FUNCTION flow_execution_receipt(uuid)")
    op.drop_table("curation_flow_agent_revisions")
