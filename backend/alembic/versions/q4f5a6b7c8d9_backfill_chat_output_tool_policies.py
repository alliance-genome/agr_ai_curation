"""Seed runtime formatter helpers added for tool-rendered chat output (ALL-1275).

Revision ID: q4f5a6b7c8d9
Revises: p3e4f5a6b7c8
"""
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]
import sqlalchemy as sa

revision = 'q4f5a6b7c8d9'
down_revision = 'p3e4f5a6b7c8'
branch_labels = None
depends_on = None

# These helpers are app-owned bindings, absent from package export discovery.
FORMATTER_TOOLS = ('read_output_value', 'finalize_chat_output')


def _defaults():
    path = Path(__file__).with_name('z8a9b0c1d2e3_add_tool_policies_table.py')
    spec = spec_from_file_location('chat_output_policy_seed_source', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('Cannot load frozen policy seed helper')
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._load_default_tool_policies()


def upgrade():
    defaults = _defaults()
    connection = op.get_bind()
    for key in FORMATTER_TOOLS:
        policy = defaults[key]
        connection.execute(sa.text('''
            INSERT INTO tool_policies
                (tool_key, display_name, description, category,
                 curator_visible, allow_attach, allow_execute, config)
            VALUES
                (:tool_key, :display_name, :description, :category,
                 :curator_visible, :allow_attach, :allow_execute, CAST(:config AS jsonb))
            ON CONFLICT (tool_key) DO NOTHING
        '''), {**policy, 'tool_key': key, 'config': json.dumps(policy['config'])})


def downgrade():
    # Never remove policies that may now be used or changed by an operator.
    pass
