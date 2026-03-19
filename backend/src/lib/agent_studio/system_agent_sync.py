"""Synchronize system-agent rows from layered YAML/package sources."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Optional

import yaml
from sqlalchemy.orm import Session

from src.lib.config.agent_loader import AgentDefinition, load_agent_definitions
from src.lib.config.agent_sources import AgentConfigSource, resolve_agent_config_sources
from src.models.sql.agent import Agent as DBAgent
from src.models.sql.prompts import PromptTemplate

logger = logging.getLogger(__name__)


def canonical_system_agent_key(agent: AgentDefinition) -> str:
    """Return the unified-agents key for a shipped/package-owned system agent."""
    if agent.folder_name == "pdf":
        return agent.agent_id
    return agent.folder_name


def _load_prompt_content_from_source(source: AgentConfigSource | None) -> Optional[str]:
    prompt_yaml = source.prompt_yaml if source is not None else None
    if prompt_yaml is None or not prompt_yaml.exists():
        return None

    with prompt_yaml.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    content = str(data.get("content") or "").strip()
    return content or None


def _get_active_system_prompt(
    db: Session,
    *,
    folder_name: str,
    agent_id: str,
) -> Optional[str]:
    for agent_name in (folder_name, agent_id):
        prompt = (
            db.query(PromptTemplate)
            .filter(PromptTemplate.agent_name == agent_name)
            .filter(PromptTemplate.prompt_type == "system")
            .filter(PromptTemplate.group_id.is_(None))
            .filter(PromptTemplate.is_active == True)  # noqa: E712
            .order_by(PromptTemplate.version.desc())
            .first()
        )
        if prompt is not None and str(prompt.content or "").strip():
            return str(prompt.content)
    return None


def _build_system_agent_values(
    agent: AgentDefinition,
    *,
    instructions: str,
) -> dict[str, Any]:
    agent_key = canonical_system_agent_key(agent)
    return {
        "agent_key": agent_key,
        "user_id": None,
        "name": agent.name,
        "description": agent.description,
        "instructions": instructions,
        "model_id": agent.model_config.model,
        "model_temperature": float(agent.model_config.temperature),
        "model_reasoning": agent.model_config.reasoning,
        "tool_ids": list(agent.tools),
        "output_schema_key": agent.output_schema,
        "group_rules_enabled": bool(agent.group_rules_enabled),
        "group_rules_component": agent_key if agent.group_rules_enabled else None,
        "group_prompt_overrides": {},
        "icon": agent.frontend.icon,
        "category": agent.category,
        "visibility": "system",
        "project_id": None,
        "shared_at": None,
        "template_source": agent.agent_id,
        "supervisor_enabled": bool(agent.supervisor_routing.enabled),
        "supervisor_description": agent.supervisor_routing.description,
        "supervisor_batchable": bool(agent.supervisor_routing.batchable),
        "supervisor_batching_entity": agent.supervisor_routing.batching_entity or None,
        "show_in_palette": bool(agent.frontend.show_in_palette),
        "is_active": True,
    }


def sync_system_agents(
    db: Session,
    *,
    agents_path: Path | None = None,
    force_reload: bool = False,
) -> dict[str, int]:
    """Upsert system-agent rows from current layered agent sources.

    The active prompt template remains the source of truth for `instructions`.
    If the prompt cache has not been seeded yet, prompt.yaml is used as a
    fallback so the unified agents table still becomes runnable.
    """
    sources = resolve_agent_config_sources(agents_path)
    source_by_folder = {source.folder_name: source for source in sources}
    agent_defs = load_agent_definitions(agents_path, force_reload=force_reload)

    existing_rows = {
        str(row.agent_key): row
        for row in db.query(DBAgent)
        .filter(DBAgent.visibility == "system")
        .all()
    }

    inserted = 0
    updated = 0
    reactivated = 0
    deactivated = 0
    discovered_keys: set[str] = set()

    for agent in sorted(agent_defs.values(), key=lambda item: item.folder_name):
        agent_key = canonical_system_agent_key(agent)

        instructions = _get_active_system_prompt(
            db,
            folder_name=agent.folder_name,
            agent_id=agent.agent_id,
        ) or _load_prompt_content_from_source(source_by_folder.get(agent.folder_name))
        if not instructions:
            logger.warning(
                "Skipping system agent '%s': no prompt content available from DB or prompt.yaml.",
                agent.folder_name,
            )
            continue

        discovered_keys.add(agent_key)
        values = _build_system_agent_values(agent, instructions=instructions)
        row = existing_rows.get(agent_key)

        if row is None:
            db.add(
                DBAgent(
                    id=uuid.uuid4(),
                    version=1,
                    **values,
                )
            )
            inserted += 1
            continue

        was_active = bool(row.is_active)
        metadata_changed = False
        for field_name, field_value in values.items():
            # Don't force re-enable agents that were programmatically disabled
            # (e.g. by runtime validation due to missing tool dependencies).
            # Only new inserts get is_active=True automatically.
            if field_name == "is_active" and not was_active:
                continue
            if getattr(row, field_name) != field_value:
                setattr(row, field_name, field_value)
                metadata_changed = True

        if metadata_changed:
            updated += 1
        if not was_active and bool(row.is_active):
            reactivated += 1

    for agent_key, row in existing_rows.items():
        if agent_key in discovered_keys or not bool(row.is_active):
            continue

        row.is_active = False
        row.supervisor_enabled = False
        deactivated += 1
        logger.warning(
            "Deactivated stale system agent '%s': no current package/config source provides it.",
            agent_key,
        )

    if inserted or updated or reactivated or deactivated:
        db.commit()

    return {
        "inserted": inserted,
        "updated": updated,
        "reactivated": reactivated,
        "deactivated": deactivated,
        "discovered": len(discovered_keys),
    }
