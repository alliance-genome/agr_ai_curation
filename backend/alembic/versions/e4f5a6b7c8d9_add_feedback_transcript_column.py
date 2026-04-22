"""Add conversation_transcript column to feedback_reports.

Revision ID: e4f5a6b7c8d9
Revises: z9a0b1c2d3e4
Create Date: 2026-04-22 17:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, Sequence[str], None] = "z9a0b1c2d3e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RECREATED_FEEDBACK_REPORTS_COMMENT = (
    "agr_ai_curation:e4f5a6b7c8d9:recreated_feedback_reports"
)


def _processing_status_enum(*, create_type: bool) -> postgresql.ENUM:
    return postgresql.ENUM(
        "pending",
        "processing",
        "completed",
        "failed",
        name="processingstatus",
        create_type=create_type,
    )


def _create_feedback_reports_table() -> None:
    bind = op.get_bind()
    _processing_status_enum(create_type=True).create(bind, checkfirst=True)

    op.create_table(
        "feedback_reports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("curator_id", sa.String(length=255), nullable=False),
        sa.Column("feedback_text", sa.Text(), nullable=False),
        sa.Column("trace_ids", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column(
            "processing_status",
            _processing_status_enum(create_type=False),
            nullable=False,
        ),
        sa.Column("trace_data", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "conversation_transcript",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("error_details", sa.Text(), nullable=True),
        sa.Column("email_sent_at", sa.DateTime(), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(), nullable=True),
        sa.Column("processing_completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_feedback_reports"),
    )
    op.create_index(
        "ix_feedback_reports_session_id",
        "feedback_reports",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        "ix_feedback_reports_created_at",
        "feedback_reports",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_feedback_reports_processing_status",
        "feedback_reports",
        ["processing_status"],
        unique=False,
    )
    op.execute(
        "COMMENT ON TABLE feedback_reports IS "
        f"'{RECREATED_FEEDBACK_REPORTS_COMMENT}'"
    )


def _feedback_reports_was_recreated(inspector: sa.Inspector) -> bool:
    table_comment = inspector.get_table_comment("feedback_reports").get("text")
    return table_comment == RECREATED_FEEDBACK_REPORTS_COMMENT


def _recreate_feedback_reports_audit_triggers() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_feedback_reports_delete ON feedback_reports;")
    op.execute("DROP TRIGGER IF EXISTS audit_feedback_reports_update ON feedback_reports;")
    op.execute("DROP TRIGGER IF EXISTS audit_feedback_reports_insert ON feedback_reports;")

    op.execute("""
        CREATE TRIGGER audit_feedback_reports_insert
            AFTER INSERT ON feedback_reports
            FOR EACH ROW
            EXECUTE FUNCTION audit_trigger_func();
    """)
    op.execute("""
        CREATE TRIGGER audit_feedback_reports_update
            AFTER UPDATE ON feedback_reports
            FOR EACH ROW
            EXECUTE FUNCTION audit_trigger_func();
    """)
    op.execute("""
        CREATE TRIGGER audit_feedback_reports_delete
            AFTER DELETE ON feedback_reports
            FOR EACH ROW
            EXECUTE FUNCTION audit_trigger_func();
    """)


def upgrade() -> None:
    """Upgrade schema - add durable feedback transcript storage."""

    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "feedback_reports" not in inspector.get_table_names():
        _create_feedback_reports_table()
        _recreate_feedback_reports_audit_triggers()
        return

    existing_columns = {
        column["name"]
        for column in inspector.get_columns("feedback_reports")
    }
    if "conversation_transcript" in existing_columns:
        return

    op.add_column(
        "feedback_reports",
        sa.Column(
            "conversation_transcript",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    _recreate_feedback_reports_audit_triggers()


def downgrade() -> None:
    """Downgrade schema - remove durable feedback transcript storage."""

    inspector = sa.inspect(op.get_bind())
    if "feedback_reports" not in inspector.get_table_names():
        return

    if _feedback_reports_was_recreated(inspector):
        op.drop_table("feedback_reports")
        op.execute("DROP TYPE IF EXISTS processingstatus")
        return

    existing_columns = {
        column["name"]
        for column in inspector.get_columns("feedback_reports")
    }
    if "conversation_transcript" in existing_columns:
        op.drop_column("feedback_reports", "conversation_transcript")
