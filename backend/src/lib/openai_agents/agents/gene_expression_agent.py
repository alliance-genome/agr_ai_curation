"""
Gene Expression Agent using OpenAI Agents SDK.

This agent specializes in extracting gene expression patterns from research papers,
including spatial location, temporal information, reagent details, and negative evidence.

DESIGN NOTE (Extraction vs Formatting Split):
This agent focuses ONLY on extraction - finding and organizing expression data.
It returns plain text output, NOT structured JSON.

The Formatter Agent (formatter_agent.py) handles text-to-JSON conversion separately.
This split solves GPT-5 + reasoning mode's tendency to fail on structured output
when also doing complex extraction work.

Enhanced version with full PDF extraction capabilities:
- search_document: Semantic search over PDF chunks
- read_section: Read entire top-level sections
- read_subsection: Read specific subsections
- agr_curation_query: Gene ID validation
"""

import logging
from typing import Optional, List, Dict, Any

from agents import Agent

from ..prompt_utils import format_document_context_for_prompt

# Prompt cache and context tracking imports
from src.lib.prompts.cache import get_prompt
from src.lib.prompts.context import set_pending_prompts

logger = logging.getLogger(__name__)


def create_gene_expression_agent(
    document_id: str,
    user_id: str,
    document_name: Optional[str] = None,
    sections: Optional[List[Dict[str, Any]]] = None,
    hierarchy: Optional[Dict[str, Any]] = None,
    abstract: Optional[str] = None,
    active_groups: Optional[List[str]] = None,
) -> Agent:
    """
    Create a Gene Expression specialist agent with full PDF extraction capabilities.

    All settings configured via environment variables. See config.py.

    This agent runs in isolation when called as a tool by the supervisor.
    It has full autonomy to make multiple tool calls internally.

    Args:
        document_id: The UUID of the PDF document to search
        user_id: The user's ID for tenant isolation
        document_name: Optional name of the document for context
        sections: Optional flat list of sections (fallback if hierarchy not available)
        hierarchy: Optional hierarchical structure from get_document_sections_hierarchical
        abstract: Optional abstract text from the paper
        active_groups: Optional list of group IDs to inject rules for (e.g., ["WB", "FB"]).
                       If provided, group-specific rules will be appended to the base prompt.
                       These rules come from config/agents/gene_expression/group_rules/<group>.yaml

    Returns:
        An Agent instance configured for Gene Expression extraction
    """
    from ..tools.weaviate_search import (
        create_search_tool,
        create_read_section_tool,
        create_read_subsection_tool,
    )
    from ..tools.agr_curation import agr_curation_query
    from ..config import get_agent_config
    from ..guardrails import ToolCallTracker, create_tool_required_output_guardrail

    # Get config from registry + environment
    config = get_agent_config("gene_expression")

    # Get base prompt from cache (zero DB queries at runtime)
    base_prompt = get_prompt("gene_expression")
    prompts_used = [base_prompt]

    # Create tool call tracker for guardrail
    tracker = ToolCallTracker()

    # Create all tools bound to this document/user, with tracking
    search_tool = create_search_tool(document_id, user_id, tracker=tracker)
    read_section_tool = create_read_section_tool(document_id, user_id, tracker=tracker)
    read_subsection_tool = create_read_subsection_tool(document_id, user_id, tracker=tracker)

    # Create output guardrail to ensure at least one tool is called
    tool_required_guardrail = create_tool_required_output_guardrail(
        tracker=tracker,
        minimum_calls=1,
        error_message="You must search or read the document before extracting expression patterns. Use search_document, read_section, or read_subsection first."
    )

    # Build instructions from cached prompt
    instructions = base_prompt.content

    # NOTE: No structured output injection - this agent returns plain text
    # The Formatter Agent handles text-to-JSON conversion separately

    # Inject group-specific rules if provided
    if active_groups:
        try:
            from config.group_rules import inject_group_rules

            instructions = inject_group_rules(
                base_prompt=instructions,
                group_ids=active_groups,
                component_type="agents",
                component_name="gene_expression",
                prompts_out=prompts_used,  # Collect group prompts for tracking
            )
            logger.info('Gene Expression agent configured with group-specific rules: %s', active_groups)
        except ImportError as e:
            logger.warning('Could not import mod_config, skipping group injection: %s', e)
        except Exception as e:
            logger.error('Failed to inject group rules: %s', e)

    # Inject document context (hierarchy + abstract) using shared utility
    context_text, structure_info = format_document_context_for_prompt(
        hierarchy=hierarchy,
        sections=sections,
        abstract=abstract
    )
    if context_text:
        instructions += context_text

    # Add document context
    if document_name:
        instructions = f"You are analyzing the document: \"{document_name}\"\n\n{instructions}"

    # Build model settings using shared helper (supports both OpenAI and Gemini)
    # Enable parallel_tool_calls for faster execution - this agent returns plain text
    # Add verbosity="low" to fix structured output + reasoning issues
    from ..config import build_model_settings, get_model_for_agent
    effective_settings = build_model_settings(
        model=config.model,
        temperature=config.temperature,
        reasoning_effort=config.reasoning,
        tool_choice=config.tool_choice,
        parallel_tool_calls=True,
        verbosity="low",  # Fix for structured output + reasoning
    )

    # Get the model (returns LitellmModel for Gemini, string for OpenAI)
    model = get_model_for_agent(config.model)

    logger.info(
        "Creating Gene Expression agent, model=%s prompt_v=%s structure=%s",
        config.model,
        base_prompt.version,
        structure_info,
        extra={"document_id": document_id[:8]},
    )

    # Log agent configuration to Langfuse for trace visibility
    from ..langfuse_client import log_agent_config
    log_agent_config(
        agent_name="Gene Expression Specialist",
        instructions=instructions,
        model=config.model,
        tools=["search_document", "read_section", "read_subsection", "agr_curation_query"],
        model_settings={
            "temperature": config.temperature,
            "reasoning": config.reasoning,
            "tool_choice": config.tool_choice,
            "prompt_version": base_prompt.version,
            "active_groups": active_groups,  # Log which groups are active
        },
        metadata={
            "document_id": document_id,
            "document_name": document_name,
            "hierarchy": hierarchy,
            "sections_count": len(sections) if sections else 0,
            "structure_info": structure_info,
            "has_abstract": bool(abstract),
            "abstract_length": len(abstract) if abstract else 0
        }
    )

    # NOTE: No output_type - this agent returns plain text
    # The Formatter Agent handles text-to-JSON conversion separately
    agent = Agent(
        name="Gene Expression Specialist",
        instructions=instructions,
        model=model,  # LitellmModel for Gemini, string for OpenAI
        model_settings=effective_settings,
        tools=[search_tool, read_section_tool, read_subsection_tool, agr_curation_query],
        output_guardrails=[tool_required_guardrail],
    )

    # Register prompts for execution logging (committed when agent actually runs)
    set_pending_prompts(agent.name, prompts_used)

    return agent
