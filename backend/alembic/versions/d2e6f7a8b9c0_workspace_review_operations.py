"""Durable explicit curator validation rounds.

Revision ID: d2e6f7a8b9c0
Revises: c1d5e6f7a8b9
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'd2e6f7a8b9c0'
down_revision = 'c1d5e6f7a8b9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('workspace_review_operations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('owner_id', sa.String(), nullable=False),
        sa.Column('session_id', sa.String(), nullable=False),
        sa.Column('request_id', sa.String(36), nullable=False),
        sa.Column('request', postgresql.JSONB(), nullable=False),
        sa.Column('artifacts', postgresql.JSONB(), nullable=False),
        sa.Column('state', sa.String(), nullable=False),
        sa.Column('outcome', postgresql.JSONB(none_as_null=True)),
        sa.Column('lease_token', sa.String(36)),
        sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('owner_id', 'request_id', name='uq_workspace_review_request'))
    op.create_index('ix_workspace_review_state', 'workspace_review_operations', ['state', 'created_at'])
    op.execute("""
        CREATE FUNCTION protect_workspace_review_operation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Curator validation rounds must be retained';
            END IF;
            IF (NEW.id, NEW.owner_id, NEW.session_id, NEW.request_id, NEW.request, NEW.created_at)
                IS DISTINCT FROM
               (OLD.id, OLD.owner_id, OLD.session_id, OLD.request_id, OLD.request, OLD.created_at) THEN
                RAISE EXCEPTION 'Curator validation request identity is immutable';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER protect_workspace_review_operation BEFORE UPDATE OR DELETE
            ON workspace_review_operations FOR EACH ROW EXECUTE FUNCTION protect_workspace_review_operation();
    """)


def downgrade():
    raise RuntimeError('Curator round history is retained; use a forward migration')
