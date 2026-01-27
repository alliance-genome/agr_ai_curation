"""Create curation_flows table

Revision ID: k5l6m7n8o9p0
Revises: j4k5l6m7n8o9
Create Date: 2026-01-05

This migration creates the curation_flows table for storing user-defined
curation workflows. Each flow consists of ordered agent steps with optional
customizations.

Key features:
- UUID primary key (application-generated)
- Soft reference to users table (no FK constraint for future sharing)
- JSONB storage for flow_definition (validated at API layer)
- Soft delete via is_active flag
- Partial unique index for (user_id, name) WHERE is_active = TRUE
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


# revision identifiers, used by Alembic.
revision = 'k5l6m7n8o9p0'
down_revision = 'j4k5l6m7n8o9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create curation_flows table
    op.create_table(
        'curation_flows',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False, comment='Owner user ID - references users(user_id)'),
        sa.Column('name', sa.String(length=255), nullable=False, comment='User-defined flow name'),
        sa.Column('description', sa.Text(), nullable=True, comment='Optional flow description'),
        sa.Column(
            'flow_definition',
            JSONB(),
            nullable=False,
            comment='Flow structure: nodes, edges, step configs. Validated by Pydantic at API layer.'
        ),
        sa.Column('execution_count', sa.Integer(), nullable=False, server_default='0', comment='Number of times this flow has been executed'),
        sa.Column('last_executed_at', sa.DateTime(timezone=True), nullable=True, comment='Timestamp of most recent execution'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true', comment='Soft delete flag (false = archived/deleted)'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("name <> ''", name='ck_flows_name_not_empty'),
    )

    # Index for fast user lookups
    op.create_index(
        'idx_curation_flows_user_id',
        'curation_flows',
        ['user_id']
    )

    # Partial index for active flows only (performance optimization)
    op.create_index(
        'idx_curation_flows_user_active',
        'curation_flows',
        ['user_id'],
        postgresql_where=sa.text('is_active = true')
    )

    # Partial UNIQUE index to enforce unique flow names per user (only for active flows)
    # This allows users to "delete" a flow and create a new one with the same name
    op.create_index(
        'uq_user_flow_name_active',
        'curation_flows',
        ['user_id', 'name'],
        unique=True,
        postgresql_where=sa.text('is_active = true')
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index('uq_user_flow_name_active', table_name='curation_flows')
    op.drop_index('idx_curation_flows_user_active', table_name='curation_flows')
    op.drop_index('idx_curation_flows_user_id', table_name='curation_flows')

    # Drop table
    op.drop_table('curation_flows')
