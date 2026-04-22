"""Add chat_kind discriminator to durable chat history tables.

This migration uses per-kind partial GIN indexes for chat search vectors instead
of a multicolumn GIN index. Every caller now constrains ``chat_kind`` to a
single fixed value, so two smaller partial indexes match the query shape
directly without introducing multicolumn GIN operator-class complexity.

Revision ID: z9a0b1c2d3e4
Revises: b9c0d1e2f3a4
Create Date: 2026-04-22 17:00:00.000000
"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "z9a0b1c2d3e4"
down_revision: Union[str, Sequence[str], None] = "b9c0d1e2f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ASSISTANT_CHAT_KIND = "assistant_chat"
AGENT_STUDIO_CHAT_KIND = "agent_studio"
CHAT_KIND_CHECK = (
    "chat_kind IN ('assistant_chat', 'agent_studio')"
)


def upgrade() -> None:
    """Upgrade schema."""

    op.add_column(
        "chat_sessions",
        sa.Column(
            "chat_kind",
            sa.String(),
            nullable=True,
            server_default=sa.text(f"'{ASSISTANT_CHAT_KIND}'"),
        ),
    )
    op.add_column(
        "chat_messages",
        sa.Column(
            "chat_kind",
            sa.String(),
            nullable=True,
            server_default=sa.text(f"'{ASSISTANT_CHAT_KIND}'"),
        ),
    )

    op.execute(
        sa.text(
            f"""
            UPDATE chat_sessions
            SET chat_kind = '{ASSISTANT_CHAT_KIND}'
            WHERE chat_kind IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            UPDATE chat_messages
            SET chat_kind = '{ASSISTANT_CHAT_KIND}'
            WHERE chat_kind IS NULL
            """
        )
    )

    op.create_check_constraint(
        "ck_chat_sessions_chat_kind",
        "chat_sessions",
        CHAT_KIND_CHECK,
    )
    op.create_check_constraint(
        "ck_chat_messages_chat_kind",
        "chat_messages",
        CHAT_KIND_CHECK,
    )
    op.alter_column(
        "chat_sessions",
        "chat_kind",
        existing_type=sa.String(),
        nullable=False,
        server_default=None,
    )
    op.alter_column(
        "chat_messages",
        "chat_kind",
        existing_type=sa.String(),
        nullable=False,
        server_default=None,
    )

    op.drop_index("ix_chat_sessions_user_auth_sub", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_search_vector", table_name="chat_sessions")
    op.execute("DROP INDEX IF EXISTS ix_chat_sessions_recent_activity")

    op.create_index(
        "ix_chat_sessions_user_auth_sub",
        "chat_sessions",
        ["user_auth_sub", "chat_kind"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_chat_sessions_search_vector_assistant_chat",
        "chat_sessions",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
        postgresql_where=sa.text(
            f"deleted_at IS NULL AND chat_kind = '{ASSISTANT_CHAT_KIND}'"
        ),
    )
    op.create_index(
        "ix_chat_sessions_search_vector_agent_studio",
        "chat_sessions",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
        postgresql_where=sa.text(
            f"deleted_at IS NULL AND chat_kind = '{AGENT_STUDIO_CHAT_KIND}'"
        ),
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX ix_chat_sessions_recent_activity
            ON chat_sessions (
                user_auth_sub,
                chat_kind,
                (COALESCE(last_message_at, created_at)) DESC,
                session_id DESC
            )
            WHERE deleted_at IS NULL
            """
        )
    )

    op.drop_index("ix_chat_messages_session_timeline", table_name="chat_messages")
    op.drop_index("ix_chat_messages_turn_lookup", table_name="chat_messages")
    op.drop_index("uq_chat_messages_user_turn", table_name="chat_messages")
    op.drop_index("uq_chat_messages_assistant_turn", table_name="chat_messages")
    op.drop_index("ix_chat_messages_search_vector", table_name="chat_messages")

    op.create_index(
        "ix_chat_messages_session_timeline",
        "chat_messages",
        ["session_id", "chat_kind", "created_at", "message_id"],
        unique=False,
    )
    op.create_index(
        "ix_chat_messages_turn_lookup",
        "chat_messages",
        ["session_id", "chat_kind", "turn_id"],
        unique=False,
        postgresql_where=sa.text("turn_id IS NOT NULL"),
    )
    op.create_index(
        "uq_chat_messages_user_turn",
        "chat_messages",
        ["session_id", "chat_kind", "turn_id"],
        unique=True,
        postgresql_where=sa.text("turn_id IS NOT NULL AND role = 'user'"),
    )
    op.create_index(
        "uq_chat_messages_assistant_turn",
        "chat_messages",
        ["session_id", "chat_kind", "turn_id"],
        unique=True,
        postgresql_where=sa.text("turn_id IS NOT NULL AND role = 'assistant'"),
    )
    op.create_index(
        "ix_chat_messages_search_vector_assistant_chat",
        "chat_messages",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
        postgresql_where=sa.text(
            f"chat_kind = '{ASSISTANT_CHAT_KIND}'"
        ),
    )
    op.create_index(
        "ix_chat_messages_search_vector_agent_studio",
        "chat_messages",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
        postgresql_where=sa.text(
            f"chat_kind = '{AGENT_STUDIO_CHAT_KIND}'"
        ),
    )

    op.execute("DROP TRIGGER IF EXISTS trg_chat_sessions_refresh_rollup ON chat_sessions;")
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
                resolved_chat_kind text;
            BEGIN
                SELECT chat_kind
                INTO resolved_chat_kind
                FROM chat_sessions
                WHERE session_id = target_session_id;

                IF resolved_chat_kind IS NULL THEN
                    RETURN;
                END IF;

                SELECT
                    string_agg(
                        strip_chat_search_content(content),
                        ' '
                        ORDER BY created_at, message_id
                    ),
                    max(created_at)
                INTO aggregated_content, newest_message_at
                FROM chat_messages
                WHERE
                    session_id = target_session_id
                    AND chat_kind = resolved_chat_kind;

                UPDATE chat_sessions
                SET
                    last_message_at = newest_message_at,
                    updated_at = now(),
                    search_vector = setweight(
                        to_tsvector('english', coalesce(title, generated_title, '')),
                        'A'
                    ) || setweight(
                        to_tsvector('english', coalesce(aggregated_content, '')),
                        'B'
                    )
                WHERE
                    session_id = target_session_id
                    AND chat_kind = resolved_chat_kind;
            END;
            $$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_chat_sessions_refresh_rollup
            AFTER INSERT OR UPDATE OF title, generated_title, chat_kind
            ON chat_sessions
            FOR EACH ROW
            EXECUTE FUNCTION refresh_chat_session_search_trigger();
            """
        )
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.execute("DROP TRIGGER IF EXISTS trg_chat_sessions_refresh_rollup ON chat_sessions;")
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
                        to_tsvector('english', coalesce(title, generated_title, '')),
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
            CREATE TRIGGER trg_chat_sessions_refresh_rollup
            AFTER INSERT OR UPDATE OF title, generated_title
            ON chat_sessions
            FOR EACH ROW
            EXECUTE FUNCTION refresh_chat_session_search_trigger();
            """
        )
    )

    op.drop_index(
        "ix_chat_sessions_search_vector_assistant_chat",
        table_name="chat_sessions",
    )
    op.drop_index(
        "ix_chat_sessions_search_vector_agent_studio",
        table_name="chat_sessions",
    )
    op.drop_index("ix_chat_sessions_user_auth_sub", table_name="chat_sessions")
    op.execute("DROP INDEX IF EXISTS ix_chat_sessions_recent_activity")

    op.create_index(
        "ix_chat_sessions_user_auth_sub",
        "chat_sessions",
        ["user_auth_sub"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
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
        sa.text(
            """
            CREATE INDEX ix_chat_sessions_recent_activity
            ON chat_sessions (
                user_auth_sub,
                (COALESCE(last_message_at, created_at)) DESC,
                session_id DESC
            )
            WHERE deleted_at IS NULL
            """
        )
    )

    op.drop_index(
        "ix_chat_messages_search_vector_assistant_chat",
        table_name="chat_messages",
    )
    op.drop_index(
        "ix_chat_messages_search_vector_agent_studio",
        table_name="chat_messages",
    )
    op.drop_index("ix_chat_messages_session_timeline", table_name="chat_messages")
    op.drop_index("ix_chat_messages_turn_lookup", table_name="chat_messages")
    op.drop_index("uq_chat_messages_user_turn", table_name="chat_messages")
    op.drop_index("uq_chat_messages_assistant_turn", table_name="chat_messages")

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

    op.drop_constraint("ck_chat_messages_chat_kind", "chat_messages", type_="check")
    op.drop_constraint("ck_chat_sessions_chat_kind", "chat_sessions", type_="check")
    op.drop_column("chat_messages", "chat_kind")
    op.drop_column("chat_sessions", "chat_kind")
