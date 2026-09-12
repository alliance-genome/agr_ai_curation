"""Backfill missing installed tool policies without changing operator decisions.

Revision ID: n1c2d3e4f5a6
Revises: m0b1c2d3e4f5
"""

from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]
import sqlalchemy as sa

revision = "n1c2d3e4f5a6"
down_revision = "m0b1c2d3e4f5"
branch_labels = None
depends_on = None


def _migration_helper(filename):
    # Reuse frozen migration discovery, not application imports or startup state.
    path = Path(__file__).with_name(filename)
    spec = spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load migration helper {filename}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _installed_defaults():
    seeds = _migration_helper("z8a9b0c1d2e3_add_tool_policies_table.py")
    bindings = _migration_helper("a823b1c2d3e4_reconcile_alliance_tool_policy.py")
    installed = bindings._installed_tool_binding_ids()
    return {
        key: value for key, value in seeds._load_default_tool_policies().items()
        if key in installed
    }


def upgrade():
    connection = op.get_bind()
    for tool_key, policy in _installed_defaults().items():
        connection.execute(
            sa.text("""
                INSERT INTO tool_policies
                    (tool_key, display_name, description, category,
                     curator_visible, allow_attach, allow_execute, config)
                VALUES
                    (:tool_key, :display_name, :description, :category,
                     :curator_visible, :allow_attach, :allow_execute, CAST(:config AS jsonb))
                ON CONFLICT (tool_key) DO NOTHING
            """),
            {**policy, "tool_key": tool_key, "config": json.dumps(policy["config"])},
        )


def downgrade():
    # Rows may now be referenced by saved agents or changed by an operator.
    # Their provenance is not recoverable, so never delete them on downgrade.
    pass
