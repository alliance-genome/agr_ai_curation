"""Add tool_policies table and seed defaults.

Revision ID: z8a9b0c1d2e3
Revises: y7z8a9b0c1d2
Create Date: 2026-02-23
"""

import json
from typing import Any, Dict

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from src.lib.config.tool_policy_defaults_loader import load_tool_policy_defaults


# revision identifiers, used by Alembic.
revision = "z8a9b0c1d2e3"
down_revision = "y7z8a9b0c1d2"
branch_labels = None
depends_on = None


def _load_default_tool_policies() -> Dict[str, Dict[str, Any]]:
    """Load seed tool policy defaults from package exports plus runtime overrides."""
    return {
        tool_key: {
            "display_name": policy.display_name,
            "description": policy.description,
            "category": policy.category,
            "curator_visible": policy.curator_visible,
            "allow_attach": policy.allow_attach,
            "allow_execute": policy.allow_execute,
            "config": policy.config,
        }
        for tool_key, policy in load_tool_policy_defaults().items()
    }


def upgrade() -> None:
    op.create_table(
        "tool_policies",
        sa.Column("tool_key", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=100), nullable=False, server_default="General"),
        sa.Column("curator_visible", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("allow_attach", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("allow_execute", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("config", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tool_key"),
    )
    op.create_index("ix_tool_policies_category", "tool_policies", ["category"], unique=False)
    op.create_index(
        "ix_tool_policies_curator_visible",
        "tool_policies",
        ["curator_visible"],
        unique=False,
    )

    connection = op.get_bind()
    policies = _load_default_tool_policies()
    for tool_key, data in policies.items():
        connection.execute(
            sa.text(
                """
                INSERT INTO tool_policies (
                    tool_key,
                    display_name,
                    description,
                    category,
                    curator_visible,
                    allow_attach,
                    allow_execute,
                    config
                ) VALUES (
                    :tool_key,
                    :display_name,
                    :description,
                    :category,
                    :curator_visible,
                    :allow_attach,
                    :allow_execute,
                    CAST(:config AS jsonb)
                )
                ON CONFLICT (tool_key) DO NOTHING
                """
            ),
            {
                "tool_key": str(tool_key),
                "display_name": str(data.get("display_name", tool_key)),
                "description": str(data.get("description", "")),
                "category": str(data.get("category", "General")),
                "curator_visible": bool(data.get("curator_visible", True)),
                "allow_attach": bool(data.get("allow_attach", True)),
                "allow_execute": bool(data.get("allow_execute", True)),
                "config": json.dumps(dict(data.get("config", {}) or {})),
            },
        )


def downgrade() -> None:
    op.drop_index("ix_tool_policies_curator_visible", table_name="tool_policies")
    op.drop_index("ix_tool_policies_category", table_name="tool_policies")
    op.drop_table("tool_policies")
