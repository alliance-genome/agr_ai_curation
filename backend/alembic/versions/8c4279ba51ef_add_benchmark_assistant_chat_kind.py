"""Separate benchmark assistance from curation and Workshop conversations.

Existing rows and indexes are retained. The existing owner/kind/timeline and
turn-id uniqueness indexes also cover the new kind; no duplicate store needed.
"""

from alembic import op

revision = "8c4279ba51ef"
down_revision = "7b3168a940de"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("chat_sessions", "chat_messages"):
        name = f"ck_{table}_chat_kind"
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(
            name, table,
            "chat_kind IN ('assistant_chat', 'agent_studio', 'benchmark_assistant')",
        )


def downgrade() -> None:
    # PostgreSQL refuses this transaction if benchmark conversations exist.
    # Never delete or reclassify retained conversations to make rollback pass.
    for table in ("chat_sessions", "chat_messages"):
        name = f"ck_{table}_chat_kind"
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, "chat_kind IN ('assistant_chat', 'agent_studio')")
