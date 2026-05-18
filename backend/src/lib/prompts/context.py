"""Thread-local prompt tracking using ContextVars.

This module provides request-scoped tracking of prompts for execution logging.
The key insight is that logging happens at EXECUTION time, NOT creation time.

The supervisor eagerly creates all specialist agents before routing, so logging
at creation would overcount (prompts logged for agents that never run). Instead:

1. Agent factories store prompts via set_pending_prompts(agent.name, prompts)
   and bind the returned run id to the created Agent object.
2. Execution wrappers call commit_pending_prompts(agent) when agent runs
3. After all execution, runner calls get_used_prompts() and logs them

Usage:
    # In agent factory
    from src.lib.prompts.context import set_pending_prompts
    prompt_run_id = set_pending_prompts("Gene Specialist", [base_prompt, group_rule_prompt])
    bind_prompt_run(agent, prompt_run_id)

    # In execution wrapper (when agent actually runs)
    from src.lib.prompts.context import commit_pending_prompts
    commit_pending_prompts(agent)

    # After all execution (in runner)
    from src.lib.prompts.context import get_used_prompts, clear_prompt_context
    used_prompts = get_used_prompts()
    # ... log prompts ...
    clear_prompt_context()
"""

import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, List, Dict, Optional

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


@dataclass(frozen=True)
class PromptAssemblyMetadata:
    """Effective prompt assembly metadata for one runtime agent execution."""

    effective_prompt_hash: str
    layer_manifest: Dict[str, Any]


@dataclass(frozen=True)
class PromptRun:
    """Prompt templates and effective assembly metadata for one agent run."""

    agent_name: str
    prompts: List[PromptTemplate]
    assembly: Optional[PromptAssemblyMetadata] = None


_PROMPT_RUN_ID_ATTR = "_agr_prompt_run_id"

_pending_prompt_runs: ContextVar[Optional[Dict[str, PromptRun]]] = ContextVar(
    "pending_prompt_runs", default=None
)
_pending_prompt_run_ids_by_agent: ContextVar[Optional[Dict[str, List[str]]]] = ContextVar(
    "pending_prompt_run_ids_by_agent", default=None
)
_prompt_run_ids_by_object_id: ContextVar[Optional[Dict[int, str]]] = ContextVar(
    "prompt_run_ids_by_object_id", default=None
)
_used_prompt_runs: ContextVar[Optional[List[PromptRun]]] = ContextVar(
    "used_prompt_runs", default=None
)


@dataclass(init=False)
class PromptOverride:
    """Full system-prompt replacement for a specific agent in this request context."""

    content: str
    agent_name: str
    custom_agent_id: str
    group_overrides: Optional[Dict[str, str]] = None

    def __init__(
        self,
        content: str,
        agent_name: str,
        custom_agent_id: str,
        group_overrides: Optional[Dict[str, str]] = None,
        mod_overrides: Optional[Dict[str, str]] = None,
    ) -> None:
        self.content = content
        self.agent_name = agent_name
        self.custom_agent_id = custom_agent_id
        self.group_overrides = group_overrides if group_overrides is not None else mod_overrides
        self.mod_overrides = self.group_overrides


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


def _get_pending_runs() -> Dict[str, PromptRun]:
    """Get or initialize the pending prompt runs dict."""
    pending = _pending_prompt_runs.get()
    if pending is None:
        pending = {}
        _pending_prompt_runs.set(pending)
    return pending


def _get_pending_run_ids_by_agent() -> Dict[str, List[str]]:
    """Get or initialize the agent-name to pending run id index."""
    pending = _pending_prompt_run_ids_by_agent.get()
    if pending is None:
        pending = {}
        _pending_prompt_run_ids_by_agent.set(pending)
    return pending


def _get_prompt_run_ids_by_object_id() -> Dict[int, str]:
    """Get or initialize the Agent object identity to pending run id index."""
    pending = _prompt_run_ids_by_object_id.get()
    if pending is None:
        pending = {}
        _prompt_run_ids_by_object_id.set(pending)
    return pending


def bind_prompt_run(agent: Any, prompt_run_id: Optional[str]) -> Any:
    """Bind a prompt run id to an Agent-like object and return the object."""

    if prompt_run_id:
        object_ids = _get_prompt_run_ids_by_object_id().copy()
        object_ids[id(agent)] = prompt_run_id
        _prompt_run_ids_by_object_id.set(object_ids)
        try:
            setattr(agent, _PROMPT_RUN_ID_ATTR, prompt_run_id)
        except (AttributeError, TypeError, ValueError):
            pass
    return agent


def _resolve_prompt_run_id(agent_or_name: Any) -> Optional[str]:
    """Resolve the unique pending prompt run id for an Agent-like object."""

    prompt_run_id = getattr(agent_or_name, _PROMPT_RUN_ID_ATTR, None)
    if prompt_run_id:
        return str(prompt_run_id)
    if not isinstance(agent_or_name, str):
        prompt_run_id = _get_prompt_run_ids_by_object_id().get(id(agent_or_name))
        if prompt_run_id:
            return prompt_run_id

    if isinstance(agent_or_name, str):
        agent_name = agent_or_name
    else:
        agent_name = getattr(agent_or_name, "name", None)
    if not agent_name:
        return None

    pending_ids = _get_pending_run_ids_by_agent().get(str(agent_name), [])
    if not pending_ids:
        return None
    if len(pending_ids) > 1:
        raise ValueError(
            f"Prompt run for agent '{agent_name}' is ambiguous; commit using the Agent instance."
        )
    return pending_ids[0]


def set_pending_prompts(
    agent_name: str,
    prompts: List[PromptTemplate],
    *,
    effective_prompt_hash: Optional[str] = None,
    layer_manifest: Optional[Dict[str, Any]] = None,
) -> str:
    """Called by agent factories to register prompts for potential logging.

    Args:
        agent_name: The Agent.name value (e.g., "Gene Specialist", "PDF Specialist")
        prompts: List of PromptTemplate objects (base prompt + any group rules)
        effective_prompt_hash: Hash of the final assembled prompt, when available
        layer_manifest: Structured layer manifest for the final assembled prompt
    """
    prompt_run_id = f"{agent_name}:{uuid.uuid4().hex}"

    pending = _get_pending().copy()
    pending[agent_name] = list(prompts)
    _pending_prompts.set(pending)

    run_pending = _get_pending_runs().copy()
    assembly = None
    if effective_prompt_hash and layer_manifest is not None:
        assembly = PromptAssemblyMetadata(
            effective_prompt_hash=effective_prompt_hash,
            layer_manifest=dict(layer_manifest),
        )
    run_pending[prompt_run_id] = PromptRun(
        agent_name=agent_name,
        prompts=list(prompts),
        assembly=assembly,
    )
    _pending_prompt_runs.set(run_pending)

    run_ids_by_agent = {
        name: list(run_ids)
        for name, run_ids in _get_pending_run_ids_by_agent().items()
    }
    run_ids_by_agent.setdefault(agent_name, []).append(prompt_run_id)
    _pending_prompt_run_ids_by_agent.set(run_ids_by_agent)

    return prompt_run_id


def set_pending_prompt_assembly(
    agent_or_name: Any,
    *,
    effective_prompt_hash: str,
    layer_manifest: Dict[str, Any],
) -> None:
    """Attach effective prompt assembly metadata to a pending agent run."""

    prompt_run_id = _resolve_prompt_run_id(agent_or_name)
    if prompt_run_id is None:
        return

    agent_name = str(getattr(agent_or_name, "name", agent_or_name))
    pending_prompts = _get_pending()
    run_pending = _get_pending_runs().copy()
    current = run_pending.get(prompt_run_id)
    prompts = list(current.prompts if current else pending_prompts.get(agent_name, []))
    run_pending[prompt_run_id] = PromptRun(
        agent_name=agent_name,
        prompts=prompts,
        assembly=PromptAssemblyMetadata(
            effective_prompt_hash=effective_prompt_hash,
            layer_manifest=dict(layer_manifest),
        ),
    )
    _pending_prompt_runs.set(run_pending)


def append_pending_prompt_runtime_context(
    agent_or_name: Any,
    *,
    layer_id_suffix: str,
    title: str,
    content: str,
    source_ref: str,
) -> None:
    """Append runtime prompt content to a pending run's assembly metadata."""

    from src.lib.prompts.assembly import append_runtime_context_to_manifest

    prompt_run_id = _resolve_prompt_run_id(agent_or_name)
    if prompt_run_id is None:
        return

    run_pending = _get_pending_runs().copy()
    current = run_pending.get(prompt_run_id)
    if current is None or current.assembly is None:
        return

    layer_manifest = append_runtime_context_to_manifest(
        current.assembly.layer_manifest,
        layer_id_suffix=layer_id_suffix,
        title=title,
        content=content,
        source_ref=source_ref,
    )
    run_pending[prompt_run_id] = PromptRun(
        agent_name=current.agent_name,
        prompts=list(current.prompts),
        assembly=PromptAssemblyMetadata(
            effective_prompt_hash=str(layer_manifest["hash"]),
            layer_manifest=layer_manifest,
        ),
    )
    _pending_prompt_runs.set(run_pending)


def _get_used() -> List[PromptTemplate]:
    """Get or initialize the used prompts list."""
    used = _used_prompts.get()
    if used is None:
        used = []
        _used_prompts.set(used)
    return used


def _get_used_runs() -> List[PromptRun]:
    """Get or initialize the used prompt runs list."""
    used = _used_prompt_runs.get()
    if used is None:
        used = []
        _used_prompt_runs.set(used)
    return used


def commit_pending_prompts(agent_or_name: Any) -> None:
    """Called by execution wrappers when agent actually executes.

    Moves prompts from pending to used. Strict audit trail: log every
    invocation (no de-dupe). If the same agent runs twice, its prompts
    are logged twice.

    Args:
        agent_or_name: The Agent object with a bound prompt run id.
    """
    prompt_run_id = _resolve_prompt_run_id(agent_or_name)
    agent_name = str(getattr(agent_or_name, "name", agent_or_name))

    pending_runs = _get_pending_runs()
    pending_run = pending_runs.get(prompt_run_id) if prompt_run_id else None

    pending = _get_pending()
    if pending_run is not None:
        used = list(_get_used())
        used.extend(pending_run.prompts)
        _used_prompts.set(used)
    elif agent_name in pending:
        used = list(_get_used())
        used.extend(pending[agent_name])
        _used_prompts.set(used)

    if pending_run is not None:
        used_runs = list(_get_used_runs())
        used_runs.append(pending_run)
        _used_prompt_runs.set(used_runs)


def get_used_prompts() -> List[PromptTemplate]:
    """Called by runner after execution to get all prompts used.

    Returns:
        List of PromptTemplate objects that were actually used in this request.
    """
    return list(_get_used())


def get_used_prompt_runs() -> List[PromptRun]:
    """Return prompt usage grouped by agent execution."""

    return list(_get_used_runs())


def clear_prompt_context() -> None:
    """Called at start of request to reset.

    Should be called at the beginning of each request to ensure clean state.
    """
    _pending_prompts.set({})
    _used_prompts.set([])
    _pending_prompt_runs.set({})
    _pending_prompt_run_ids_by_agent.set({})
    _prompt_run_ids_by_object_id.set({})
    _used_prompt_runs.set([])
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
