"""Flow execution engine for curation flows.

This module provides functions to execute user-defined agent workflows with
tool restriction based on flow membership.

Main entry points:
    - execute_flow: Async generator for streaming flow execution
    - create_flow_supervisor: Creates a supervisor agent configured for a flow

Helpers:
    - is_agent_in_flow: Check if an agent is part of a flow
    - get_flow_agent_ids: Get the set of agent IDs used in a flow
    - get_all_agent_tools: Get all agent transfer tools with is_enabled based on flow
    - build_supervisor_instructions: Build system prompt for flow execution
    - build_flow_prompt: Build initial execution prompt
"""

from .executor import (
    execute_flow,
    create_flow_supervisor,
    is_agent_in_flow,
    get_flow_agent_ids,
    get_all_agent_tools,
    build_supervisor_instructions,
    build_flow_prompt,
)

__all__ = [
    "execute_flow",
    "create_flow_supervisor",
    "is_agent_in_flow",
    "get_flow_agent_ids",
    "get_all_agent_tools",
    "build_supervisor_instructions",
    "build_flow_prompt",
]
