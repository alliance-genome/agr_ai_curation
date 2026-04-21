"""Add generated chat-title metadata to durable sessions.

Revision ID: y8z9a0b1c2d3
Revises: s1t2u3v4w5x6
Create Date: 2026-04-21 13:10:00.000000
"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "y8z9a0b1c2d3"
down_revision: Union[str, Sequence[str], None] = "s1t2u3v4w5x6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.add_column(
        "chat_sessions",
        sa.Column("generated_title", sa.String(length=255), nullable=True),
    )
    op.create_check_constraint(
        "ck_chat_sessions_generated_title_not_empty",
        "chat_sessions",
        "generated_title IS NULL OR btrim(generated_title) <> ''",
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
    op.execute("DROP TRIGGER IF EXISTS trg_chat_sessions_refresh_rollup ON chat_sessions;")
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
            CREATE TRIGGER trg_chat_sessions_refresh_rollup
            AFTER INSERT OR UPDATE OF title
            ON chat_sessions
            FOR EACH ROW
            EXECUTE FUNCTION refresh_chat_session_search_trigger();
            """
        )
    )
    op.drop_constraint(
        "ck_chat_sessions_generated_title_not_empty",
        "chat_sessions",
        type_="check",
    )
    op.drop_column("chat_sessions", "generated_title")
