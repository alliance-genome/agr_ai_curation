"""Use the existing document retry pool for explicit split successors.

Revision ID: c1d5e6f7a8b9
Revises: b0c4d5e6f7a8

Parent limits and all consumed charges remain unchanged. Per-target ancestry
continues to enforce the frozen attempt ceiling. Drain workers at cutover.
"""
from alembic import op

revision = "c1d5e6f7a8b9"
down_revision = "b0c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE validation_review_episodes DISABLE TRIGGER protect_validation_review_episodes;
        UPDATE validation_review_episodes
        SET allowances = allowances || jsonb_build_object(
            'extra_attempts', (policy->>'extra_per_run')::int,
            'reconsideration_tokens', (policy->>'extra_per_run')::bigint * (policy->>'tokens_per_extra')::bigint)
        WHERE (allowances->>'extra_attempts')::int > 0;
        ALTER TABLE validation_review_episodes ENABLE TRIGGER protect_validation_review_episodes;
    """)


def downgrade():
    raise RuntimeError("Review history is retained; use a forward recovery migration")
