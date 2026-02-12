"""CSV Formatter Agent using OpenAI Agents SDK.

This agent formats data as CSV (comma-separated values) files for download.
It uses a tool to save the file and returns FileInfo for the frontend.

Key Design:
- Context (trace_id, session_id, curator_id) captured at INVOCATION time from contextvars
- NO parameters passed to factory - all context from contextvars
- Simple prompt, no reasoning mode needed
"""

import logging
from agents import Agent

from src.lib.prompts.cache import get_prompt
from src.lib.prompts.context import set_pending_prompts

from ..config import (
    build_model_settings,
    get_agent_config,
    get_model_for_agent,
    log_agent_config,
)
from ..tools.file_output_tools import create_csv_tool

logger = logging.getLogger(__name__)


def create_csv_formatter_agent() -> Agent:
    """Create a CSV Formatter agent.

    Note: trace_id, session_id, curator_id are NOT passed as parameters.
    They are captured from context variables at tool INVOCATION time
    (not tool creation time), which is necessary because trace_id isn't
    available until after the Langfuse trace is created.

    Returns:
        An Agent instance configured for CSV formatting
    """
    # Get config from registry + environment
    config = get_agent_config("csv_formatter")
    log_agent_config("CSV Formatter", config)

    # Get prompts from cache (zero DB queries at runtime)
    base_prompt = get_prompt("csv_formatter", "system")
    prompts_used = [base_prompt]

    # Build instructions from cached prompt
    instructions = base_prompt.content

    # Get model (returns LitellmModel for Gemini, string for OpenAI)
    model = get_model_for_agent(config.model)

    # Build model settings - NO reasoning for formatters (simple task)
    # Use build_model_settings to handle models that don't support temperature (e.g., GPT-5)
    model_settings = build_model_settings(
        model=config.model,
        temperature=config.temperature,
        reasoning_effort=None,  # Formatters don't use reasoning
        parallel_tool_calls=False,  # One file at a time
    )

    # Create tool with context captured via closure
    save_csv = create_csv_tool()

    logger.info(
        "Creating CSV Formatter agent, model=%s temp=%s prompt_v=%s",
        config.model,
        config.temperature,
        base_prompt.version,
    )

    # Log agent configuration to Langfuse for trace visibility
    from ..langfuse_client import log_agent_config as log_to_langfuse
    log_to_langfuse(
        agent_name="CSV Formatter",
        instructions=instructions,
        model=config.model,
        tools=["save_csv_file"],
        model_settings={
            "temperature": config.temperature,
            "reasoning": None,
            "prompt_version": base_prompt.version,
        },
    )

    # Create the formatter agent
    agent = Agent(
        name="CSV Formatter",
        instructions=instructions,
        model=model,
        model_settings=model_settings,
        tools=[save_csv],
        output_type=None,  # Tool returns FileInfo dict directly
    )

    # Register prompts for execution logging (committed when agent runs)
    set_pending_prompts(agent.name, prompts_used)

    return agent
