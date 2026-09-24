"""Stop inheriting the extraction-time term resolver helpers (ALL-1276).

Extraction agents no longer search databases for identities, and no validator
uses these helpers, so a custom agent never inherits them from a template.
Only the inheritance designation changes; attach/execute policy and any other
operator config stay as they are.

Revision ID: s6t7u8v9w0x1
Revises: r5a6b7c8d9e0
"""
from alembic import op  # pyright: ignore[reportAttributeAccessIssue]
import sqlalchemy as sa

revision = 's6t7u8v9w0x1'
down_revision = 'r5a6b7c8d9e0'
branch_labels = None
depends_on = None

RESOLVER_HELPER_TOOLS = ('search_domain_field_terms', 'inspect_ontology_term', 'resolve_domain_field_term')


def _set_inheritance(value: bool) -> None:
    connection = op.get_bind()
    for key in RESOLVER_HELPER_TOOLS:
        connection.execute(sa.text('''
            UPDATE tool_policies
            SET config = jsonb_set(config, '{system_managed_inheritance}',
                                   CAST(:value AS jsonb))
            WHERE tool_key = :tool_key
        '''), {'tool_key': key, 'value': 'true' if value else 'false'})


def upgrade():
    _set_inheritance(False)


def downgrade():
    _set_inheritance(True)
