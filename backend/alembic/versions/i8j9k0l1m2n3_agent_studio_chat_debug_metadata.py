"""Add Agent Studio chat debug metadata query surface.

Revision ID: i8j9k0l1m2n3
Revises: h7i8j9k0l1m2
Create Date: 2026-05-07 12:55:00.000000
"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "i8j9k0l1m2n3"
down_revision: Union[str, Sequence[str], None] = "h7i8j9k0l1m2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


AGENT_STUDIO_CHAT_KIND = "agent_studio"


def upgrade() -> None:
    """Upgrade schema."""

    op.create_index(
        "ix_chat_messages_agent_studio_trace_id",
        "chat_messages",
        ["trace_id"],
        unique=False,
        postgresql_where=sa.text(
            f"chat_kind = '{AGENT_STUDIO_CHAT_KIND}' AND trace_id IS NOT NULL"
        ),
    )
    op.create_index(
        "ix_chat_messages_agent_studio_payload_json",
        "chat_messages",
        ["payload_json"],
        unique=False,
        postgresql_using="gin",
        postgresql_where=sa.text(
            f"chat_kind = '{AGENT_STUDIO_CHAT_KIND}' AND payload_json IS NOT NULL"
        ),
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE VIEW agent_studio_chat_debug_turns AS
            SELECT
                user_message.session_id,
                user_message.turn_id,
                user_message.message_id AS user_message_id,
                assistant_message.message_id AS assistant_message_id,
                user_message.created_at AS user_created_at,
                assistant_message.created_at AS assistant_created_at,
                user_message.trace_id AS user_trace_id,
                assistant_message.trace_id AS assistant_trace_id,
                COALESCE(assistant_message.trace_id, user_message.trace_id) AS trace_id,
                user_message.payload_json -> 'debug_context' AS debug_context,
                user_message.payload_json -> 'agent_workshop_prompt_context'
                    AS agent_workshop_prompt_context,
                COALESCE(
                    assistant_message.payload_json -> 'trace_capture',
                    user_message.payload_json -> 'trace_capture'
                ) AS trace_capture,
                CASE
                    WHEN jsonb_typeof(assistant_message.payload_json -> 'tool_calls') = 'array'
                    THEN jsonb_array_length(assistant_message.payload_json -> 'tool_calls')
                    ELSE 0
                END AS tool_call_count,
                assistant_message.payload_json -> 'tool_calls' AS tool_calls
            FROM chat_messages AS user_message
            LEFT JOIN chat_messages AS assistant_message
                ON assistant_message.session_id = user_message.session_id
                AND assistant_message.chat_kind = user_message.chat_kind
                AND assistant_message.turn_id = user_message.turn_id
                AND assistant_message.role = 'assistant'
            WHERE user_message.chat_kind = 'agent_studio'
                AND user_message.role = 'user'
            """
        )
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.execute(sa.text("DROP VIEW IF EXISTS agent_studio_chat_debug_turns"))
    op.drop_index(
        "ix_chat_messages_agent_studio_payload_json",
        table_name="chat_messages",
    )
    op.drop_index(
        "ix_chat_messages_agent_studio_trace_id",
        table_name="chat_messages",
    )
