"""Shared tool-surface compiler for OpenAI hosted tool search (ALL-1280).

Every runtime hands its final, already-authorized and already run-state-bound
tool list to :func:`compile_tool_surface` (usually through
:func:`apply_tool_surface`) immediately before the SDK run starts. The
compiler only *partitions* that list into eager and deferred definitions; it
never adds, drops or re-authorizes a tool. Invariants (a violation raises
:class:`ToolSurfaceError`):

- the declared (application) tool names of the output equal the input names;
- deferred tools are function tools placed in a declared namespace, each
  namespace holds at most ``TOOL_SURFACE_NAMESPACE_MAX_FUNCTIONS`` functions
  and carries exactly one description;
- forced, required and terminal (``finalize_*``) tools are always eager;
- ``ToolSearchTool(execution="server")`` is added only when something is
  deferred (client-executed search is not supported here);
- ordering is deterministic: ``[tool_search, *eager, *deferred]`` with eager
  tools in input order and namespaces in order of first appearance.

An eager surface (policy ``mode: eager``, or a deferred policy on a provider
without hosted tool search) returns the input list unchanged, so eager
runtimes send byte-identical tool payloads.

Hosted search reports loaded/called functions under qualified wire names
(``namespace.tool``); :func:`canonical_tool_name` maps them back to the
application tool name for ledgers, guardrails, events and measurement.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from agents import FunctionTool, ToolSearchTool, tool_namespace

logger = logging.getLogger(__name__)

MODE_DEFERRED = "deferred"
MODE_EAGER_POLICY = "eager_policy"
MODE_EAGER_PROVIDER_UNSUPPORTED = "eager_provider_unsupported"
TERMINAL_TOOL_PREFIX = "finalize_"
# Validators and supervisors keep every tool visible (ALL-1280 decision);
# startup validation rejects a deferred policy for them.
NEVER_DEFERRED_RUNTIMES = frozenset({"validator", "chat_supervisor", "flow_supervisor"})
NEVER_DEFERRED_AGENT_CATEGORIES = frozenset({"Validation", "Routing"})

NamespaceResolver = Callable[[str], "tuple[str, str] | None"]

_unsupported_logged: set[tuple[str, str]] = set()
_unsupported_logged_lock = threading.Lock()


class ToolSurfaceError(ValueError):
    """The tool surface cannot be compiled without breaking an invariant."""


class ToolGroupCapError(ToolSurfaceError):
    """A saved custom agent has more tools in one group than the cap allows.

    The message is curator-facing (see :func:`tool_group_cap_message`); the
    runtime, agent and per-group tool lists are kept for logs and events.
    """

    def __init__(
        self,
        message: str,
        *,
        runtime: str,
        agent_key: str,
        oversized: Mapping[str, list[str]],
    ) -> None:
        super().__init__(message)
        self.runtime = runtime
        self.agent_key = agent_key
        self.oversized = dict(oversized)


def tool_group_cap_message(namespace: str, tool_count: int, namespace_max: int) -> str:
    """Curator-facing explanation of a tool group over the per-agent cap."""

    group = namespace.replace("_", " ")
    return (
        f"This agent has {tool_count} tools from the '{group}' group, and custom agents "
        f"can use at most {namespace_max} tools from one group. Please contact the AI "
        "Curation developers for help setting up this agent."
    )


class ToolSurfaceConfigurationError(RuntimeError):
    """Namespace or loading-policy configuration is invalid (startup hard-fail)."""


def canonical_tool_name(name: Any) -> str:
    """Return the application tool name for a wire name.

    Hosted tool search reports deferred functions as ``namespace.tool``;
    application tool names never contain dots, so the last segment is the
    registered tool name.
    """

    text = str(name or "").strip()
    return text.rsplit(".", 1)[-1] if "." in text else text


def is_terminal_tool_name(name: str) -> bool:
    """Terminal/finalization tools must stay eager and outside namespaces."""

    return canonical_tool_name(name).startswith(TERMINAL_TOOL_PREFIX)


def _tool_name(tool: Any) -> str:
    return str(getattr(tool, "name", None) or getattr(tool, "tool_name", None) or "").strip()


def _definition(tool: Any) -> dict[str, Any]:
    if isinstance(tool, FunctionTool):
        return {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.params_json_schema,
            "strict": tool.strict_json_schema,
            "defer_loading": bool(tool.defer_loading),
            "namespace": getattr(tool, "_tool_namespace", None),
            "namespace_description": getattr(tool, "_tool_namespace_description", None),
        }
    if isinstance(tool, ToolSearchTool):
        return {"type": "tool_search", "execution": tool.execution}
    return {"type": type(tool).__name__, "name": _tool_name(tool)}


def _chars(value: Any) -> int:
    return len(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")))


@dataclass
class ToolSurface:
    """One compiled surface plus the run-level tool-search observations."""

    tools: list[Any]
    runtime: str
    agent_key: str
    mode: str
    declared_names: tuple[str, ...]
    eager_names: tuple[str, ...]
    deferred_names: tuple[str, ...]
    namespace_names: tuple[str, ...]
    fingerprint: str
    eager_chars: int
    deferred_chars: int
    namespace_header_chars: int
    source_tools: list[Any] = field(default_factory=list, repr=False)
    namespace_descriptions: dict[str, str] = field(default_factory=dict)
    loaded_names: set[str] = field(default_factory=set)
    called_names: list[str] = field(default_factory=list)
    searches: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def counts(self) -> dict[str, int]:
        return {
            "candidate_count": len(self.declared_names),
            "eager_count": len(self.eager_names),
            "deferred_count": len(self.deferred_names),
            "namespace_count": len(self.namespace_names),
        }

    def observe_response_output(self, output_items: Iterable[Any] | None) -> None:
        """Record hosted searches, loaded definitions and function calls."""

        from src.lib.openai_agents.model_request_measurement import (
            flatten_loaded_tool_definitions,
        )

        with self._lock:
            for raw_item in list(output_items or []):
                item = _mapping(raw_item)
                item_type = str(item.get("type") or "")
                if item_type == "tool_search_call":
                    self.searches += 1
                elif item_type == "tool_search_output":
                    for definition in flatten_loaded_tool_definitions(item.get("tools")):
                        name = canonical_tool_name(_mapping(definition).get("name"))
                        if name:
                            self.loaded_names.add(name)
                elif item_type == "function_call":
                    name = canonical_tool_name(item.get("name"))
                    if name:
                        self.called_names.append(name)

    def summary(self) -> dict[str, Any]:
        """Run-level surface record (cumulative up to the latest response)."""

        with self._lock:
            called = sorted(set(self.called_names))
            loaded = sorted(self.loaded_names)
            return {
                "runtime": self.runtime,
                "agent_key": self.agent_key,
                "mode": self.mode,
                "fingerprint": self.fingerprint,
                **self.counts,
                "eager_chars": self.eager_chars,
                "deferred_chars": self.deferred_chars,
                "namespace_header_chars": self.namespace_header_chars,
                "declared_names": list(self.declared_names),
                "deferred_names": list(self.deferred_names),
                "namespaces": list(self.namespace_names),
                "searches": self.searches,
                "loaded_names": loaded,
                "called_names": called,
                "loaded_not_called": sorted(set(loaded) - set(called)),
            }


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json", exclude_none=True)
        if isinstance(dumped, Mapping):
            return dumped
    return {}


def _fingerprint(mode: str, tools: Sequence[Any]) -> str:
    payload = json.dumps(
        {"mode": mode, "tools": [_definition(tool) for tool in tools]},
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _log_unsupported_once(runtime: str, agent_key: str, *, model: str | None, provider: str | None) -> None:
    key = (runtime, agent_key)
    with _unsupported_logged_lock:
        if key in _unsupported_logged:
            return
        _unsupported_logged.add(key)
    logger.warning(
        "Deferred tool loading policy for %s agent %s runs eagerly: provider %s / model %s "
        "does not support hosted tool search (mode=%s)",
        runtime,
        agent_key,
        provider,
        model,
        MODE_EAGER_PROVIDER_UNSUPPORTED,
        extra={
            "tool_surface_mode": MODE_EAGER_PROVIDER_UNSUPPORTED,
            "tool_surface_runtime": runtime,
            "agent_key": agent_key,
            "provider": provider,
            "model": model,
        },
    )


def compile_tool_surface(
    tools: Sequence[Any],
    *,
    runtime: str,
    agent_key: str,
    policy: Any,
    supports_tool_search: bool,
    namespace_resolver: NamespaceResolver,
    forced_tool_names: Iterable[str] = (),
    required_tool_names: Iterable[str] = (),
    model: str | None = None,
    provider: str | None = None,
) -> ToolSurface:
    """Partition an authorized tool list into the provider-facing surface."""

    input_tools = list(tools)
    declared_names = tuple(_tool_name(tool) for tool in input_tools)
    if any(not name for name in declared_names):
        raise ToolSurfaceError(f"{runtime} agent {agent_key} has a tool without a name")
    duplicates = sorted({name for name in declared_names if declared_names.count(name) > 1})
    if duplicates:
        raise ToolSurfaceError(
            f"{runtime} agent {agent_key} declares duplicate tools: {', '.join(duplicates)}"
        )
    for tool in input_tools:
        if isinstance(tool, ToolSearchTool) or (
            isinstance(tool, FunctionTool)
            and (tool.defer_loading or getattr(tool, "_tool_namespace", None))
        ):
            raise ToolSurfaceError(
                f"{runtime} agent {agent_key} tool '{_tool_name(tool)}' is already "
                "compiled; the surface compiler must receive plain tools"
            )

    def eager_surface(mode: str) -> ToolSurface:
        chars = sum(_chars(_definition(tool)) for tool in input_tools)
        return ToolSurface(
            tools=input_tools,
            runtime=runtime,
            agent_key=agent_key,
            mode=mode,
            declared_names=declared_names,
            eager_names=declared_names,
            deferred_names=(),
            namespace_names=(),
            fingerprint=_fingerprint(mode, input_tools),
            eager_chars=chars,
            deferred_chars=0,
            namespace_header_chars=0,
            source_tools=input_tools,
        )

    if policy.mode == "eager":
        return eager_surface(MODE_EAGER_POLICY)
    if not supports_tool_search:
        if policy.on_unsupported_provider == "fail":
            raise ToolSurfaceError(
                f"{runtime} agent {agent_key} requires hosted tool search, but provider "
                f"{provider} / model {model} does not support it "
                "(on_unsupported_provider: fail)"
            )
        _log_unsupported_once(runtime, agent_key, model=model, provider=provider)
        return eager_surface(MODE_EAGER_PROVIDER_UNSUPPORTED)

    from src.lib.openai_agents.config import get_tool_surface_namespace_max_functions

    namespace_max = get_tool_surface_namespace_max_functions()
    always_eager = (
        set(policy.eager_tools)
        | {canonical_tool_name(name) for name in forced_tool_names if name}
        | {canonical_tool_name(name) for name in required_tool_names if name}
    )
    allowed_namespaces = (
        set(policy.deferred_namespaces) if policy.deferred_namespaces is not None else None
    )

    eager: list[Any] = []
    grouped: dict[str, list[FunctionTool]] = {}
    descriptions: dict[str, str] = {}
    for tool, name in zip(input_tools, declared_names):
        if (
            not isinstance(tool, FunctionTool)
            or name in always_eager
            or is_terminal_tool_name(name)
        ):
            eager.append(tool)
            continue
        namespace = namespace_resolver(name)
        if namespace is None or (
            allowed_namespaces is not None and namespace[0] not in allowed_namespaces
        ):
            eager.append(tool)
            continue
        namespace_name, namespace_description = namespace
        known_description = descriptions.setdefault(namespace_name, namespace_description)
        if known_description != namespace_description:
            raise ToolSurfaceError(
                f"Namespace '{namespace_name}' has more than one description"
            )
        deferred_tool = copy.copy(tool)
        deferred_tool.defer_loading = True
        grouped.setdefault(namespace_name, []).append(deferred_tool)

    oversized = {
        name: [tool.name for tool in members]
        for name, members in sorted(grouped.items())
        if len(members) > namespace_max
    }
    if oversized:
        # Startup validation keeps packaged agents under the cap, so only a
        # saved custom agent can reach this; its curator needs developer help.
        logger.error(
            "%s agent %s places more than %s tools in namespace(s) %s "
            "(TOOL_SURFACE_NAMESPACE_MAX_FUNCTIONS)",
            runtime,
            agent_key,
            namespace_max,
            oversized,
            extra={"runtime": runtime, "agent_key": agent_key, "operation": "tool_group_cap"},
        )
        raise ToolGroupCapError(
            " ".join(
                tool_group_cap_message(name, len(members), namespace_max)
                for name, members in oversized.items()
            ),
            runtime=runtime,
            agent_key=agent_key,
            oversized=oversized,
        )

    deferred: list[FunctionTool] = []
    for namespace_name, members in grouped.items():
        deferred.extend(
            tool_namespace(
                name=namespace_name,
                description=descriptions[namespace_name],
                tools=members,
            )
        )

    compiled: list[Any] = [*eager, *deferred]
    if deferred:
        compiled.insert(0, ToolSearchTool(execution="server"))
    compiled_names = tuple(
        _tool_name(tool) for tool in compiled if not isinstance(tool, ToolSearchTool)
    )
    if sorted(compiled_names) != sorted(declared_names):
        raise ToolSurfaceError(
            f"{runtime} agent {agent_key} surface changed its declared tools"
        )
    mode = MODE_DEFERRED if deferred else MODE_EAGER_POLICY
    return ToolSurface(
        tools=compiled,
        runtime=runtime,
        agent_key=agent_key,
        mode=mode,
        declared_names=declared_names,
        eager_names=tuple(_tool_name(tool) for tool in eager),
        deferred_names=tuple(tool.name for tool in deferred),
        namespace_names=tuple(grouped),
        fingerprint=_fingerprint(mode, compiled),
        eager_chars=sum(_chars(_definition(tool)) for tool in eager),
        deferred_chars=sum(_chars(_definition(tool)) for tool in deferred),
        namespace_header_chars=sum(
            _chars({"name": name, "description": descriptions[name]}) for name in grouped
        ),
        source_tools=input_tools,
        namespace_descriptions=dict(descriptions),
    )


def deferred_tools_note(surface: ToolSurface) -> str:
    """Runtime note naming the tool groups a deferred surface loads on demand."""

    members: dict[str, list[str]] = {}
    for tool in surface.tools:
        namespace = getattr(tool, "_tool_namespace", None)
        if namespace:
            members.setdefault(namespace, []).append(tool.name)
    lines = [
        "## Tools loaded on demand",
        "To keep your tool list short, the tool groups below are hidden until you "
        "load them with tool search. Tools you can already see are always available. "
        "When a step needs a tool you cannot see, search for its group, then call "
        "the tool.",
    ]
    for namespace in surface.namespace_names:
        lines.append(
            f"- {namespace}: {surface.namespace_descriptions[namespace]} "
            f"(tools: {', '.join(members[namespace])})"
        )
    return "\n".join(lines)


def deferred_tool_namespaces(surface: ToolSurface) -> dict[str, str]:
    """Return ``tool name -> namespace`` for the tools this surface defers."""

    return {
        tool.name: tool._tool_namespace
        for tool in surface.tools
        if getattr(tool, "_tool_namespace", None)
    }


def run_config_for_tool_surface(run_config: Any, surface: ToolSurface) -> Any:
    """Return the run config that keeps a deferred run alive on a bare call.

    The SDK looks a hosted-search function up by ``(namespace, name)``, so a
    call to a deferred tool the model has not loaded yet (a bare ``name`` with
    no namespace) is "not found". By default that raises
    ``ModelBehaviorError`` and ends the run; on a deferred surface the model
    instead gets an error output naming the tool group to load through tool
    search. Eager surfaces keep the run config unchanged.
    """

    if surface.mode != MODE_DEFERRED:
        return run_config
    if run_config.tool_error_formatter is not None:
        raise ToolSurfaceError(
            f"{surface.runtime} agent {surface.agent_key} defers tools but its run "
            "config already has a tool_error_formatter"
        )
    namespaces = deferred_tool_namespaces(surface)

    def format_tool_error(args: Any) -> str | None:
        if args.kind != "tool_not_found":
            return None
        tool_name = canonical_tool_name(args.tool_name)
        namespace = namespaces.get(tool_name)
        if namespace is None:
            return None
        return (
            f"Tool '{tool_name}' is not loaded yet. It is in the '{namespace}' tool "
            f"group: load the '{namespace}' group with tool search, then call "
            f"'{tool_name}' again."
        )

    return replace(
        run_config,
        tool_not_found_behavior="return_error_to_model",
        tool_error_formatter=format_tool_error,
    )


_TOOL_SEARCH_ITEM_TYPES = frozenset({"tool_search_call", "tool_search_output"})


def replay_input_without_tool_search(
    items: Sequence[Any],
) -> tuple[list[Any], dict[str, int]]:
    """Rewrite run history for a follow-up request that declares no tools.

    ``tool_search_call`` / ``tool_search_output`` items only carry the search
    and the loaded tool *definitions*; a request without ``ToolSearchTool``
    has no surface for them, so they are removed. A reasoning item is tied to
    the next non-reasoning item, and the Responses API rejects one whose
    following item is missing, so reasoning directly preceding a removed
    search item is removed too (the SDK's own convention for dropped calls).
    Function calls made through a namespace keep their name, arguments and
    call id but lose the ``namespace`` field (the request declares none).
    Every function call and function call output -- the evidence the model
    gathered -- and the reasoning before them is kept. Returns the rewritten
    items and what was changed.
    """

    def item_type(item: Any) -> Any:
        return item.get("type") if isinstance(item, Mapping) else None

    removed = {
        index for index, item in enumerate(items) if item_type(item) in _TOOL_SEARCH_ITEM_TYPES
    }
    removed_reasoning: set[int] = set()
    for index, item in enumerate(items):
        if item_type(item) != "reasoning":
            continue
        following = next(
            (
                next_index
                for next_index in range(index + 1, len(items))
                if item_type(items[next_index]) != "reasoning"
            ),
            None,
        )
        if following in removed:
            removed_reasoning.add(index)

    replay: list[Any] = []
    changes = {
        "tool_search_items_removed": len(removed),
        "reasoning_items_removed": len(removed_reasoning),
        "function_call_namespaces_removed": 0,
    }
    for index, item in enumerate(items):
        if index in removed or index in removed_reasoning:
            continue
        if item_type(item) == "function_call" and item.get("namespace"):
            item = {key: value for key, value in item.items() if key != "namespace"}
            changes["function_call_namespaces_removed"] += 1
        replay.append(item)
    return replay, changes


def record_tool_surface_prompt(prompt_agent: Any, surface: ToolSurface, *, target_agent: Any) -> None:
    """Record the on-demand tool note as a runtime prompt layer.

    ``apply_tool_surface`` appends :func:`deferred_tools_note` to the
    instructions the model receives; the pending prompt run must carry the
    same layer before it is committed so the prompt audit and TraceReview
    reconstruction match the request.
    """

    if surface.mode != MODE_DEFERRED:
        return
    from src.lib.prompts.context import append_pending_prompt_runtime_context

    append_pending_prompt_runtime_context(
        prompt_agent,
        layer_id_suffix="tool_surface_on_demand",
        title="Tools loaded on demand",
        content=deferred_tools_note(surface),
        source_ref="src.lib.openai_agents.tool_surface:deferred_tools_note",
        target_agent=target_agent,
    )


@contextmanager
def tool_surface_trace_attributes(surface: ToolSurface) -> Iterator[None]:
    """Tag the Langfuse observations a run creates with its tool-surface mode.

    Enter around the SDK run creation: the run's background task copies this
    context, so every model/tool observation of the run carries the tag and
    metadata (evaluation arms filter on ``tool_surface:<mode>``).
    """

    from src.lib.openai_agents.langfuse_client import (
        get_langfuse,
        is_openai_agents_tracing_enabled,
    )

    if get_langfuse() is None or not is_openai_agents_tracing_enabled():
        yield
        return
    from langfuse import propagate_attributes

    with propagate_attributes(
        tags=[f"tool_surface:{surface.mode}"],
        metadata={
            "tool_surface_mode": surface.mode,
            "tool_surface_runtime": surface.runtime,
            "tool_surface_fingerprint": surface.fingerprint,
        },
    ):
        yield


# ---------------------------------------------------------------------------
# Declarative configuration
# ---------------------------------------------------------------------------


def tool_namespace_memberships(bindings: Iterable[Any]) -> dict[str, str]:
    """Return ``tool_id -> namespace id`` from tool-binding metadata."""

    memberships: dict[str, str] = {}
    for binding in bindings:
        metadata = binding.metadata if isinstance(binding.metadata, dict) else {}
        namespace = metadata.get("namespace")
        if namespace is not None:
            memberships[binding.tool_id] = str(namespace)
    return memberships


def declared_tool_namespaces() -> dict[str, str]:
    """Return ``tool_id -> namespace id`` from package tool-binding metadata.

    Reads the cached package tool registry that Agent Studio and the tool
    runtime already share: loading the registry from disk takes ~28 ms, and
    this runs for every deferred extractor run.
    """

    from src.lib.agent_studio.catalog_service import _load_package_tool_registry

    return tool_namespace_memberships(_load_package_tool_registry().bindings)


def declarative_namespace_resolver() -> NamespaceResolver:
    """Resolve a tool to its declared ``(namespace, description)``."""

    from src.lib.config.tool_loading_loader import get_tool_namespaces

    namespaces = get_tool_namespaces()
    memberships = declared_tool_namespaces()

    def resolve(tool_name: str) -> tuple[str, str] | None:
        namespace_id = memberships.get(canonical_tool_name(tool_name))
        if namespace_id is None:
            return None
        namespace = namespaces[namespace_id]
        return namespace.namespace_id, namespace.description

    return resolve


def resolve_tool_loading_policy(runtime: str, agent_key: str | None = None) -> Any:
    """Return the agent.yaml override when declared, else the runtime policy."""

    from src.lib.config.agent_loader import get_agent_definition
    from src.lib.config.tool_loading_loader import get_tool_loading_policies

    if agent_key:
        definition = get_agent_definition(agent_key)
        if definition is not None and definition.tool_loading is not None:
            return definition.tool_loading
    policies = get_tool_loading_policies()
    if runtime not in policies:
        raise ToolSurfaceError(f"No tool loading policy for runtime '{runtime}'")
    return policies[runtime]


def model_supports_tool_search(model_id: str | None, provider_id: str | None) -> bool:
    """Whether the catalog model and its provider declare hosted tool search."""

    from src.lib.config.models_loader import get_model
    from src.lib.config.providers_loader import get_provider

    if not model_id:
        return False
    model = get_model(model_id)
    if model is None or not model.supports_tool_search:
        return False
    provider = get_provider(provider_id or model.provider)
    return bool(provider is not None and provider.supports_tool_search)


def runtime_for_agent(agent: Any) -> str:
    """Classify a runtime agent for the loading policy lookup."""

    declared = getattr(agent, "tool_surface_runtime", None)
    if declared:
        return str(declared)
    from src.lib.observability.cost_context import get_agent_cost_identity
    identity = get_agent_cost_identity(agent)
    role = identity.get("agent_role") if isinstance(identity, Mapping) else None
    return {
        "extraction": "extractor",
        "validation": "validator",
        "formatter": "formatter",
        "supervisor": "chat_supervisor",
    }.get(str(role or ""), "specialist")


def _agent_key(agent: Any) -> str:
    from src.lib.observability.cost_context import get_agent_cost_identity
    identity = get_agent_cost_identity(agent)
    return str(
        getattr(agent, "agent_key", None)
        or (identity.get("agent_id") if isinstance(identity, Mapping) else None)
        or getattr(agent, "name", None)
        or "unknown"
    )


def _agent_model_and_provider(agent: Any) -> tuple[str | None, str | None]:
    model = getattr(agent, "model", None)
    if isinstance(model, str):
        from src.lib.config.models_loader import get_model

        catalog_model = get_model(model)
        return model, catalog_model.provider if catalog_model is not None else None
    model_id = getattr(model, "model", None)
    provider = getattr(model, "_agr_provider_id", None) or getattr(model, "_provider_id", None)
    return (str(model_id) if model_id else None), (str(provider) if provider else None)


def apply_tool_surface(
    agent: Any,
    *,
    runtime: str | None = None,
    forced_tool_names: Iterable[str] = (),
    required_tool_names: Iterable[str] = (),
) -> ToolSurface:
    """Compile ``agent.tools`` in place; call LAST, right before the SDK run."""

    tools = list(getattr(agent, "tools", None) or [])
    # Instructions before any on-demand tool note this function added.
    base_instructions = getattr(
        agent, "tool_surface_base_instructions", getattr(agent, "instructions", None)
    )
    previous = getattr(agent, "tool_surface", None)
    if isinstance(previous, ToolSurface) and getattr(agent, "tools", None) is previous.tools:
        # Re-running an unchanged, already compiled agent: compile from the
        # original plain tools, never from the compiled surface.
        tools = list(previous.source_tools)
    effective_runtime = runtime or runtime_for_agent(agent)
    agent_key = _agent_key(agent)
    # A named tool_choice must target a visible tool (the SDK rejects a named
    # choice that only a deferred tool could satisfy).
    tool_choice = getattr(getattr(agent, "model_settings", None), "tool_choice", None)
    named_choice = (
        (tool_choice,)
        if isinstance(tool_choice, str) and tool_choice not in {"auto", "required", "none"}
        else ()
    )
    policy = resolve_tool_loading_policy(effective_runtime, agent_key)
    model_id, provider_id = (None, None)
    supported = False
    if policy.mode == "deferred":
        model_id, provider_id = _agent_model_and_provider(agent)
        supported = model_supports_tool_search(model_id, provider_id)
    surface = compile_tool_surface(
        tools,
        runtime=effective_runtime,
        agent_key=agent_key,
        policy=policy,
        supports_tool_search=supported,
        namespace_resolver=(
            declarative_namespace_resolver() if policy.mode == "deferred" else _no_namespace
        ),
        forced_tool_names=(*forced_tool_names, *named_choice),
        required_tool_names=required_tool_names,
        model=model_id,
        provider=provider_id,
    )
    if surface.mode == MODE_DEFERRED:
        if not isinstance(base_instructions, str):
            raise ToolSurfaceError(
                f"{effective_runtime} agent {agent_key} defers tools but its instructions "
                "are not static text, so the on-demand tool note cannot be added"
            )
        agent.tool_surface_base_instructions = base_instructions
        agent.instructions = f"{base_instructions}\n\n{deferred_tools_note(surface)}"
    elif hasattr(agent, "tool_surface_base_instructions"):
        agent.instructions = base_instructions
    agent.tools = surface.tools
    agent.tool_surface = surface
    return surface


def _no_namespace(_tool_name: str) -> None:
    return None


def oversized_tool_namespaces(
    tool_names: Iterable[str],
    memberships: Mapping[str, str],
    namespace_max: int,
) -> dict[str, list[str]]:
    """Return the namespaces holding more than ``namespace_max`` of ``tool_names``."""

    per_namespace: dict[str, list[str]] = {}
    for tool_name in dict.fromkeys(str(name) for name in tool_names):
        namespace = memberships.get(tool_name)
        if namespace is not None:
            per_namespace.setdefault(namespace, []).append(tool_name)
    return {
        namespace: members
        for namespace, members in sorted(per_namespace.items())
        if len(members) > namespace_max
    }


# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------


def validate_tool_surface_configuration() -> dict[str, Any]:
    """Validate namespaces, memberships and loading policies; raise on any error.

    Checks: namespace references on bindings resolve; terminal tools
    (``finalize_*`` or builder finalization) never join a namespace; every
    runtime has a policy whose deferred namespaces exist and whose eager tools
    are known; each agent keeps at most TOOL_SURFACE_NAMESPACE_MAX_FUNCTIONS
    tools per namespace; agent ``tool_loading`` overrides only name the
    agent's own tools and existing namespaces; validator and supervisor
    runtimes, and Validation/Routing agents, never defer.
    """

    from src.lib.config.agent_loader import load_agent_definitions
    from src.lib.config.tool_loading_loader import (
        get_tool_loading_policies,
        get_tool_namespaces,
    )
    from src.lib.config.tool_policy_defaults_loader import load_tool_policy_defaults
    from src.lib.openai_agents.config import get_tool_surface_namespace_max_functions
    from src.lib.packages.tool_registry import load_tool_registry

    errors: list[str] = []
    try:
        namespaces = get_tool_namespaces()
        policies = get_tool_loading_policies()
    except ValueError as exc:
        raise ToolSurfaceConfigurationError(f"Tool surface configuration is invalid: {exc}") from exc

    registry = load_tool_registry()
    memberships: dict[str, str] = {}
    for binding in registry.bindings:
        metadata = binding.metadata if isinstance(binding.metadata, dict) else {}
        namespace = metadata.get("namespace")
        if namespace is None:
            continue
        if namespace not in namespaces:
            errors.append(
                f"Tool '{binding.tool_id}' declares unknown namespace '{namespace}'"
            )
            continue
        if is_terminal_tool_name(binding.tool_id) or metadata.get("builder_finalization"):
            errors.append(
                f"Terminal tool '{binding.tool_id}' must not join namespace '{namespace}' "
                "(terminal tools are always eager)"
            )
            continue
        memberships[binding.tool_id] = str(namespace)

    known_tools = set(registry.bindings_by_tool_id) | set(load_tool_policy_defaults())
    for runtime, policy in policies.items():
        for namespace in policy.deferred_namespaces or ():
            if namespace not in namespaces:
                errors.append(
                    f"{policy.source_label} defers unknown namespace '{namespace}'"
                )
        if runtime == "agent_studio":
            # Studio's authorized universe and namespaces are request-scoped
            # (Agent Studio catalog), not package bindings.
            continue
        for tool_name in policy.eager_tools:
            if tool_name not in known_tools:
                errors.append(f"{policy.source_label} names unknown eager tool '{tool_name}'")

    for runtime in sorted(NEVER_DEFERRED_RUNTIMES & set(policies)):
        if policies[runtime].mode == "deferred":
            errors.append(
                f"{policies[runtime].source_label} defers tools for runtime '{runtime}' "
                "(validator and supervisor tools are always eager)"
            )

    namespace_max = get_tool_surface_namespace_max_functions()
    for agent_id, definition in sorted(load_agent_definitions().items()):
        for namespace, members in oversized_tool_namespaces(
            definition.tools or [], memberships, namespace_max
        ).items():
            errors.append(
                f"Agent '{agent_id}' places {len(members)} tools in namespace "
                f"'{namespace}' (max {namespace_max})"
            )
        override = definition.tool_loading
        if override is None:
            continue
        if override.mode == "deferred" and definition.category in NEVER_DEFERRED_AGENT_CATEGORIES:
            errors.append(
                f"Agent '{agent_id}' ({definition.category}) tool_loading defers tools "
                "(validator and supervisor tools are always eager)"
            )
        for namespace in override.deferred_namespaces or ():
            if namespace not in namespaces:
                errors.append(f"Agent '{agent_id}' tool_loading defers unknown namespace '{namespace}'")
        for tool_name in override.eager_tools:
            if tool_name not in (definition.tools or []):
                errors.append(
                    f"Agent '{agent_id}' tool_loading names eager tool '{tool_name}' "
                    "that the agent does not declare"
                )

    if errors:
        raise ToolSurfaceConfigurationError(
            "Tool surface configuration is invalid: " + "; ".join(errors)
        )
    return {
        "namespace_count": len(namespaces),
        "namespaced_tool_count": len(memberships),
        "policies": {runtime: policy.mode for runtime, policy in sorted(policies.items())},
    }
