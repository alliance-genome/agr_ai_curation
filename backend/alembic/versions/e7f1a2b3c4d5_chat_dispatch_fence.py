"""Retain chat extraction dispatch identity across reconnects.

Revision ID: e7f1a2b3c4d5
Revises: d6e0f1a2b3c4
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "e7f1a2b3c4d5"
down_revision = "d6e0f1a2b3c4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "chat_review_dispatches",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String, nullable=False),
        sa.Column("document_id", sa.String, nullable=False),
        sa.Column("session_id", sa.String, nullable=False),
        sa.Column("turn_id", sa.String, nullable=False),
        sa.Column("state", sa.String, nullable=False),
        sa.Column("result", JSONB(none_as_null=True)),
        sa.CheckConstraint(
            "(state = 'started' AND result IS NULL) OR (state = 'completed' AND result IS NOT NULL)"
        ),
    )
    op.execute("""
        CREATE FUNCTION protect_chat_review_dispatch() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Chat review dispatches are retained';
            END IF;
            IF OLD.state <> 'started' OR NEW.state <> 'completed'
               OR (to_jsonb(OLD) - 'state' - 'result') <> (to_jsonb(NEW) - 'state' - 'result') THEN
                RAISE EXCEPTION 'Only the first dispatch completion may be recorded';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER protect_chat_review_dispatch
            BEFORE UPDATE OR DELETE ON chat_review_dispatches
            FOR EACH ROW EXECUTE FUNCTION protect_chat_review_dispatch();
    """)


def downgrade():
    raise RuntimeError(
        "Dispatch evidence is retained; use a forward recovery migration"
    )
