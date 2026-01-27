"""Add feedback_reports table

Revision ID: f8e9a2b3c4d5
Revises: 70d71ff39f77
Create Date: 2025-10-21 13:20:00.000000

"""
from typing import Sequence, Union
from datetime import datetime

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f8e9a2b3c4d5'
down_revision: Union[str, Sequence[str], None] = '70d71ff39f77'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - add feedback_reports table."""
    op.create_table(
        'feedback_reports',
        # Primary key
        sa.Column('id', sa.String(length=36), nullable=False),

        # Core feedback data (captured immediately)
        sa.Column('session_id', sa.String(length=255), nullable=False),
        sa.Column('curator_id', sa.String(length=255), nullable=False),
        sa.Column('feedback_text', sa.Text(), nullable=False),
        sa.Column('trace_ids', postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),

        # Processing status
        sa.Column('processing_status',
                 sa.Enum('pending', 'processing', 'completed', 'failed',
                        name='processingstatus'),
                 nullable=False),

        # Extracted data (populated by background task)
        sa.Column('trace_data', postgresql.JSON(astext_type=sa.Text()), nullable=True),

        # Error tracking
        sa.Column('error_details', sa.Text(), nullable=True),

        # Timing metadata
        sa.Column('email_sent_at', sa.DateTime(), nullable=True),
        sa.Column('processing_started_at', sa.DateTime(), nullable=True),
        sa.Column('processing_completed_at', sa.DateTime(), nullable=True),

        # Constraints
        sa.PrimaryKeyConstraint('id', name='pk_feedback_reports'),
    )

    # Create indexes for efficient querying
    op.create_index(
        'ix_feedback_reports_session_id',
        'feedback_reports',
        ['session_id'],
        unique=False
    )
    op.create_index(
        'ix_feedback_reports_created_at',
        'feedback_reports',
        ['created_at'],
        unique=False
    )
    op.create_index(
        'ix_feedback_reports_processing_status',
        'feedback_reports',
        ['processing_status'],
        unique=False
    )


def downgrade() -> None:
    """Downgrade schema - remove feedback_reports table."""
    op.drop_index('ix_feedback_reports_processing_status', table_name='feedback_reports')
    op.drop_index('ix_feedback_reports_created_at', table_name='feedback_reports')
    op.drop_index('ix_feedback_reports_session_id', table_name='feedback_reports')
    op.drop_table('feedback_reports')

    # Drop the enum type
    op.execute("DROP TYPE IF EXISTS processingstatus")
