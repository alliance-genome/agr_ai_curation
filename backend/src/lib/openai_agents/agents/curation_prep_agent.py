"""Direct OpenAI Agents SDK builder for the curation prep workflow."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml
from agents import Agent

from src.lib.config.agent_loader import AgentDefinition, get_agent_definition
from src.lib.config.agent_sources import resolve_agent_config_sources
from src.lib.openai_agents.config import (
    ReasoningEffort,
    build_model_settings,
    get_model_for_agent,
    resolve_model_provider,
)
from src.lib.openai_agents.prompt_utils import inject_structured_output_instruction
from src.lib.prompts.cache import PromptNotFoundError, get_prompt
from src.schemas.curation_prep import CurationPrepAgentOutput

logger = logging.getLogger(__name__)

CURATION_PREP_AGENT_ID = "curation_prep"
_SUPPORTED_REASONING_LEVELS = frozenset({"minimal", "low", "medium", "high"})


def get_curation_prep_agent_definition() -> AgentDefinition:
    """Return the layered config definition for the curation prep agent."""

    definition = get_agent_definition(CURATION_PREP_AGENT_ID)
    if definition is None:
        raise ValueError(
            "Curation prep agent definition is missing. "
            "Expected config/agents/curation_prep/agent.yaml to be discoverable."
        )
    return definition


def create_curation_prep_agent() -> Agent:
    """Create the curation prep agent with structured-output enforcement."""

    definition = get_curation_prep_agent_definition()
    provider = resolve_model_provider(definition.model_config.model)
    model = get_model_for_agent(definition.model_config.model, provider_override=provider)

    reasoning_value = str(definition.model_config.reasoning or "").strip().lower()
    reasoning_effort: Optional[ReasoningEffort]
    if reasoning_value in _SUPPORTED_REASONING_LEVELS:
        reasoning_effort = reasoning_value  # type: ignore[assignment]
    else:
        reasoning_effort = None

    instructions = inject_structured_output_instruction(
        _load_curation_prep_prompt(),
        output_type=CurationPrepAgentOutput,
    )

    model_settings = build_model_settings(
        model=definition.model_config.model,
        temperature=definition.model_config.temperature,
        reasoning_effort=reasoning_effort,
        parallel_tool_calls=False,
        verbosity="low",
        include_usage=True,
        provider_override=provider,
    )

    return Agent(
        name=definition.name,
        instructions=instructions,
        model=model,
        model_settings=model_settings,
        tools=[],
        output_type=CurationPrepAgentOutput,
    )


def _load_curation_prep_prompt() -> str:
    """Load prompt content from the prompt cache, falling back to prompt.yaml."""

    try:
        return get_prompt(CURATION_PREP_AGENT_ID).content
    except (PromptNotFoundError, RuntimeError):
        prompt_path = _resolve_curation_prep_prompt_path()
        if prompt_path is None:
            raise ValueError(
                "Curation prep prompt not found in prompt cache and no prompt.yaml "
                "was discovered for config/agents/curation_prep."
            )

        with prompt_path.open("r", encoding="utf-8") as handle:
            prompt_yaml = yaml.safe_load(handle) or {}

        content = str(prompt_yaml.get("content") or "").strip()
        if not content:
            raise ValueError(f"Curation prep prompt is empty: {prompt_path}")

        logger.info(
            "Using curation prep prompt fallback from %s because no active cached prompt was found.",
            prompt_path,
        )
        return content


def _resolve_curation_prep_prompt_path() -> Path | None:
    """Return the layered prompt.yaml path for the curation prep agent when present."""

    for source in resolve_agent_config_sources():
        if source.folder_name != CURATION_PREP_AGENT_ID:
            continue
        prompt_yaml = source.prompt_yaml
        if prompt_yaml is not None and prompt_yaml.exists():
            return prompt_yaml
    return None


__all__ = [
    "CURATION_PREP_AGENT_ID",
    "create_curation_prep_agent",
    "get_curation_prep_agent_definition",
]
