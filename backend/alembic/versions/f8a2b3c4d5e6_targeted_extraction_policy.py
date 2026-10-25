"""Explicit targeted-extraction policy for the unreleased review runtime.

Revision ID: f8a2b3c4d5e6
Revises: e7f1a2b3c4d5

No previously admitted token, time or validator allowance changes. Existing
operations have never supported targeted extraction; their new extraction
ceiling is the approved initial-plus-one default. Historical audit facts remain
unchanged. Drain review workers before this protocol cutover.
"""
from alembic import op
import sqlalchemy as sa

revision = "f8a2b3c4d5e6"
down_revision = "e7f1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("validation_review_work", sa.Column("kind", sa.String(), nullable=False, server_default="validator"))
    op.create_check_constraint("ck_review_work_kind", "validation_review_work", "kind IN ('validator', 'extraction_followup')")
    op.execute("""
        ALTER TABLE validation_review_scopes DISABLE TRIGGER protect_validation_review_scopes;
        ALTER TABLE validation_review_episodes DISABLE TRIGGER protect_validation_review_episodes;
        UPDATE validation_review_scopes
        SET contract = jsonb_set(contract, '{policy,extraction_attempts_per_entry}', '2'::jsonb)
        WHERE NOT (contract->'policy' ? 'extraction_attempts_per_entry');
        UPDATE validation_review_episodes
        SET policy = jsonb_set(policy, '{extraction_attempts_per_entry}', '2'::jsonb)
        WHERE NOT (policy ? 'extraction_attempts_per_entry');
        ALTER TABLE validation_review_scopes ENABLE TRIGGER protect_validation_review_scopes;
        ALTER TABLE validation_review_episodes ENABLE TRIGGER protect_validation_review_episodes;
    """)


def downgrade():
    raise RuntimeError("Review history is retained; use a forward recovery migration")
