"""Create users table

Revision ID: 6e5d344c93d0
Revises: 148ad0f8d61e
Create Date: 2025-01-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6e5d344c93d0'
down_revision: Union[str, Sequence[str], None] = '148ad0f8d61e'  # Fixed: depend on current head
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - add users table."""
    op.create_table(
        'users',
        # Primary key with auto-increment
        sa.Column('user_id', sa.Integer(), sa.Identity(always=False), nullable=False),

        # User identity from Okta
        sa.Column('okta_id', sa.String(255), nullable=False),
        sa.Column('email', sa.String(255), nullable=True),
        sa.Column('display_name', sa.String(255), nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('last_login', sa.DateTime(timezone=True), nullable=True),

        # Status
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),

        # Constraints
        sa.PrimaryKeyConstraint('user_id', name='pk_users'),
        sa.UniqueConstraint('okta_id', name='uq_users_okta_id'),
        sa.CheckConstraint("okta_id <> ''", name='ck_users_okta_id_not_empty')
    )

    # Create indexes for efficient querying
    op.create_index(
        'idx_users_okta_id',
        'users',
        ['okta_id'],
        unique=False
    )

    # Conditional index for email (only non-null values)
    op.create_index(
        'idx_users_email',
        'users',
        ['email'],
        unique=False,
        postgresql_where=sa.text('email IS NOT NULL')
    )

    # Conditional index for active users
    op.create_index(
        'idx_users_active',
        'users',
        ['is_active'],
        unique=False,
        postgresql_where=sa.text('is_active = true')
    )


def downgrade() -> None:
    """Downgrade schema - remove users table."""
    op.drop_index('idx_users_active', table_name='users')
    op.drop_index('idx_users_email', table_name='users')
    op.drop_index('idx_users_okta_id', table_name='users')
    op.drop_table('users')
