"""Seed policy rows for the topic-tag builder tools added in 0.12.0.

The table seed reads the package defaults at migration time, so databases
seeded before these tools existed have no rows for them, and a curator's copy
of the topic-tag agent is refused at run time.

Revision ID: a9b8c7d6e5f4
Revises: b5c6d7e8f9a0
"""
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]
import sqlalchemy as sa

revision = 'a9b8c7d6e5f4'
down_revision = 'b5c6d7e8f9a0'
branch_labels = None
depends_on = None

TOPIC_ENTITY_TAG_TOOLS = (
    'stage_topic_entity_tag', 'patch_topic_entity_tag', 'discard_topic_entity_tag',
    'list_staged_topic_entity_tags', 'finalize_topic_entity_tag_extraction',
)


def _defaults():
    path = Path(__file__).with_name('z8a9b0c1d2e3_add_tool_policies_table.py')
    spec = spec_from_file_location('topic_entity_tag_policy_seed_source', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('Cannot load frozen policy seed helper')
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._load_default_tool_policies()


def upgrade():
    defaults = _defaults()
    connection = op.get_bind()
    for key in TOPIC_ENTITY_TAG_TOOLS:
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
