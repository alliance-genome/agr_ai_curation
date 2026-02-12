"""
Alliance Orthologs Agent using OpenAI Agents SDK.

This agent specializes in querying the Alliance of Genome Resources API
for orthology relationships between genes across species.
"""

import logging

from agents import Agent

from ..models import OrthologsResult
from ..prompt_utils import inject_structured_output_instruction

# Prompt cache and context tracking imports
from src.lib.prompts.cache import get_prompt
from src.lib.prompts.context import set_pending_prompts

logger = logging.getLogger(__name__)


def create_orthologs_agent() -> Agent:
    """
    Create an Alliance Orthologs specialist agent.

    All settings configured via environment variables. See config.py.

    This agent runs in isolation when called as a tool by the supervisor.
    It has full autonomy to make multiple tool calls internally.

    Returns:
        An Agent instance configured for orthology queries
    """
    from ..tools.rest_api import create_rest_api_tool
    from ..config import get_agent_config, log_agent_config

    # Get config from registry + environment
    config = get_agent_config("orthologs")
    log_agent_config("Orthologs Specialist", config)

    # Create a restricted REST API tool for Alliance Genome API only
    alliance_api_tool = create_rest_api_tool(
        allowed_domains=["alliancegenome.org", "www.alliancegenome.org"],
        tool_name="alliance_api_call",
        tool_description="Query Alliance of Genome Resources API for orthology data (alliancegenome.org only)"
    )

    # Get base prompt from cache (zero DB queries at runtime)
    base_prompt = get_prompt("orthologs")
    prompts_used = [base_prompt]

    # Build instructions from cached prompt
    instructions = base_prompt.content

    # Inject structured output requirement to prevent silent failures
    instructions = inject_structured_output_instruction(
        instructions,
        output_type=OrthologsResult
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
        "Creating Orthologs agent, model=%s prompt_v=%s",
        config.model,
        base_prompt.version,
    )

    # Log agent configuration to Langfuse for trace visibility
    from ..langfuse_client import log_agent_config
    log_agent_config(
        agent_name="Orthologs Specialist",
        instructions=instructions,
        model=config.model,
        tools=["alliance_api_call"],
        model_settings={
            "temperature": config.temperature,
            "reasoning": config.reasoning,
            "tool_choice": config.tool_choice,
            "prompt_version": base_prompt.version,
        }
    )

    # Create the agent
    agent = Agent(
        name="Orthologs Specialist",
        instructions=instructions,
        model=model,  # LitellmModel for Gemini, string for OpenAI
        model_settings=effective_settings,
        tools=[alliance_api_tool],
        output_type=OrthologsResult,
    )

    # Register prompts for execution logging (committed when agent actually runs)
    set_pending_prompts(agent.name, prompts_used)

    return agent
