"""Seed unified agents table from YAML + migrate custom agents.

Revision ID: v4w5x6y7z8a9
Revises: u3v4w5x6y7z8
Create Date: 2026-02-22
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.dialects.postgresql import insert
import yaml

from src.lib.config.agent_sources import resolve_agent_config_sources

# revision identifiers, used by Alembic.
revision = "v4w5x6y7z8a9"
down_revision = "u3v4w5x6y7z8"
branch_labels = None
depends_on = None


_MODEL_ENV_PATTERN = re.compile(r"^\$\{([A-Z0-9_]+):-([^}]+)\}$")


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


def _load_agent_yaml_specs() -> List[Dict[str, Any]]:
    specs: List[Dict[str, Any]] = []
    for source in resolve_agent_config_sources():
        agent_yaml = source.agent_yaml
        if agent_yaml is None or not agent_yaml.exists():
            continue

        with agent_yaml.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}

        model_cfg = data.get("model_config", {}) or {}
        routing_cfg = data.get("supervisor_routing", {}) or {}
        frontend_cfg = data.get("frontend", {}) or {}
        batchable = bool(routing_cfg.get("batchable", False))

        specs.append(
            {
                "folder_name": source.folder_name,
                "agent_id": str(data.get("agent_id", source.folder_name)),
                "name": str(data.get("name", source.folder_name.replace("_", " ").title())),
                "description": str(data.get("description", "")),
                "category": data.get("category"),
                "tools": list(data.get("tools", []) or []),
                "output_schema_key": data.get("output_schema"),
                "model_id": _resolve_model_name(model_cfg.get("model", "gpt-4o")),
                "model_temperature": float(model_cfg.get("temperature", 0.1)),
                "model_reasoning": model_cfg.get("reasoning"),
                "group_rules_enabled": bool(data.get("group_rules_enabled", False)),
                "icon": str(frontend_cfg.get("icon", "\U0001F916")),
                "show_in_palette": bool(frontend_cfg.get("show_in_palette", True)),
                "supervisor_enabled": bool(routing_cfg.get("enabled", True)),
                "supervisor_description": routing_cfg.get("description"),
                "supervisor_batchable": batchable,
                "supervisor_batching_entity": routing_cfg.get(
                    "batching_entity",
                    f"{source.folder_name}s" if batchable else None,
                ),
            }
        )

    return specs


def _build_parent_spec_index(yaml_specs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Index parent specs by canonical folder and legacy agent_id aliases."""
    indexed: Dict[str, Dict[str, Any]] = {}
    for spec in yaml_specs:
        folder_name = str(spec.get("folder_name") or "").strip()
        agent_id = str(spec.get("agent_id") or "").strip()
        if folder_name:
            indexed[folder_name] = spec
            indexed[folder_name.lower()] = spec
        if agent_id:
            indexed[agent_id] = spec
            indexed[agent_id.lower()] = spec
    return indexed


def _resolve_parent_spec(
    parent_specs: Dict[str, Dict[str, Any]],
    parent_key: str,
) -> Dict[str, Any] | None:
    raw_key = str(parent_key or "").strip()
    if not raw_key:
        return None
    return parent_specs.get(raw_key) or parent_specs.get(raw_key.lower())


def _active_system_prompt(connection: sa.Connection, folder_name: str, agent_id: str) -> str | None:
    prompt_text = sa.text(
        """
        SELECT content
        FROM prompt_templates
        WHERE prompt_type = 'system'
          AND is_active = true
          AND agent_name = :agent_name
        ORDER BY version DESC
        LIMIT 1
        """
    )
    content = connection.execute(prompt_text, {"agent_name": folder_name}).scalar_one_or_none()
    if content is not None:
        return str(content)
    content = connection.execute(prompt_text, {"agent_name": agent_id}).scalar_one_or_none()
    if content is not None:
        return str(content)
    return None


def _system_prompt_from_yaml(folder_name: str) -> tuple[str, str]:
    source = next(
        (
            item
            for item in resolve_agent_config_sources()
            if item.folder_name == folder_name
        ),
        None,
    )
    prompt_yaml = source.prompt_yaml if source is not None else None
    if prompt_yaml is None or not prompt_yaml.exists():
        raise RuntimeError(
            f"Missing prompt.yaml for agent '{folder_name}' at {prompt_yaml}."
        )

    with prompt_yaml.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    content = data.get("content")
    if not content:
        raise RuntimeError(
            f"Missing non-empty 'content' in {prompt_yaml} for agent '{folder_name}'."
        )

    return str(content), source.source_file_display(prompt_yaml)


def _canonical_system_agent_key(spec: Dict[str, Any]) -> str:
    """Resolve canonical unified-agent key for system agent specs."""
    folder_name = str(spec.get("folder_name") or "").strip()
    agent_id = str(spec.get("agent_id") or folder_name).strip()
    if folder_name == "pdf":
        return agent_id
    return folder_name


def _seed_active_system_prompt_from_yaml(
    connection: sa.Connection,
    folder_name: str,
    agent_name: str,
) -> str:
    content, source_file = _system_prompt_from_yaml(folder_name)

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
                'alembic:v4w5x6y7z8a9',
                'Seeded from config/agents prompt.yaml during unified agents migration',
                :source_file
            )
            """
        ),
        {
            "agent_name": agent_name,
            "content": content,
            "version": int(next_version),
            "source_file": source_file,
        },
    )

    return content


def _agents_table() -> sa.Table:
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
        sa.column("mod_prompt_overrides", JSONB),
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
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def upgrade() -> None:
    connection = op.get_bind()
    agents = _agents_table()

    yaml_specs = _load_agent_yaml_specs()
    parent_specs = _build_parent_spec_index(yaml_specs)

    # Seed system agents from config/agents/*/agent.yaml and active DB prompts.
    for spec in yaml_specs:
        canonical_agent_key = _canonical_system_agent_key(spec)
        instructions = _active_system_prompt(
            connection=connection,
            folder_name=spec["folder_name"],
            agent_id=spec["agent_id"],
        )
        if instructions is None:
            instructions = _seed_active_system_prompt_from_yaml(
                connection=connection,
                folder_name=spec["folder_name"],
                agent_name=canonical_agent_key,
            )

        connection.execute(
            insert(agents)
            .values(
                id=uuid.uuid4(),
                agent_key=canonical_agent_key,
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
                group_rules_component=canonical_agent_key if spec["group_rules_enabled"] else None,
                mod_prompt_overrides={},
                icon=spec["icon"],
                category=spec["category"],
                visibility="system",
                project_id=None,
                shared_at=None,
                template_source=spec["agent_id"],
                supervisor_enabled=spec["supervisor_enabled"],
                supervisor_description=spec["supervisor_description"],
                supervisor_batchable=spec["supervisor_batchable"],
                supervisor_batching_entity=spec["supervisor_batching_entity"],
                show_in_palette=spec["show_in_palette"],
                version=1,
                is_active=True,
            )
            .on_conflict_do_nothing(index_elements=["agent_key"])
        )

    # Materialize existing custom_agents rows into unified agents table.
    custom_rows = connection.execute(
        sa.text(
            """
            SELECT
                id,
                user_id,
                parent_agent_key,
                name,
                description,
                custom_prompt,
                mod_prompt_overrides,
                icon,
                include_mod_rules,
                is_active,
                created_at,
                updated_at
            FROM custom_agents
            """
        )
    ).mappings().all()

    for custom in custom_rows:
        parent_key = str(custom["parent_agent_key"] or "").strip()
        parent = _resolve_parent_spec(parent_specs, parent_key)
        if parent is None:
            raise RuntimeError(
                f"Cannot migrate custom agent {custom['id']}: "
                f"unknown parent_agent_key '{parent_key}'."
            )
        canonical_parent_key = str(parent["folder_name"])

        connection.execute(
            insert(agents)
            .values(
                id=custom["id"],
                agent_key=f"ca_{custom['id']}",
                user_id=custom["user_id"],
                name=custom["name"],
                description=custom["description"],
                instructions=custom["custom_prompt"],
                model_id=parent["model_id"],
                model_temperature=parent["model_temperature"],
                model_reasoning=parent["model_reasoning"],
                tool_ids=parent["tools"],
                output_schema_key=parent["output_schema_key"],
                group_rules_enabled=bool(custom["include_mod_rules"]),
                group_rules_component=canonical_parent_key,
                mod_prompt_overrides=custom["mod_prompt_overrides"] or {},
                icon=custom["icon"] or "\U0001F916",
                category=parent["category"],
                visibility="private",
                project_id=None,
                shared_at=None,
                template_source=canonical_parent_key,
                supervisor_enabled=False,
                supervisor_description=None,
                supervisor_batchable=False,
                supervisor_batching_entity=None,
                show_in_palette=True,
                version=1,
                is_active=bool(custom["is_active"]),
                created_at=custom["created_at"],
                updated_at=custom["updated_at"],
            )
            .on_conflict_do_nothing(index_elements=["agent_key"])
        )


def downgrade() -> None:
    connection = op.get_bind()

    yaml_specs = _load_agent_yaml_specs()
    system_keys = [spec["folder_name"] for spec in yaml_specs]

    if system_keys:
        connection.execute(
            sa.text(
                """
                DELETE FROM agents
                WHERE visibility = 'system'
                  AND agent_key = ANY(:system_keys)
                """
            ),
            {"system_keys": system_keys},
        )

    connection.execute(
        sa.text(
            """
            DELETE FROM agents
            WHERE agent_key LIKE 'ca_%'
              AND id IN (SELECT id FROM custom_agents)
            """
        )
    )
