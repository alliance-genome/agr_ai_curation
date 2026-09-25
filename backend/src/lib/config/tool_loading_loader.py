"""Tool namespace and tool loading policy loaders (ALL-1280).

Hosted tool search groups deferred function tools into namespaces the model
sees only by name and description until it searches them. Two declarative
sources drive that surface:

- ``tool_namespaces`` package exports define namespaces (``id``, one
  ``description`` of at most 160 characters, ``owner`` package). Tool
  membership is declared on the tool binding (``metadata.namespace`` in the
  package ``tools/bindings.yaml``), never inferred from tool names.
- ``tool_loading`` package exports, optionally replaced per runtime by the
  runtime override ``tool_loading.yaml``, declare the loading policy for each
  runtime. An agent's ``agent.yaml`` may declare a ``tool_loading`` override
  with the same fields.

Everything here parses and validates strictly: an unknown field, runtime,
mode or namespace is an error, never a default.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from src.lib.packages import ExportKind

from .package_default_sources import (
    load_optional_runtime_yaml_source,
    load_package_yaml_sources,
)

TOOL_LOADING_RUNTIMES = (
    "agent_studio",
    "chat_supervisor",
    "flow_supervisor",
    "extractor",
    "validator",
    "formatter",
    "specialist",
)
TOOL_LOADING_MODES = ("eager", "deferred")
UNSUPPORTED_PROVIDER_BEHAVIOURS = ("eager", "fail")
NAMESPACE_DESCRIPTION_MAX_CHARS = 160
_NAMESPACE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_POLICY_FIELDS = frozenset(
    {"mode", "deferred_namespaces", "eager_tools", "on_unsupported_provider"}
)

_cache_lock = threading.Lock()
_namespaces_cache: dict[str, "ToolNamespace"] | None = None
_policies_cache: dict[str, "ToolLoadingPolicy"] | None = None


class ToolLoadingConfigError(ValueError):
    """Raised when namespace or loading-policy configuration is invalid."""


@dataclass(frozen=True)
class ToolNamespace:
    """One hosted tool-search namespace."""

    namespace_id: str
    description: str
    owner: str
    source_label: str


@dataclass(frozen=True)
class ToolLoadingPolicy:
    """Loading policy for one runtime or one agent override.

    ``mode="eager"`` sends every tool definition up front. ``mode="deferred"``
    defers namespaced function tools (only those in ``deferred_namespaces``
    when it is set) except ``eager_tools`` and forced/required/terminal tools.
    """

    mode: str
    deferred_namespaces: tuple[str, ...] | None = None
    eager_tools: tuple[str, ...] = ()
    on_unsupported_provider: str = "eager"
    source_label: str = ""


def _string_list(value: Any, *, field: str, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ToolLoadingConfigError(f"{label} field '{field}' must be a list of names")
    items = tuple(item.strip() for item in value)
    if len(set(items)) != len(items):
        raise ToolLoadingConfigError(f"{label} field '{field}' contains duplicates")
    return items


def parse_tool_loading_policy(data: Any, *, label: str) -> ToolLoadingPolicy:
    """Parse one policy mapping (runtime entry or agent.yaml ``tool_loading``)."""

    if not isinstance(data, Mapping):
        raise ToolLoadingConfigError(f"{label} must be a mapping")
    unknown = sorted(set(data) - _POLICY_FIELDS)
    if unknown:
        raise ToolLoadingConfigError(f"{label} has unknown fields: {', '.join(unknown)}")
    mode = str(data.get("mode") or "").strip()
    if mode not in TOOL_LOADING_MODES:
        raise ToolLoadingConfigError(
            f"{label} field 'mode' must be one of {', '.join(TOOL_LOADING_MODES)}"
        )
    on_unsupported = str(data.get("on_unsupported_provider", "eager")).strip()
    if on_unsupported not in UNSUPPORTED_PROVIDER_BEHAVIOURS:
        raise ToolLoadingConfigError(
            f"{label} field 'on_unsupported_provider' must be one of "
            f"{', '.join(UNSUPPORTED_PROVIDER_BEHAVIOURS)}"
        )
    deferred_namespaces = (
        _string_list(data["deferred_namespaces"], field="deferred_namespaces", label=label)
        if data.get("deferred_namespaces") is not None
        else None
    )
    eager_tools = (
        _string_list(data["eager_tools"], field="eager_tools", label=label)
        if data.get("eager_tools") is not None
        else ()
    )
    if mode == "eager" and (deferred_namespaces or eager_tools):
        raise ToolLoadingConfigError(
            f"{label} declares deferred_namespaces/eager_tools but mode is 'eager'"
        )
    return ToolLoadingPolicy(
        mode=mode,
        deferred_namespaces=deferred_namespaces,
        eager_tools=eager_tools,
        on_unsupported_provider=on_unsupported,
        source_label=label,
    )


def load_tool_namespaces(*, packages_dir: Path | None = None) -> dict[str, ToolNamespace]:
    """Load every package-declared namespace; ids are unique across packages."""

    namespaces: dict[str, ToolNamespace] = {}
    for source in load_package_yaml_sources(
        export_kind=ExportKind.TOOL_NAMESPACES,
        packages_dir=packages_dir,
    ):
        label = source.describe()
        raw = source.payload.get("namespaces")
        if not isinstance(raw, list) or not raw:
            raise ToolLoadingConfigError(f"{label} must define a non-empty 'namespaces' list")
        for entry in raw:
            if not isinstance(entry, Mapping):
                raise ToolLoadingConfigError(f"{label} namespace entries must be mappings")
            unknown = sorted(set(entry) - {"id", "description", "owner"})
            if unknown:
                raise ToolLoadingConfigError(
                    f"{label} namespace has unknown fields: {', '.join(unknown)}"
                )
            namespace_id = str(entry.get("id") or "").strip()
            description = str(entry.get("description") or "").strip()
            owner = str(entry.get("owner") or "").strip()
            if not _NAMESPACE_ID_PATTERN.match(namespace_id):
                raise ToolLoadingConfigError(
                    f"{label} namespace id '{namespace_id}' must match {_NAMESPACE_ID_PATTERN.pattern}"
                )
            if not description or len(description) > NAMESPACE_DESCRIPTION_MAX_CHARS:
                raise ToolLoadingConfigError(
                    f"{label} namespace '{namespace_id}' needs one description of 1-"
                    f"{NAMESPACE_DESCRIPTION_MAX_CHARS} characters"
                )
            if not owner:
                raise ToolLoadingConfigError(f"{label} namespace '{namespace_id}' needs an owner")
            if namespace_id in namespaces:
                raise ToolLoadingConfigError(
                    f"Namespace '{namespace_id}' is declared by both "
                    f"{namespaces[namespace_id].source_label} and {label}"
                )
            namespaces[namespace_id] = ToolNamespace(
                namespace_id=namespace_id,
                description=description,
                owner=owner,
                source_label=label,
            )
    return namespaces


def load_tool_loading_policies(
    tool_loading_path: Path | None = None,
    *,
    packages_dir: Path | None = None,
) -> dict[str, ToolLoadingPolicy]:
    """Load runtime policies: package exports, then the runtime override file.

    A later source replaces a runtime's whole policy. Every runtime in
    ``TOOL_LOADING_RUNTIMES`` must end up with a policy.
    """

    sources = list(
        load_package_yaml_sources(
            export_kind=ExportKind.TOOL_LOADING,
            packages_dir=packages_dir,
        )
    )
    runtime_source = load_optional_runtime_yaml_source(
        explicit_path=tool_loading_path,
        env_var="TOOL_LOADING_CONFIG_PATH",
        filename="tool_loading.yaml",
    )
    if runtime_source is not None:
        sources.append(runtime_source)
    if not sources:
        raise ToolLoadingConfigError(
            "No tool loading policy was found in runtime packages or runtime override config"
        )

    policies: dict[str, ToolLoadingPolicy] = {}
    for source in sources:
        label = source.describe()
        unknown_top = sorted(set(source.payload) - {"tool_loading_api_version", "runtimes"})
        if unknown_top:
            raise ToolLoadingConfigError(f"{label} has unknown fields: {', '.join(unknown_top)}")
        runtimes = source.payload.get("runtimes")
        if not isinstance(runtimes, Mapping):
            raise ToolLoadingConfigError(f"{label} must define a 'runtimes' mapping")
        for runtime, raw_policy in runtimes.items():
            if runtime not in TOOL_LOADING_RUNTIMES:
                raise ToolLoadingConfigError(f"{label} declares unknown runtime '{runtime}'")
            policies[runtime] = parse_tool_loading_policy(
                raw_policy,
                label=f"{label} runtime '{runtime}'",
            )
    missing = [runtime for runtime in TOOL_LOADING_RUNTIMES if runtime not in policies]
    if missing:
        raise ToolLoadingConfigError(
            f"Tool loading policy is missing runtimes: {', '.join(missing)}"
        )
    return policies


def get_tool_namespaces() -> dict[str, ToolNamespace]:
    """Return the cached namespace registry."""

    global _namespaces_cache
    with _cache_lock:
        if _namespaces_cache is None:
            _namespaces_cache = load_tool_namespaces()
        return _namespaces_cache


def get_tool_loading_policies() -> dict[str, ToolLoadingPolicy]:
    """Return the cached runtime loading policies."""

    global _policies_cache
    with _cache_lock:
        if _policies_cache is None:
            _policies_cache = load_tool_loading_policies()
        return _policies_cache


def reset_tool_loading_cache() -> None:
    """Forget cached namespaces and policies (tests and explicit reloads)."""

    global _namespaces_cache, _policies_cache
    with _cache_lock:
        _namespaces_cache = None
        _policies_cache = None
