"""
Allele Curation Agent using OpenAI Agents SDK.

This agent specializes in querying the Alliance Genome Resources
Curation Database for allele information across multiple model organisms.

Supports group-specific rule injection based on user context (Cognito groups).
"""

import logging
from typing import List, Optional

from agents import Agent

from ..models import AlleleResult, AlleleResultEnvelope
from ..prompt_utils import inject_structured_output_instruction

# Prompt cache and context tracking imports
from src.lib.prompts.cache import get_prompt
from src.lib.prompts.context import set_pending_prompts

logger = logging.getLogger(__name__)


def create_allele_agent(active_groups: Optional[List[str]] = None) -> Agent:
    """
    Create an Allele Curation specialist agent.

    All settings configured via environment variables. See config.py.

    This agent runs in isolation when called as a tool by the supervisor.
    It has full autonomy to make multiple tool calls internally.

    Args:
        active_groups: Optional list of group IDs to inject rules for (e.g., ["MGI", "FB"]).
                       If provided, group-specific rules will be appended to the base prompt.
                       These rules come from the prompt cache (database).

    Returns:
        An Agent instance configured for Allele Curation queries
    """
    from ..tools.agr_curation import agr_curation_query
    from ..config import get_agent_config, log_agent_config

    # Get config from registry + environment
    config = get_agent_config("allele")
    log_agent_config("Allele Specialist", config)

    # Get base prompt from cache (zero DB queries at runtime)
    base_prompt = get_prompt("allele")
    prompts_used = [base_prompt]

    # Build instructions from cached prompt
    instructions = base_prompt.content

    # Inject structured output requirement to prevent silent failures
    instructions = inject_structured_output_instruction(
        instructions,
        output_type=AlleleResultEnvelope
    )

    # Inject group-specific rules if provided
    if active_groups:
        try:
            from config.mod_rules.mod_config import inject_group_rules

            instructions = inject_group_rules(
                base_prompt=instructions,
                group_ids=active_groups,
                component_type="agents",
                component_name="allele",
                prompts_out=prompts_used,  # Collect group prompts for tracking
            )
            logger.info(f"Allele agent configured with group-specific rules: {active_groups}")
        except ImportError as e:
            logger.warning(f"Could not import mod_config, skipping injection: {e}")
        except Exception as e:
            logger.error(f"Failed to inject group rules: {e}")

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
        f"[OpenAI Agents] Creating Allele agent, model={config.model}, "
        f"prompt_v={base_prompt.version}, groups={active_groups}"
    )

    # Log agent configuration to Langfuse for trace visibility
    from ..langfuse_client import log_agent_config
    log_agent_config(
        agent_name="Allele Specialist",
        instructions=instructions,  # Use potentially modified instructions
        model=config.model,
        tools=["agr_curation_query"],
        model_settings={
            "temperature": config.temperature,
            "reasoning": config.reasoning,
            "tool_choice": config.tool_choice,
            "active_groups": active_groups,  # Log which groups are active
            "prompt_version": base_prompt.version,
        }
    )

    # Create the agent
    agent = Agent(
        name="Allele Specialist",
        instructions=instructions,  # Use potentially modified instructions
        model=model,  # LitellmModel for Gemini, string for OpenAI
        model_settings=effective_settings,
        tools=[agr_curation_query],
        output_type=AlleleResultEnvelope,
    )

    # Register prompts for execution logging (committed when agent actually runs)
    set_pending_prompts(agent.name, prompts_used)

    return agent
