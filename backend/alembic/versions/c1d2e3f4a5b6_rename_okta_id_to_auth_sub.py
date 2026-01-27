"""Rename okta_id to auth_sub for Cognito migration

Revision ID: c1d2e3f4a5b6
Revises: 3b8f92c1a5d4
Create Date: 2025-01-27 12:00:00.000000

This migration renames the okta_id column to auth_sub to reflect
the migration from Okta to AWS Cognito authentication.
The column stores the unique user identifier from the JWT 'sub' claim.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = '3b8f92c1a5d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Rename okta_id column to auth_sub in users table."""
    # Drop existing constraints and indexes that reference okta_id
    op.drop_index('idx_users_okta_id', table_name='users')
    op.drop_constraint('uq_users_okta_id', 'users', type_='unique')
    op.drop_constraint('ck_users_okta_id_not_empty', 'users', type_='check')

    # Rename the column
    op.alter_column('users', 'okta_id', new_column_name='auth_sub')

    # Recreate constraints and indexes with new names
    op.create_unique_constraint('uq_users_auth_sub', 'users', ['auth_sub'])
    op.create_check_constraint(
        'ck_users_auth_sub_not_empty',
        'users',
        sa.text("auth_sub <> ''")
    )
    op.create_index('idx_users_auth_sub', 'users', ['auth_sub'], unique=False)


def downgrade() -> None:
    """Rename auth_sub column back to okta_id in users table."""
    # Drop new constraints and indexes
    op.drop_index('idx_users_auth_sub', table_name='users')
    op.drop_constraint('uq_users_auth_sub', 'users', type_='unique')
    op.drop_constraint('ck_users_auth_sub_not_empty', 'users', type_='check')

    # Rename column back
    op.alter_column('users', 'auth_sub', new_column_name='okta_id')

    # Recreate original constraints and indexes
    op.create_unique_constraint('uq_users_okta_id', 'users', ['okta_id'])
    op.create_check_constraint(
        'ck_users_okta_id_not_empty',
        'users',
        sa.text("okta_id <> ''")
    )
    op.create_index('idx_users_okta_id', 'users', ['okta_id'], unique=False)
