"""Deterministic prompt layer assembly for system agents."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

from src.lib.agent_studio.system_agent_sync import canonical_system_agent_key
from src.lib.config.agent_loader import AgentDefinition, load_agent_definitions
from src.lib.config.schema_discovery import resolve_output_schema
from src.lib.openai_agents.prompt_utils import inject_structured_output_instruction
from src.lib.prompts.cache import PromptNotFoundError, get_all_active_prompts
from src.models.sql.prompts import PromptTemplate

PromptLayerKind = Literal[
    "core_static",
    "core_generated",
    "base_prompt",
    "group_rules",
    "curator_overlay",
    "runtime_context",
]


CORE_STATIC_PROMPT = """## Platform Runtime Contract
These backend-owned instructions are part of the system-agent contract.
Editable prompts may add task and domain guidance, but must not override locked runtime,
schema, tool, audit, or safety requirements.
"""


@dataclass(frozen=True)
class PromptLayer:
    """One independently visible prompt layer with stable provenance."""

    id: str
    kind: PromptLayerKind
    title: str
    content: str
    provenance: str
    editable: bool
    locked: bool
    source_ref: str
    hash: str

    def to_manifest(self) -> dict[str, Any]:
        """Return a JSON-serializable layer manifest."""
        return asdict(self)


@dataclass(frozen=True)
class PromptLayerBundle:
    """Ordered prompt layers for a system agent."""

    agent_id: str
    layers: tuple[PromptLayer, ...]
    hash: str

    @property
    def layer_order(self) -> tuple[PromptLayerKind, ...]:
        """Return the layer kinds in render order."""
        return tuple(layer.kind for layer in self.layers)

    def render(self, separator: str = "\n\n") -> str:
        """Render the layered content without merging layer metadata."""
        return separator.join(layer.content for layer in self.layers if layer.content)

    def to_manifest(self) -> dict[str, Any]:
        """Return a JSON-serializable bundle manifest."""
        return {
            "agent_id": self.agent_id,
            "layers": [layer.to_manifest() for layer in self.layers],
            "hash": self.hash,
        }


def build_agent_core_prompt(agent_id: str) -> PromptLayerBundle:
    """Build locked backend-owned core layers for a system agent."""
    agent = _resolve_system_agent(agent_id)
    canonical_agent_id = canonical_system_agent_key(agent)
    layers = [
        _make_layer(
            layer_id=f"{canonical_agent_id}:core_static",
            kind="core_static",
            title="Platform runtime contract",
            content=CORE_STATIC_PROMPT.strip(),
            provenance="backend_static",
            editable=False,
            locked=True,
            source_ref="src.lib.prompts.assembly:CORE_STATIC_PROMPT",
        )
    ]

    generated_content = _build_core_generated_content(agent)
    if generated_content:
        layers.append(
            _make_layer(
                layer_id=f"{canonical_agent_id}:core_generated",
                kind="core_generated",
                title="Generated runtime contract",
                content=generated_content,
                provenance="backend_generated",
                editable=False,
                locked=True,
                source_ref=_core_generated_source_ref(agent),
            )
        )

    return _bundle(canonical_agent_id, layers)


def build_agent_prompt_layers(
    agent_id: str,
    group_id: str | Sequence[str] | None = None,
    overlay: str | None = None,
    runtime_context: str | Mapping[str, Any] | Sequence[Any] | None = None,
) -> PromptLayerBundle:
    """Build final prompt layers in deterministic effective-prompt order."""
    agent = _resolve_system_agent(agent_id)
    canonical_agent_id = canonical_system_agent_key(agent)
    cache = get_all_active_prompts()

    layers = list(build_agent_core_prompt(canonical_agent_id).layers)
    layers.append(
        _prompt_template_layer(
            _required_prompt_template(
                cache,
                agent_name=canonical_agent_id,
                prompt_type="system",
                group_id=None,
            ),
            layer_id=f"{canonical_agent_id}:base_prompt",
            kind="base_prompt",
            title="Editable base prompt",
            provenance="prompt_template:system",
            editable=True,
            locked=False,
        )
    )

    group_layer = _build_group_rules_layer(
        cache,
        canonical_agent_id=canonical_agent_id,
        group_ids=_normalize_group_ids(group_id),
    )
    if group_layer is not None:
        layers.append(group_layer)

    overlay_content = _normalize_optional_text(overlay)
    if overlay_content:
        layers.append(
            _make_layer(
                layer_id=f"{canonical_agent_id}:curator_overlay",
                kind="curator_overlay",
                title="Curator overlay",
                content=overlay_content,
                provenance="curator_overlay",
                editable=True,
                locked=False,
                source_ref="request:curator_overlay",
            )
        )

    runtime_content = _normalize_runtime_context(runtime_context)
    if runtime_content:
        layers.append(
            _make_layer(
                layer_id=f"{canonical_agent_id}:runtime_context",
                kind="runtime_context",
                title="Runtime context",
                content=runtime_content,
                provenance="runtime_context",
                editable=False,
                locked=True,
                source_ref="request:runtime_context",
            )
        )

    return _bundle(canonical_agent_id, layers)


def _resolve_system_agent(agent_id: str) -> AgentDefinition:
    requested_id = str(agent_id or "").strip()
    if not requested_id:
        raise ValueError("agent_id is required")

    definitions = load_agent_definitions()
    for agent in definitions.values():
        if requested_id in {
            agent.agent_id,
            agent.folder_name,
            canonical_system_agent_key(agent),
        }:
            return agent

    raise ValueError(f"Unknown system agent '{requested_id}'")


def _build_core_generated_content(agent: AgentDefinition) -> str:
    fragments: list[str] = []
    schema_key = str(agent.output_schema or "").strip()
    if schema_key:
        output_type = resolve_output_schema(schema_key)
        if output_type is None:
            raise ValueError(
                f"Output schema '{schema_key}' for agent '{agent.agent_id}' is not registered"
            )

        fragments.append(
            inject_structured_output_instruction(
                "",
                output_type=output_type,
                insert_after_first_section=False,
            ).strip()
        )

    return "\n\n".join(fragment for fragment in fragments if fragment)


def _core_generated_source_ref(agent: AgentDefinition) -> str:
    schema_key = str(agent.output_schema or "").strip()
    if schema_key:
        return f"agent_config:{agent.agent_id}:output_schema:{schema_key}"
    return f"agent_config:{agent.agent_id}"


def _required_prompt_template(
    cache: Mapping[str, PromptTemplate],
    *,
    agent_name: str,
    prompt_type: str,
    group_id: str | None,
) -> PromptTemplate:
    key = _prompt_cache_key(agent_name, prompt_type, group_id)
    prompt = cache.get(key)
    if prompt is None:
        raise PromptNotFoundError(
            f"No active prompt found for agent='{agent_name}', "
            f"type='{prompt_type}', group='{group_id}'."
        )
    return prompt


def _prompt_template_layer(
    prompt: PromptTemplate,
    *,
    layer_id: str,
    kind: PromptLayerKind,
    title: str,
    provenance: str,
    editable: bool,
    locked: bool,
) -> PromptLayer:
    return _make_layer(
        layer_id=layer_id,
        kind=kind,
        title=title,
        content=_prompt_template_content(prompt),
        provenance=provenance,
        editable=editable,
        locked=locked,
        source_ref=_prompt_template_source_ref(prompt),
    )


def _build_group_rules_layer(
    cache: Mapping[str, PromptTemplate],
    *,
    canonical_agent_id: str,
    group_ids: tuple[str, ...],
) -> PromptLayer | None:
    if not group_ids:
        return None

    prompts: list[PromptTemplate] = []
    for group in group_ids:
        prompt = cache.get(_prompt_cache_key(canonical_agent_id, "group_rules", group))
        if prompt is not None:
            prompts.append(prompt)

    if not prompts:
        return None

    content = "\n\n".join(
        f"## {prompt.group_id}\n{_prompt_template_content(prompt)}"
        for prompt in prompts
    )
    source_ref = ",".join(_prompt_template_source_ref(prompt) for prompt in prompts)
    group_ref = "+".join(str(prompt.group_id) for prompt in prompts)
    return _make_layer(
        layer_id=f"{canonical_agent_id}:group_rules:{group_ref}",
        kind="group_rules",
        title="Package/admin group rules",
        content=content,
        provenance="prompt_template:group_rules",
        editable=True,
        locked=False,
        source_ref=source_ref,
    )


def _prompt_cache_key(agent_name: str, prompt_type: str, group_id: str | None) -> str:
    return f"{agent_name}:{prompt_type}:{group_id or 'base'}"


def _prompt_template_source_ref(prompt: PromptTemplate) -> str:
    prompt_id = str(prompt.id) if prompt.id is not None else "active"
    group = prompt.group_id or "base"
    return (
        f"prompt_templates:{prompt_id}:"
        f"{prompt.agent_name}:{prompt.prompt_type}:{group}:v{prompt.version}"
    )


def _prompt_template_content(prompt: PromptTemplate) -> str:
    if prompt.content is None:
        raise ValueError(
            "PromptTemplate content is required for "
            f"{prompt.agent_name}:{prompt.prompt_type}:{prompt.group_id or 'base'}"
        )
    return str(prompt.content).strip()


def _normalize_group_ids(group_id: str | Sequence[str] | None) -> tuple[str, ...]:
    if group_id is None:
        return ()
    raw_groups: Sequence[str]
    if isinstance(group_id, str):
        raw_groups = (group_id,)
    else:
        raw_groups = group_id

    groups: list[str] = []
    for raw_group in raw_groups:
        normalized = str(raw_group or "").strip().upper()
        if normalized and normalized not in groups:
            groups.append(normalized)
    return tuple(groups)


def _normalize_optional_text(value: str | None) -> str:
    return str(value or "").strip()


def _normalize_runtime_context(
    runtime_context: str | Mapping[str, Any] | Sequence[Any] | None,
) -> str:
    if runtime_context is None:
        return ""
    if isinstance(runtime_context, str):
        return runtime_context.strip()
    return json.dumps(runtime_context, sort_keys=True, separators=(",", ":"), default=str)


def _make_layer(
    *,
    layer_id: str,
    kind: PromptLayerKind,
    title: str,
    content: str,
    provenance: str,
    editable: bool,
    locked: bool,
    source_ref: str,
) -> PromptLayer:
    if content is None:
        raise ValueError(f"Prompt layer '{layer_id}' content is required")
    normalized_content = str(content).strip()
    layer_hash = _stable_hash(
        {
            "id": layer_id,
            "kind": kind,
            "title": title,
            "content": normalized_content,
            "provenance": provenance,
            "editable": editable,
            "locked": locked,
            "source_ref": source_ref,
        }
    )
    return PromptLayer(
        id=layer_id,
        kind=kind,
        title=title,
        content=normalized_content,
        provenance=provenance,
        editable=editable,
        locked=locked,
        source_ref=source_ref,
        hash=layer_hash,
    )


def _bundle(agent_id: str, layers: Sequence[PromptLayer]) -> PromptLayerBundle:
    layer_tuple = tuple(layers)
    return PromptLayerBundle(
        agent_id=agent_id,
        layers=layer_tuple,
        hash=_stable_hash(
            {
                "agent_id": agent_id,
                "layers": [layer.hash for layer in layer_tuple],
            }
        ),
    )


def _stable_hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
