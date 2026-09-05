"""Allow explicit packaged builder identity without a model response schema.

Revision ID: m0b1c2d3e4f5
Revises: l9a0b1c2d3e4

Existing immutable snapshots and receipt fingerprints are not rewritten.
"""
from alembic import op

revision = "m0b1c2d3e4f5"
down_revision = "l9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade():
    # Fresh g4 bootstraps the corrected shape before i6 pins flows; databases
    # that applied the earlier g4 still need this forward constraint upgrade.
    op.execute("ALTER TABLE agent_execution_revisions DROP CONSTRAINT IF EXISTS ck_agent_execution_domain_builder")
    op.drop_constraint("ck_agent_execution_output_contract", "agent_execution_revisions", type_="check")
    op.create_check_constraint("ck_agent_execution_output_contract", "agent_execution_revisions",
        "(output_state = 'none' AND output_mode IS NULL AND output_schema_key IS NULL AND profile_revision_id IS NULL AND profile_fingerprint IS NULL) OR "
        "(output_state = 'structured_extraction' AND output_mode IS NOT NULL AND ("
        "(output_mode = 'domain' AND (output_schema_key IS NULL OR length(trim(output_schema_key)) > 0) AND profile_revision_id IS NULL AND profile_fingerprint IS NULL) OR "
        "(output_mode = 'profile_bound_generic' AND output_schema_key IS NULL AND profile_revision_id IS NOT NULL AND profile_fingerprint IS NOT NULL) OR "
        "(output_mode = 'unprofiled_generic' AND output_schema_key IS NULL AND profile_revision_id IS NULL AND profile_fingerprint IS NULL)))")
    op.create_check_constraint("ck_agent_execution_domain_builder", "agent_execution_revisions",
        "(CASE WHEN COALESCE(snapshot #> '{output_contract,domain_extraction_ref}', 'null'::jsonb) <> 'null'::jsonb THEN "
        "output_state = 'structured_extraction' AND output_mode = 'domain' AND output_schema_key IS NULL AND "
        "jsonb_typeof(snapshot #> '{output_contract,domain_extraction_ref}') = 'object' AND "
        "((snapshot #> '{output_contract,domain_extraction_ref}') - ARRAY['package_id','agent_id','domain_pack_id']) = '{}'::jsonb AND "
        "jsonb_typeof(snapshot #> '{output_contract,domain_extraction_ref,package_id}') = 'string' AND "
        "jsonb_typeof(snapshot #> '{output_contract,domain_extraction_ref,agent_id}') = 'string' AND "
        "jsonb_typeof(snapshot #> '{output_contract,domain_extraction_ref,domain_pack_id}') = 'string' AND "
        "(snapshot #>> '{output_contract,domain_extraction_ref,package_id}') ~ '^[^[:space:]]+$' AND "
        "(snapshot #>> '{output_contract,domain_extraction_ref,agent_id}') ~ '^[^[:space:]]+$' AND "
        "(snapshot #>> '{output_contract,domain_extraction_ref,domain_pack_id}') ~ '^[^[:space:]]+$' AND "
        "(snapshot #>> '{curation,domain_pack_id}') = (snapshot #>> '{output_contract,domain_extraction_ref,domain_pack_id}') "
        "ELSE NOT (output_mode IS NOT DISTINCT FROM 'domain' AND output_schema_key IS NULL) END) IS TRUE")


def downgrade():
    # Fail before changing constraints when the older runtime cannot represent
    # a saved builder revision. Never delete or reinterpret immutable history.
    op.execute("""
      DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM agent_execution_revisions
                   WHERE output_mode = 'domain' AND output_schema_key IS NULL) THEN
          RAISE EXCEPTION 'Cannot downgrade with saved packaged builder revisions';
        END IF;
      END $$;
    """)
    op.drop_constraint("ck_agent_execution_domain_builder", "agent_execution_revisions", type_="check")
    op.drop_constraint("ck_agent_execution_output_contract", "agent_execution_revisions", type_="check")
    op.create_check_constraint("ck_agent_execution_output_contract", "agent_execution_revisions",
        "(output_state = 'none' AND output_mode IS NULL AND output_schema_key IS NULL AND profile_revision_id IS NULL AND profile_fingerprint IS NULL) OR "
        "(output_state = 'structured_extraction' AND output_mode IS NOT NULL AND ("
        "(output_mode = 'domain' AND output_schema_key IS NOT NULL AND length(trim(output_schema_key)) > 0 AND profile_revision_id IS NULL AND profile_fingerprint IS NULL) OR "
        "(output_mode = 'profile_bound_generic' AND output_schema_key IS NULL AND profile_revision_id IS NOT NULL AND profile_fingerprint IS NOT NULL) OR "
        "(output_mode = 'unprofiled_generic' AND output_schema_key IS NULL AND profile_revision_id IS NULL AND profile_fingerprint IS NULL)))")
