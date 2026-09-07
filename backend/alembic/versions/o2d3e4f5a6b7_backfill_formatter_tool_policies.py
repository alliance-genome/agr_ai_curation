"""Seed runtime formatter helpers omitted by package-only policy discovery.

Revision ID: o2d3e4f5a6b7
Revises: n1c2d3e4f5a6
"""
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]
import sqlalchemy as sa

revision = 'o2d3e4f5a6b7'
down_revision = 'n1c2d3e4f5a6'
branch_labels = None
depends_on = None

# These helpers are app-owned bindings, absent from package export discovery.
FORMATTER_TOOLS = (
    'explain_formatter_capabilities', 'inspect_output_artifacts', 'inspect_output_rows',
    'inspect_field_values', 'build_default_projection_plan', 'validate_output_projection',
    'preview_output_projection', 'finalize_and_save', 'formatter_cannot_complete',
)


def _defaults():
    path = Path(__file__).with_name('z8a9b0c1d2e3_add_tool_policies_table.py')
    spec = spec_from_file_location('formatter_policy_seed_source', path)
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
