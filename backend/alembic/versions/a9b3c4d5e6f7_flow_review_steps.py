"""Retain flow extraction submission and mandatory review boundaries.

Revision ID: a9b3c4d5e6f7
Revises: f8a2b3c4d5e6
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "a9b3c4d5e6f7"
down_revision = "f8a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("flow_review_steps",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String, nullable=False),
        sa.Column("document_id", sa.String, nullable=False),
        sa.Column("session_id", sa.String, nullable=False),
        sa.Column("run_id", sa.String, nullable=False),
        sa.Column("node_id", sa.String, nullable=False),
        sa.Column("contract", JSONB, nullable=False),
        sa.Column("state", sa.String, nullable=False),
        sa.Column("artifact_id", sa.String(36), sa.ForeignKey("validation_artifacts.id")),
        sa.Column("episode_id", sa.String(36), sa.ForeignKey("validation_review_episodes.id")),
        sa.Column("outcome", JSONB(none_as_null=True)),
        sa.CheckConstraint("state IN ('started', 'review_pending', 'completed', 'failed', 'interrupted')"),
        sa.CheckConstraint("(artifact_id IS NULL) = (episode_id IS NULL)"),
        sa.CheckConstraint("state NOT IN ('review_pending', 'completed') OR artifact_id IS NOT NULL"),
    )
    op.execute("""
        CREATE FUNCTION protect_flow_review_step() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Flow review steps are retained';
            END IF;
            IF (to_jsonb(OLD) - 'state' - 'artifact_id' - 'episode_id' - 'outcome') <>
               (to_jsonb(NEW) - 'state' - 'artifact_id' - 'episode_id' - 'outcome') THEN
                RAISE EXCEPTION 'Flow review step identity is immutable';
            END IF;
            IF OLD.artifact_id IS NOT NULL AND
               (OLD.artifact_id, OLD.episode_id) IS DISTINCT FROM (NEW.artifact_id, NEW.episode_id) THEN
                RAISE EXCEPTION 'Flow extraction cannot be replaced';
            END IF;
            IF NOT ((OLD.state = 'started' AND NEW.state IN ('review_pending', 'failed', 'interrupted'))
                 OR (OLD.state = 'review_pending' AND NEW.state IN ('completed', 'failed', 'interrupted'))) THEN
                RAISE EXCEPTION 'Flow review steps cannot be replayed';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER protect_flow_review_step BEFORE UPDATE OR DELETE ON flow_review_steps
            FOR EACH ROW EXECUTE FUNCTION protect_flow_review_step();
    """)


def downgrade():
    raise RuntimeError("Flow review evidence is retained; use a forward recovery migration")
