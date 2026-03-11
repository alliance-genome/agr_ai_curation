"""Upsert gene_extractor system agent into unified agents table.

Revision ID: 08b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-02-26
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Tuple

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.dialects.postgresql import insert
import yaml


# revision identifiers, used by Alembic.
revision = "08b9c0d1e2f3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


_MODEL_ENV_PATTERN = re.compile(r"^\$\{([A-Z0-9_]+):-([^}]+)\}$")
_AGENT_KEY = "gene_extractor"
_AGENT_ID = "gene_extractor"
_CREATED_BY = "alembic:08b9c0d1e2f3"


def _repo_root() -> Path:
    # backend/alembic/versions/<this_file>.py -> repo root is parents[3]
    return Path(__file__).resolve().parents[3]


def _resolve_model_name(raw_model: Any) -> str:
    model = str(raw_model or "gpt-4o")
    match = _MODEL_ENV_PATTERN.match(model)
    if not match:
        return model
    env_var = match.group(1)
    default = match.group(2)
    return os.environ.get(env_var, default)


def _load_agent_spec() -> Tuple[Dict[str, Any], str]:
    repo_root = _repo_root()
    agent_yaml = repo_root / "config" / "agents" / _AGENT_KEY / "agent.yaml"
    prompt_yaml = repo_root / "config" / "agents" / _AGENT_KEY / "prompt.yaml"

    if not agent_yaml.exists():
        raise RuntimeError(f"Missing {_AGENT_KEY} agent config: {agent_yaml}")
    if not prompt_yaml.exists():
        raise RuntimeError(f"Missing {_AGENT_KEY} prompt config: {prompt_yaml}")

    with agent_yaml.open("r", encoding="utf-8") as handle:
        agent_data = yaml.safe_load(handle) or {}
    with prompt_yaml.open("r", encoding="utf-8") as handle:
        prompt_data = yaml.safe_load(handle) or {}

    prompt_content = str(prompt_data.get("content") or "").strip()
    if not prompt_content:
        raise RuntimeError(f"Missing non-empty content in {prompt_yaml}")

    model_cfg = agent_data.get("model_config", {}) or {}
    routing_cfg = agent_data.get("supervisor_routing", {}) or {}
    frontend_cfg = agent_data.get("frontend", {}) or {}
    batchable = bool(routing_cfg.get("batchable", False))

    spec = {
        "agent_key": _AGENT_KEY,
        "agent_id": str(agent_data.get("agent_id") or _AGENT_ID),
        "name": str(agent_data.get("name") or "Gene Extraction Agent"),
        "description": str(agent_data.get("description") or ""),
        "category": agent_data.get("category"),
        "tools": list(agent_data.get("tools", []) or []),
        "output_schema_key": agent_data.get("output_schema"),
        "model_id": _resolve_model_name(model_cfg.get("model", "gpt-4o")),
        "model_temperature": float(model_cfg.get("temperature", 0.1)),
        "model_reasoning": model_cfg.get("reasoning"),
        "group_rules_enabled": bool(agent_data.get("group_rules_enabled", False)),
        "icon": str(frontend_cfg.get("icon", "🤖")),
        "show_in_palette": bool(frontend_cfg.get("show_in_palette", True)),
        "supervisor_enabled": bool(routing_cfg.get("enabled", True)),
        "supervisor_description": routing_cfg.get("description"),
        "supervisor_batchable": batchable,
        "supervisor_batching_entity": routing_cfg.get(
            "batching_entity",
            f"{_AGENT_KEY}s" if batchable else None,
        ),
    }

    return spec, prompt_content


def _active_system_prompt(connection: sa.Connection, agent_name: str) -> str | None:
    content = connection.execute(
        sa.text(
            """
            SELECT content
            FROM prompt_templates
            WHERE prompt_type = 'system'
              AND is_active = true
              AND agent_name = :agent_name
            ORDER BY version DESC
            LIMIT 1
            """
        ),
        {"agent_name": agent_name},
    ).scalar_one_or_none()
    if content is None:
        return None
    return str(content)


def _acquire_prompt_seed_lock(connection: sa.Connection, agent_name: str) -> None:
    """Serialize prompt seeding for this agent during concurrent migration runners."""
    connection.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"prompt_templates:system:{agent_name}"},
    )


def _seed_prompt_template(connection: sa.Connection, agent_name: str, content: str) -> str:
    next_version = connection.execute(
        sa.text(
            """
            SELECT COALESCE(MAX(version), 0) + 1
            FROM prompt_templates
            WHERE agent_name = :agent_name
              AND prompt_type = 'system'
              AND group_id IS NULL
            """
        ),
        {"agent_name": agent_name},
    ).scalar_one()

    connection.execute(
        sa.text(
            """
            INSERT INTO prompt_templates (
                id,
                agent_name,
                prompt_type,
                group_id,
                content,
                version,
                is_active,
                created_by,
                change_notes,
                source_file
            )
            VALUES (
                gen_random_uuid(),
                :agent_name,
                'system',
                NULL,
                :content,
                :version,
                true,
                :created_by,
                'Seeded gene_extractor prompt for unified agents runtime parity',
                :source_file
            )
            """
        ),
        {
            "agent_name": agent_name,
            "content": content,
            "version": int(next_version),
            "created_by": _CREATED_BY,
            "source_file": f"config/agents/{_AGENT_KEY}/prompt.yaml",
        },
    )

    return content


def _prompt_overrides_column_name(connection: sa.Connection) -> str:
    columns = {column["name"] for column in sa.inspect(connection).get_columns("agents")}
    if "group_prompt_overrides" in columns:
        return "group_prompt_overrides"
    if "mod_prompt_overrides" in columns:
        return "mod_prompt_overrides"
    raise RuntimeError(
        "agents table is missing both group_prompt_overrides and mod_prompt_overrides"
    )


def _agents_table(prompt_overrides_column: str) -> sa.Table:
    return sa.table(
        "agents",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("agent_key", sa.String()),
        sa.column("user_id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("instructions", sa.Text()),
        sa.column("model_id", sa.String()),
        sa.column("model_temperature", sa.Float()),
        sa.column("model_reasoning", sa.String()),
        sa.column("tool_ids", JSONB),
        sa.column("output_schema_key", sa.String()),
        sa.column("group_rules_enabled", sa.Boolean()),
        sa.column("group_rules_component", sa.String()),
        sa.column(prompt_overrides_column, JSONB),
        sa.column("icon", sa.String()),
        sa.column("category", sa.String()),
        sa.column("visibility", sa.String()),
        sa.column("project_id", UUID(as_uuid=True)),
        sa.column("shared_at", sa.DateTime(timezone=True)),
        sa.column("template_source", sa.String()),
        sa.column("supervisor_enabled", sa.Boolean()),
        sa.column("supervisor_description", sa.Text()),
        sa.column("supervisor_batchable", sa.Boolean()),
        sa.column("supervisor_batching_entity", sa.String()),
        sa.column("show_in_palette", sa.Boolean()),
        sa.column("version", sa.Integer()),
        sa.column("is_active", sa.Boolean()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def upgrade() -> None:
    connection = op.get_bind()
    spec, prompt_content = _load_agent_spec()
    prompt_overrides_column = _prompt_overrides_column_name(connection)

    _acquire_prompt_seed_lock(connection, _AGENT_KEY)
    instructions = _active_system_prompt(connection, _AGENT_KEY)
    if instructions is None:
        instructions = _seed_prompt_template(connection, _AGENT_KEY, prompt_content)

    agents = _agents_table(prompt_overrides_column)
    connection.execute(
        insert(agents)
        .values(
            id=uuid.uuid4(),
            agent_key=spec["agent_key"],
            user_id=None,
            name=spec["name"],
            description=spec["description"],
            instructions=instructions,
            model_id=spec["model_id"],
            model_temperature=spec["model_temperature"],
            model_reasoning=spec["model_reasoning"],
            tool_ids=spec["tools"],
            output_schema_key=spec["output_schema_key"],
            group_rules_enabled=spec["group_rules_enabled"],
            group_rules_component=spec["agent_key"] if spec["group_rules_enabled"] else None,
            **{prompt_overrides_column: {}},
            icon=spec["icon"],
            category=spec["category"],
            visibility="system",
            project_id=None,
            shared_at=None,
            template_source=spec["agent_key"],
            supervisor_enabled=spec["supervisor_enabled"],
            supervisor_description=spec["supervisor_description"],
            supervisor_batchable=spec["supervisor_batchable"],
            supervisor_batching_entity=spec["supervisor_batching_entity"],
            show_in_palette=spec["show_in_palette"],
            version=1,
            is_active=True,
            updated_at=sa.func.now(),
        )
        .on_conflict_do_update(
            index_elements=["agent_key"],
            set_={
                "user_id": None,
                "name": spec["name"],
                "description": spec["description"],
                "instructions": instructions,
                "model_id": spec["model_id"],
                "model_temperature": spec["model_temperature"],
                "model_reasoning": spec["model_reasoning"],
                "tool_ids": spec["tools"],
                "output_schema_key": spec["output_schema_key"],
                "group_rules_enabled": spec["group_rules_enabled"],
                "group_rules_component": spec["agent_key"] if spec["group_rules_enabled"] else None,
                prompt_overrides_column: {},
                "icon": spec["icon"],
                "category": spec["category"],
                "visibility": "system",
                "project_id": None,
                "shared_at": None,
                "template_source": spec["agent_key"],
                "supervisor_enabled": spec["supervisor_enabled"],
                "supervisor_description": spec["supervisor_description"],
                "supervisor_batchable": spec["supervisor_batchable"],
                "supervisor_batching_entity": spec["supervisor_batching_entity"],
                "show_in_palette": spec["show_in_palette"],
                "version": 1,
                "is_active": True,
                "updated_at": sa.func.now(),
            },
        )
    )


def downgrade() -> None:
    connection = op.get_bind()

    connection.execute(
        sa.text(
            """
            DELETE FROM agents
            WHERE visibility = 'system'
              AND agent_key = :agent_key
            """
        ),
        {"agent_key": _AGENT_KEY},
    )

    connection.execute(
        sa.text(
            """
            DELETE FROM prompt_templates
            WHERE agent_name = :agent_name
              AND prompt_type = 'system'
              AND created_by = :created_by
            """
        ),
        {"agent_name": _AGENT_KEY, "created_by": _CREATED_BY},
    )
