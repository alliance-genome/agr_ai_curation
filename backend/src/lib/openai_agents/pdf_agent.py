"""
PDF Q&A Agent using OpenAI Agents SDK.

This agent specializes in answering questions about PDF documents
using the Weaviate hybrid search tool for retrieval.

This module is the SINGLE SOURCE OF TRUTH for PDF agent creation.
The agent runs in isolation when called as a tool by the supervisor,
with full autonomy to make multiple tool calls internally.

Advanced features used:
- ModelSettings: Per-agent temperature and reasoning configuration
- Output guardrails: Ensure at least one tool is called before responding
"""

import logging
from typing import Optional, List, Dict, Any

from agents import Agent

from .tools.weaviate_search import (
    create_search_tool,
    create_read_section_tool,
    create_read_subsection_tool
)
from .models import Answer
from .guardrails import ToolCallTracker, create_tool_required_output_guardrail
from .prompt_utils import format_document_context_for_prompt

# Prompt cache and context tracking imports
from src.lib.prompts.cache import get_prompt
from src.lib.prompts.context import set_pending_prompts

logger = logging.getLogger(__name__)


def create_pdf_agent(
    document_id: str,
    user_id: str,
    document_name: Optional[str] = None,
    sections: Optional[List[Dict[str, Any]]] = None,
    hierarchy: Optional[Dict[str, Any]] = None,
    abstract: Optional[str] = None,
    active_groups: Optional[List[str]] = None,
) -> Agent:
    """
    Create a PDF Q&A agent configured for a specific document.

    All settings (model, temperature, reasoning) are configured via environment
    variables. See config.py for available settings.

    This agent runs in isolation when called as a tool by the supervisor.
    It has full autonomy to make multiple tool calls internally.

    Args:
        document_id: The UUID of the PDF document to search
        user_id: The user's user ID for tenant isolation
        document_name: Optional name of the document for context
        sections: Optional flat list of sections (fallback if hierarchy not available)
        hierarchy: Optional hierarchical structure from get_document_sections_hierarchical
        abstract: Optional abstract text from the paper
        active_groups: Optional list of group IDs to inject rules for (e.g., ["WB", "FB"]).
                       If provided, group-specific rules will be appended to the base prompt.
                       These rules come from config/agents/pdf/group_rules/<group>.yaml

    Returns:
        An Agent instance configured for PDF Q&A
    """
    from .config import get_agent_config, log_agent_config

    # Get config from registry + environment
    config = get_agent_config("pdf")
    log_agent_config("PDF Specialist", config)

    # Create tool call tracker for guardrail
    tracker = ToolCallTracker()

    # Create the tools bound to this document/user, with tracking
    search_tool = create_search_tool(document_id, user_id, tracker=tracker)
    read_section_tool = create_read_section_tool(document_id, user_id, tracker=tracker)
    read_subsection_tool = create_read_subsection_tool(document_id, user_id, tracker=tracker)

    # Create output guardrail to ensure at least one tool is called
    tool_required_guardrail = create_tool_required_output_guardrail(
        tracker=tracker,
        minimum_calls=1,
        error_message="You must search or read the document before answering. Use search_document or read_section first."
    )

    # Get base prompt from cache (zero DB queries at runtime)
    base_prompt = get_prompt("pdf")
    prompts_used = [base_prompt]

    # Build instructions from cached prompt
    instructions = base_prompt.content

    # Inject group-specific rules if provided
    if active_groups:
        try:
            from config.group_rules import inject_group_rules

            instructions = inject_group_rules(
                base_prompt=instructions,
                group_ids=active_groups,
                component_type="agents",
                component_name="pdf",
                prompts_out=prompts_used,  # Collect group prompts for tracking
            )
            logger.info(f"PDF agent configured with group-specific rules: {active_groups}")
        except ImportError as e:
            logger.warning(f"Could not import mod_config, skipping group injection: {e}")
        except Exception as e:
            logger.error(f"Failed to inject group rules: {e}")

    # Inject document context (hierarchy + abstract) using shared utility
    context_text, structure_info = format_document_context_for_prompt(
        hierarchy=hierarchy,
        sections=sections,
        abstract=abstract
    )
    if context_text:
        instructions += context_text

    # Customize with document name if provided
    if document_name:
        instructions = f"You are helping the user with the document: \"{document_name}\"\n\n{instructions}"

    # Build model settings using shared helper (supports both OpenAI and Gemini)
    # Enable parallel_tool_calls for faster execution (re-testing - was disabled for structured output issues)
    from .config import build_model_settings, get_model_for_agent
    effective_settings = build_model_settings(
        model=config.model,
        temperature=config.temperature,
        reasoning_effort=config.reasoning,
        tool_choice=config.tool_choice,
        parallel_tool_calls=True,  # Enabled for faster parallel searches
    )

    # Get the model (returns LitellmModel for Gemini, string for OpenAI)
    model = get_model_for_agent(config.model)

    logger.info(
        f"[OpenAI Agents] Creating PDF agent for document {document_id[:8]}... "
        f"model={config.model}, temp={config.temperature}, tool_choice={config.tool_choice}, "
        f"structure={structure_info}, guardrail=tool_required(min=1), prompt_v={base_prompt.version}"
    )

    # Log agent configuration to Langfuse for trace visibility
    from .langfuse_client import log_agent_config
    log_agent_config(
        agent_name="PDF Specialist",
        instructions=instructions,
        model=config.model,
        tools=["search_document", "read_section", "read_subsection"],
        model_settings={
            "temperature": config.temperature,
            "reasoning": config.reasoning,
            "tool_choice": config.tool_choice
        },
        metadata={
            "document_id": document_id,
            "document_name": document_name,
            "hierarchy": hierarchy,
            "sections_count": len(sections) if sections else 0,
            "structure_info": structure_info,
            "has_abstract": bool(abstract),
            "abstract_length": len(abstract) if abstract else 0,
            "prompt_version": base_prompt.version,
            "active_groups": active_groups,  # Log which groups are active
        }
    )

    # Create the agent
    agent = Agent(
        name="PDF Specialist",
        instructions=instructions,
        model=model,  # LitellmModel for Gemini, string for OpenAI
        model_settings=effective_settings,
        tools=[search_tool, read_section_tool, read_subsection_tool],
        output_type=Answer,
        output_guardrails=[tool_required_guardrail],
    )

    # Register prompts for execution logging (committed when agent actually runs)
    set_pending_prompts(agent.name, prompts_used)

    return agent
