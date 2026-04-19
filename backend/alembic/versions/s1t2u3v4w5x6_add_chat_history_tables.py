"""Add durable chat history tables.

Revision ID: s1t2u3v4w5x6
Revises: r8s9t0u1v2w3
Create Date: 2026-04-19 14:35:00.000000
"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "s1t2u3v4w5x6"
down_revision: Union[str, Sequence[str], None] = "r8s9t0u1v2w3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())
TSVECTOR = postgresql.TSVECTOR()
EMPTY_TSVECTOR = sa.text("to_tsvector('english', '')")


def upgrade() -> None:
    """Upgrade schema."""

    op.create_table(
        "chat_sessions",
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("user_auth_sub", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column(
            "active_document_id",
            UUID,
            sa.ForeignKey("pdf_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "search_vector",
            TSVECTOR,
            nullable=False,
            server_default=EMPTY_TSVECTOR,
        ),
        sa.CheckConstraint(
            "btrim(session_id) <> ''",
            name="ck_chat_sessions_session_id_not_empty",
        ),
        sa.CheckConstraint(
            "btrim(user_auth_sub) <> ''",
            name="ck_chat_sessions_user_auth_sub_not_empty",
        ),
        sa.CheckConstraint(
            "title IS NULL OR btrim(title) <> ''",
            name="ck_chat_sessions_title_not_empty",
        ),
        sa.PrimaryKeyConstraint("session_id"),
    )

    op.create_index(
        "ix_chat_sessions_user_auth_sub",
        "chat_sessions",
        ["user_auth_sub"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_chat_sessions_active_document_id",
        "chat_sessions",
        ["active_document_id"],
        unique=False,
        postgresql_where=sa.text("active_document_id IS NOT NULL"),
    )
    op.create_index(
        "ix_chat_sessions_search_vector",
        "chat_sessions",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.execute(
        "CREATE INDEX ix_chat_sessions_recent_activity "
        "ON chat_sessions (user_auth_sub, (COALESCE(last_message_at, created_at)) DESC, session_id DESC) "
        "WHERE deleted_at IS NULL"
    )

    op.create_table(
        "chat_messages",
        sa.Column(
            "message_id",
            UUID,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            sa.String(length=255),
            sa.ForeignKey("chat_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("turn_id", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column(
            "message_type",
            sa.String(length=50),
            nullable=False,
            server_default="text",
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("payload_json", JSONB, nullable=True),
        sa.Column("trace_id", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "search_vector",
            TSVECTOR,
            nullable=False,
            server_default=EMPTY_TSVECTOR,
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'flow')",
            name="ck_chat_messages_role",
        ),
        sa.CheckConstraint(
            "btrim(session_id) <> ''",
            name="ck_chat_messages_session_id_not_empty",
        ),
        sa.CheckConstraint(
            "turn_id IS NULL OR btrim(turn_id) <> ''",
            name="ck_chat_messages_turn_id_not_empty",
        ),
        sa.CheckConstraint(
            "btrim(message_type) <> ''",
            name="ck_chat_messages_message_type_not_empty",
        ),
        sa.CheckConstraint(
            "btrim(content) <> ''",
            name="ck_chat_messages_content_not_empty",
        ),
        sa.PrimaryKeyConstraint("message_id"),
    )

    op.create_index(
        "ix_chat_messages_session_timeline",
        "chat_messages",
        ["session_id", "created_at", "message_id"],
        unique=False,
    )
    op.create_index(
        "ix_chat_messages_turn_lookup",
        "chat_messages",
        ["session_id", "turn_id"],
        unique=False,
        postgresql_where=sa.text("turn_id IS NOT NULL"),
    )
    op.create_index(
        "uq_chat_messages_user_turn",
        "chat_messages",
        ["session_id", "turn_id"],
        unique=True,
        postgresql_where=sa.text("turn_id IS NOT NULL AND role = 'user'"),
    )
    op.create_index(
        "uq_chat_messages_assistant_turn",
        "chat_messages",
        ["session_id", "turn_id"],
        unique=True,
        postgresql_where=sa.text("turn_id IS NOT NULL AND role = 'assistant'"),
    )
    op.create_index(
        "ix_chat_messages_search_vector",
        "chat_messages",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )

    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION strip_chat_search_content(raw_content text)
            RETURNS text
            LANGUAGE sql
            IMMUTABLE
            AS $$
                SELECT trim(
                    regexp_replace(
                        regexp_replace(
                            coalesce(raw_content, ''),
                            '(?s)Hidden flow context \\(internal grounding data; not user-visible output\\):\\s*<FLOW_INTERNAL_CONTEXT_JSON>.*?</FLOW_INTERNAL_CONTEXT_JSON>',
                            '',
                            'g'
                        ),
                        '(?s)<FLOW_INTERNAL_CONTEXT_JSON>.*?</FLOW_INTERNAL_CONTEXT_JSON>',
                        '',
                        'g'
                    )
                )
            $$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION set_chat_message_search_vector()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                NEW.search_vector := setweight(
                    to_tsvector('english', strip_chat_search_content(NEW.content)),
                    'B'
                );
                RETURN NEW;
            END;
            $$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION refresh_chat_session_rollup(target_session_id text)
            RETURNS void
            LANGUAGE plpgsql
            AS $$
            DECLARE
                aggregated_content text;
                newest_message_at timestamptz;
            BEGIN
                SELECT
                    string_agg(
                        strip_chat_search_content(content),
                        ' '
                        ORDER BY created_at, message_id
                    ),
                    max(created_at)
                INTO aggregated_content, newest_message_at
                FROM chat_messages
                WHERE session_id = target_session_id;

                UPDATE chat_sessions
                SET
                    last_message_at = newest_message_at,
                    updated_at = now(),
                    search_vector = setweight(
                        to_tsvector('english', coalesce(title, '')),
                        'A'
                    ) || setweight(
                        to_tsvector('english', coalesce(aggregated_content, '')),
                        'B'
                    )
                WHERE session_id = target_session_id;
            END;
            $$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION refresh_chat_session_search_trigger()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                PERFORM refresh_chat_session_rollup(NEW.session_id);
                RETURN NEW;
            END;
            $$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION refresh_chat_session_from_message_trigger()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF TG_OP = 'UPDATE' AND OLD.session_id IS DISTINCT FROM NEW.session_id THEN
                    PERFORM refresh_chat_session_rollup(OLD.session_id);
                END IF;

                PERFORM refresh_chat_session_rollup(COALESCE(NEW.session_id, OLD.session_id));
                RETURN NULL;
            END;
            $$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_chat_messages_search_vector
            BEFORE INSERT OR UPDATE OF content
            ON chat_messages
            FOR EACH ROW
            EXECUTE FUNCTION set_chat_message_search_vector();
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_chat_sessions_refresh_rollup
            AFTER INSERT OR UPDATE OF title
            ON chat_sessions
            FOR EACH ROW
            EXECUTE FUNCTION refresh_chat_session_search_trigger();
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_chat_messages_refresh_session
            AFTER INSERT OR UPDATE OR DELETE
            ON chat_messages
            FOR EACH ROW
            EXECUTE FUNCTION refresh_chat_session_from_message_trigger();
            """
        )
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.execute("DROP TRIGGER IF EXISTS trg_chat_messages_refresh_session ON chat_messages;")
    op.execute("DROP TRIGGER IF EXISTS trg_chat_sessions_refresh_rollup ON chat_sessions;")
    op.execute("DROP TRIGGER IF EXISTS trg_chat_messages_search_vector ON chat_messages;")

    op.execute("DROP FUNCTION IF EXISTS refresh_chat_session_from_message_trigger();")
    op.execute("DROP FUNCTION IF EXISTS refresh_chat_session_search_trigger();")
    op.execute("DROP FUNCTION IF EXISTS refresh_chat_session_rollup(text);")
    op.execute("DROP FUNCTION IF EXISTS set_chat_message_search_vector();")
    op.execute("DROP FUNCTION IF EXISTS strip_chat_search_content(text);")

    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
