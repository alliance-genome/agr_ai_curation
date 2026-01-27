"""
Chat Output Agent using OpenAI Agents SDK.

This agent formats and displays curation results directly in the chat window
as an alternative to structured JSON output. It provides human-readable
summaries and formatted output for curator review.

Use this agent instead of JSON Formatter when curators want to see results
in the chat interface rather than structured data exports.
"""

import logging

from agents import Agent

# Prompt cache and context tracking imports
from src.lib.prompts.cache import get_prompt
from src.lib.prompts.context import set_pending_prompts

logger = logging.getLogger(__name__)


def create_chat_output_agent() -> Agent:
    """
    Create a Chat Output agent for formatting results as chat messages.

    This agent has no tools - its only purpose is to take extracted data
    and format it into a readable chat response with proper formatting,
    tables, and summaries.

    Returns:
        An Agent instance configured for chat output formatting
    """
    from ..config import get_agent_config, log_agent_config, build_model_settings, get_model_for_agent

    # Get config from registry + environment
    config = get_agent_config("chat_output")
    log_agent_config("Chat Output Agent", config)

    # Get base prompt from cache (zero DB queries at runtime)
    base_prompt = get_prompt("chat_output")
    prompts_used = [base_prompt]

    # Build instructions from cached prompt
    instructions = base_prompt.content

    # Build model settings - no reasoning needed for simple formatting
    effective_settings = build_model_settings(
        model=config.model,
        temperature=config.temperature,
        reasoning_effort=None,  # No reasoning for formatting
        tool_choice=None,
        parallel_tool_calls=False,
    )

    # Get the model (returns LitellmModel for Gemini, string for OpenAI)
    model = get_model_for_agent(config.model)

    logger.info(
        f"[OpenAI Agents] Creating Chat Output agent, model={config.model}, "
        f"prompt_v={base_prompt.version}"
    )

    # Log agent configuration to Langfuse for trace visibility
    from ..langfuse_client import log_agent_config
    log_agent_config(
        agent_name="Chat Output Agent",
        instructions=instructions,
        model=config.model,
        tools=[],  # No tools - pure formatting
        model_settings={
            "temperature": config.temperature,
            "reasoning": None,
            "prompt_version": base_prompt.version,
        },
        metadata={
            "purpose": "chat_formatting"
        }
    )

    # Create the agent
    agent = Agent(
        name="Chat Output Agent",
        instructions=instructions,
        model=model,
        model_settings=effective_settings,
        tools=[],  # No tools - formatting only
        output_type=None,  # Plain text output for chat
    )

    # Register prompts for execution logging (committed when agent actually runs)
    set_pending_prompts(agent.name, prompts_used)

    return agent
