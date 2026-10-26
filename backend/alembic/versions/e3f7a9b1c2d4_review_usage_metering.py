"""Retire review token ceilings while preserving original policy evidence.

Drain review workers before applying. No attempts, deadlines, charges, provider
reservations or immutable validation facts are reset. Historical coverage stays
unchanged; its token credits are no longer interpreted as quotas.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'e3f7a9b1c2d4'
down_revision = 'd2e6f7a8b9c0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'validation_review_policy_transitions',
        sa.Column('source_table', sa.String(), primary_key=True),
        sa.Column('source_id', sa.String(36), primary_key=True),
        sa.Column('migration_revision', sa.String(), nullable=False),
        sa.Column('original_record', JSONB, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute("""
        CREATE FUNCTION protect_validation_review_policy_transitions() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Original review policies must be retained';
        END $$;
        CREATE TRIGGER protect_validation_review_policy_transitions
            BEFORE UPDATE OR DELETE ON validation_review_policy_transitions
            FOR EACH ROW EXECUTE FUNCTION protect_validation_review_policy_transitions();
        LOCK TABLE validation_review_scopes, validation_review_episodes IN ACCESS EXCLUSIVE MODE;
        INSERT INTO validation_review_policy_transitions
            (source_table, source_id, migration_revision, original_record)
            SELECT 'validation_review_scopes', id, 'e3f7a9b1c2d4', to_jsonb(s)
            FROM validation_review_scopes s;
        INSERT INTO validation_review_policy_transitions
            (source_table, source_id, migration_revision, original_record)
            SELECT 'validation_review_episodes', id, 'e3f7a9b1c2d4', to_jsonb(e)
            FROM validation_review_episodes e;
        ALTER TABLE validation_review_scopes DISABLE TRIGGER protect_validation_review_scopes;
        ALTER TABLE validation_review_episodes DISABLE TRIGGER protect_validation_review_episodes;
        UPDATE validation_review_scopes SET contract = jsonb_set(
            jsonb_set(contract, '{policy}', (contract->'policy') - ARRAY['reviewer_tokens','tokens_per_target','tokens_per_extra']),
            '{allowances}', (contract->'allowances') - ARRAY['initial_tokens','reviewer_tokens','reconsideration_tokens']);
        UPDATE validation_review_episodes SET
            policy = policy - ARRAY['reviewer_tokens','tokens_per_target','tokens_per_extra'],
            allowances = allowances - ARRAY['initial_tokens','reviewer_tokens','reconsideration_tokens'];
        ALTER TABLE validation_review_scopes ENABLE TRIGGER protect_validation_review_scopes;
        ALTER TABLE validation_review_episodes ENABLE TRIGGER protect_validation_review_episodes;
    """)


def downgrade():
    raise RuntimeError('Review history is retained; use a forward recovery migration')
