"""Thread-local prompt tracking using ContextVars.

This module provides request-scoped tracking of prompts for execution logging.
The key insight is that logging happens at EXECUTION time, NOT creation time.

The supervisor eagerly creates all specialist agents before routing, so logging
at creation would overcount (prompts logged for agents that never run). Instead:

1. Agent factories store prompts via set_pending_prompts(agent.name, prompts)
2. Execution wrappers call commit_pending_prompts(agent.name) when agent runs
3. After all execution, runner calls get_used_prompts() and logs them

Usage:
    # In agent factory
    from src.lib.prompts.context import set_pending_prompts
    set_pending_prompts("Gene Specialist", [base_prompt, mod_rule_prompt])

    # In execution wrapper (when agent actually runs)
    from src.lib.prompts.context import commit_pending_prompts
    commit_pending_prompts(agent.name)

    # After all execution (in runner)
    from src.lib.prompts.context import get_used_prompts, clear_prompt_context
    used_prompts = get_used_prompts()
    # ... log prompts ...
    clear_prompt_context()
"""

from contextvars import ContextVar
from dataclasses import dataclass
from typing import List, Dict, Optional

from src.models.sql.prompts import PromptTemplate

# Thread-safe storage: agent_name -> list of prompts pending execution
_pending_prompts: ContextVar[Optional[Dict[str, List[PromptTemplate]]]] = ContextVar(
    "pending_prompts", default=None
)

# Prompts that were actually used (logged after execution)
# Note: Uses default=None pattern (matching langfuse_client.py) to avoid shared mutable state
_used_prompts: ContextVar[Optional[List[PromptTemplate]]] = ContextVar(
    "used_prompts", default=None
)


@dataclass
class PromptOverride:
    """Full system-prompt replacement for a specific agent in this request context."""

    content: str
    agent_name: str
    custom_agent_id: str
    mod_overrides: Optional[Dict[str, str]] = None


prompt_override_var: ContextVar[Optional[PromptOverride]] = ContextVar(
    "prompt_override", default=None
)


def set_prompt_override(override: PromptOverride) -> None:
    """Set the current request's prompt override."""
    prompt_override_var.set(override)


def clear_prompt_override() -> None:
    """Clear any active prompt override for this request."""
    prompt_override_var.set(None)


def get_prompt_override() -> Optional[PromptOverride]:
    """Get the active prompt override if one is set."""
    return prompt_override_var.get(None)


def _get_pending() -> Dict[str, List[PromptTemplate]]:
    """Get or initialize the pending prompts dict."""
    pending = _pending_prompts.get()
    if pending is None:
        pending = {}
        _pending_prompts.set(pending)
    return pending


def set_pending_prompts(agent_name: str, prompts: List[PromptTemplate]) -> None:
    """Called by agent factories to register prompts for potential logging.

    Args:
        agent_name: The Agent.name value (e.g., "Gene Specialist", "PDF Specialist")
        prompts: List of PromptTemplate objects (base prompt + any MOD rules)
    """
    pending = _get_pending().copy()
    pending[agent_name] = list(prompts)
    _pending_prompts.set(pending)


def _get_used() -> List[PromptTemplate]:
    """Get or initialize the used prompts list."""
    used = _used_prompts.get()
    if used is None:
        used = []
        _used_prompts.set(used)
    return used


def commit_pending_prompts(agent_name: str) -> None:
    """Called by execution wrappers when agent actually executes.

    Moves prompts from pending to used. Strict audit trail: log every
    invocation (no de-dupe). If the same agent runs twice, its prompts
    are logged twice.

    Args:
        agent_name: The Agent.name value (must match what was passed to set_pending_prompts)
    """
    pending = _get_pending()
    if agent_name in pending:
        used = list(_get_used())
        used.extend(pending[agent_name])
        _used_prompts.set(used)


def get_used_prompts() -> List[PromptTemplate]:
    """Called by runner after execution to get all prompts used.

    Returns:
        List of PromptTemplate objects that were actually used in this request.
    """
    return list(_get_used())


def clear_prompt_context() -> None:
    """Called at start of request to reset.

    Should be called at the beginning of each request to ensure clean state.
    """
    _pending_prompts.set({})
    _used_prompts.set([])
    prompt_override_var.set(None)


def get_pending_for_agent(agent_name: str) -> Optional[List[PromptTemplate]]:
    """Get pending prompts for a specific agent (for testing/debugging).

    Args:
        agent_name: The Agent.name value

    Returns:
        List of pending prompts for the agent, or None if not found.
    """
    pending = _get_pending()
    return pending.get(agent_name)
