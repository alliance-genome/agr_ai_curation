"""
Disease Ontology Agent using OpenAI Agents SDK.

This agent specializes in querying disease ontology information from the
Alliance Curation Database's ontologyterm table.
"""

import logging
import os

from agents import Agent

from ..models import DiseaseResult, DiseaseResultEnvelope
from ..prompt_utils import inject_structured_output_instruction

# Prompt cache and context tracking imports
from src.lib.prompts.cache import get_prompt
from src.lib.prompts.context import set_pending_prompts

logger = logging.getLogger(__name__)


def create_disease_agent() -> Agent:
    """
    Create a Disease Ontology specialist agent.

    This agent uses the Alliance Curation Database (via curation_db_sql tool)
    to query disease ontology terms from the ontologyterm table.

    Returns:
        An Agent instance configured for Disease Ontology queries
    """
    from ..tools.sql_query import create_sql_query_tool
    from ..config import get_agent_config, log_agent_config

    # Get config from registry + environment
    config = get_agent_config("disease")
    log_agent_config("Disease Specialist", config)

    # Use the Curation Database URL (same as other agents)
    database_url = os.getenv("CURATION_DB_URL")

    tools = []
    if database_url:
        # Create SQL tool pointing to curation database
        sql_tool = create_sql_query_tool(database_url, tool_name="curation_db_sql")
        tools.append(sql_tool)
        logger.info("Disease agent configured with Curation Database")
    else:
        logger.warning("CURATION_DB_URL not set - Disease agent will have limited functionality")

    # Get base prompt from cache (zero DB queries at runtime)
    base_prompt = get_prompt("disease")
    prompts_used = [base_prompt]

    # Build instructions from cached prompt
    instructions = base_prompt.content

    # Inject structured output requirement to prevent silent failures
    instructions = inject_structured_output_instruction(
        instructions,
        output_type=DiseaseResultEnvelope
    )

    if not database_url:
        instructions = "Curation Database is not configured. Unable to query disease information."

    # Build model settings using shared helper (supports both OpenAI and Gemini)
    from ..config import build_model_settings, get_model_for_agent
    effective_settings = build_model_settings(
        model=config.model,
        temperature=config.temperature,
        reasoning_effort=config.reasoning,
        tool_choice=config.tool_choice if database_url else None,
        parallel_tool_calls=True,
    )

    # Get the model (returns LitellmModel for Gemini, string for OpenAI)
    model = get_model_for_agent(config.model)

    logger.info(
        "Creating Disease agent, model=%s, prompt_v=%s",
        config.model,
        base_prompt.version,
    )

    # Log agent configuration to Langfuse for trace visibility
    from ..langfuse_client import log_agent_config
    log_agent_config(
        agent_name="Disease Specialist",
        instructions=instructions,
        model=config.model,
        tools=["curation_db_sql"] if database_url else [],
        model_settings={
            "temperature": config.temperature,
            "reasoning": config.reasoning,
            "tool_choice": config.tool_choice,
            "prompt_version": base_prompt.version,
        },
        metadata={
            "database_configured": bool(database_url)
        }
    )

    # Create the agent
    agent = Agent(
        name="Disease Specialist",
        instructions=instructions,
        model=model,  # LitellmModel for Gemini, string for OpenAI
        model_settings=effective_settings,
        tools=tools,
        output_type=DiseaseResultEnvelope,
    )

    # Register prompts for execution logging (committed when agent actually runs)
    set_pending_prompts(agent.name, prompts_used)

    return agent
