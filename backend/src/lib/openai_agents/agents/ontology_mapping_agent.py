"""
Ontology Mapping Agent using OpenAI Agents SDK.

This agent specializes in mapping human-readable labels to ontology term IDs
from the AGR Curation Database across multiple model organisms.
"""

import logging

from agents import Agent

from ..models import OntologyMappingEnvelope
from ..prompt_utils import inject_structured_output_instruction

# Prompt cache and context tracking imports
from src.lib.prompts.cache import get_prompt
from src.lib.prompts.context import set_pending_prompts

logger = logging.getLogger(__name__)


def create_ontology_mapping_agent() -> Agent:
    """
    Create an Ontology Mapping specialist agent.

    All settings configured via environment variables. See config.py.

    This agent runs in isolation when called as a tool by the supervisor.
    It has full autonomy to make multiple tool calls internally.

    Returns:
        An Agent instance configured for Ontology Mapping
    """
    from ..tools.agr_curation import agr_curation_query
    from ..config import get_agent_config, log_agent_config

    # Get config from registry + environment
    config = get_agent_config("ontology_mapping")
    log_agent_config("Ontology Mapping Specialist", config)

    # Get base prompt from cache (zero DB queries at runtime)
    base_prompt = get_prompt("ontology_mapping")
    prompts_used = [base_prompt]

    # Build instructions from cached prompt
    instructions = base_prompt.content

    # Inject structured output requirement to prevent silent failures
    instructions = inject_structured_output_instruction(
        instructions,
        output_type=OntologyMappingEnvelope
    )

    # Build model settings using shared helper (supports both OpenAI and Gemini)
    from ..config import build_model_settings, get_model_for_agent
    effective_settings = build_model_settings(
        model=config.model,
        temperature=config.temperature,
        reasoning_effort=config.reasoning,
        tool_choice=config.tool_choice,
        parallel_tool_calls=True,
    )

    # Get the model (returns LitellmModel for Gemini, string for OpenAI)
    model = get_model_for_agent(config.model)

    logger.info(
        "Creating Ontology Mapping agent, model=%s prompt_v=%s",
        config.model,
        base_prompt.version,
    )

    # Log agent configuration to Langfuse for trace visibility
    from ..langfuse_client import log_agent_config
    log_agent_config(
        agent_name="Ontology Mapping Specialist",
        instructions=instructions,
        model=config.model,
        tools=["agr_curation_query"],
        model_settings={
            "temperature": config.temperature,
            "reasoning": config.reasoning,
            "tool_choice": config.tool_choice,
            "prompt_version": base_prompt.version,
        }
    )

    # Create the agent
    agent = Agent(
        name="Ontology Mapping Specialist",
        instructions=instructions,
        model=model,  # LitellmModel for Gemini, string for OpenAI
        model_settings=effective_settings,
        tools=[agr_curation_query],
        output_type=OntologyMappingEnvelope,
    )

    # Register prompts for execution logging (committed when agent actually runs)
    set_pending_prompts(agent.name, prompts_used)

    return agent
