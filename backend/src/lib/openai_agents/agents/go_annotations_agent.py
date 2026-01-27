"""
GO Annotations Agent using OpenAI Agents SDK.

This agent specializes in retrieving GO annotations for specific genes from the
Gene Ontology Consortium's API, including evidence codes and curation sources.
"""

import logging

from agents import Agent

from ..models import GOAnnotationsResult
from ..prompt_utils import inject_structured_output_instruction

# Prompt cache and context tracking imports
from src.lib.prompts.cache import get_prompt
from src.lib.prompts.context import set_pending_prompts

logger = logging.getLogger(__name__)


def create_go_annotations_agent() -> Agent:
    """
    Create a GO Annotations specialist agent.

    All settings configured via environment variables. See config.py.

    This agent runs in isolation when called as a tool by the supervisor.
    It has full autonomy to make multiple tool calls internally.

    Returns:
        An Agent instance configured for GO annotation queries
    """
    from ..tools.rest_api import create_rest_api_tool
    from ..config import get_agent_config, log_agent_config

    # Get config from registry + environment
    config = get_agent_config("go_annotations")
    log_agent_config("GO Annotations Specialist", config)

    # Create a restricted REST API tool for GO API only
    go_api_tool = create_rest_api_tool(
        allowed_domains=["geneontology.org", "api.geneontology.org"],
        tool_name="go_api_call",
        tool_description="Query Gene Ontology API for gene annotations with evidence codes (geneontology.org only)"
    )

    # Get base prompt from cache (zero DB queries at runtime)
    base_prompt = get_prompt("go_annotations")
    prompts_used = [base_prompt]

    # Build instructions from cached prompt
    instructions = base_prompt.content

    # Inject structured output requirement to prevent silent failures
    instructions = inject_structured_output_instruction(
        instructions,
        output_type=GOAnnotationsResult
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
        f"[OpenAI Agents] Creating GO Annotations agent, model={config.model}, "
        f"prompt_v={base_prompt.version}"
    )

    # Log agent configuration to Langfuse for trace visibility
    from ..langfuse_client import log_agent_config
    log_agent_config(
        agent_name="GO Annotations Specialist",
        instructions=instructions,
        model=config.model,
        tools=["go_api_call"],
        model_settings={
            "temperature": config.temperature,
            "reasoning": config.reasoning,
            "tool_choice": config.tool_choice,
            "prompt_version": base_prompt.version,
        }
    )

    # Create the agent
    agent = Agent(
        name="GO Annotations Specialist",
        instructions=instructions,
        model=model,  # LitellmModel for Gemini, string for OpenAI
        model_settings=effective_settings,
        tools=[go_api_tool],
        output_type=GOAnnotationsResult,
    )

    # Register prompts for execution logging (committed when agent actually runs)
    set_pending_prompts(agent.name, prompts_used)

    return agent
