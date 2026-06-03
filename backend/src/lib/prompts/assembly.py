"""Deterministic prompt layer assembly for system agents."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

from src.lib.config.agent_loader import (
    AgentDefinition,
    canonical_system_agent_key,
    load_agent_definitions,
)
from src.lib.config.schema_discovery import resolve_output_schema
from src.lib.domain_packs.validation_registry import ValidationBindingState
from src.lib.openai_agents.tool_call_policy import (
    DOCUMENT_REQUIRED_TOOL_NAMES,
    required_package_tool_names_from_metadata,
    required_tool_names_for_available_tools,
)
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

TOOL_POLICY_SUMMARIES = {
    "record_evidence": (
        "- Evidence policy: retained PDF evidence must come from "
        "`read_chunk.evidence_spans[].span_id` values. Use `record_evidence` with "
        "`span_ids`; the backend copies exact source text into `verified_quote` and "
        "preserves source span provenance. Before final output, review the active-run "
        "evidence workspace and keep only intended active evidence records."
    ),
    "get_agent_contract": (
        "- Detailed field, tool, schema, validator, and ontology facts are served "
        "by the read-only get_agent_contract helper."
    ),
}


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


_PROMPT_TEMPLATE_SOURCE_RE = re.compile(r"prompt_templates:([^:,\s]+):")


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


def prompt_templates_for_bundle(bundle: PromptLayerBundle) -> tuple[PromptTemplate, ...]:
    """Return active prompt template rows referenced by an assembled bundle."""

    cache = get_all_active_prompts()
    by_id = {str(prompt.id): prompt for prompt in cache.values() if prompt.id is not None}
    templates: list[PromptTemplate] = []
    seen: set[str] = set()
    for layer in bundle.layers:
        for prompt_id in _PROMPT_TEMPLATE_SOURCE_RE.findall(layer.source_ref):
            prompt = by_id.get(prompt_id)
            if prompt is None or prompt_id in seen:
                continue
            templates.append(prompt)
            seen.add(prompt_id)
    return tuple(templates)


def append_runtime_context_to_manifest(
    layer_manifest: Mapping[str, Any],
    *,
    layer_id_suffix: str,
    title: str,
    content: str,
    source_ref: str,
) -> dict[str, Any]:
    """Return a manifest extended with an additional runtime-context layer."""

    agent_id = str(layer_manifest.get("agent_id") or "").strip()
    if not agent_id:
        raise ValueError("layer_manifest agent_id is required")

    layer_content = _normalize_optional_text(content)
    if not layer_content:
        return dict(layer_manifest)

    layers = [dict(layer) for layer in layer_manifest.get("layers", []) or []]
    runtime_layer = _runtime_context_layer(
        agent_id=agent_id,
        layer_id_suffix=layer_id_suffix,
        title=title,
        content=layer_content,
        source_ref=source_ref,
    )
    layers.append(runtime_layer.to_manifest())
    return {
        "agent_id": agent_id,
        "layers": layers,
        "hash": _stable_hash(
            {
                "agent_id": agent_id,
                "layers": [str(layer["hash"]) for layer in layers],
            }
        ),
    }


def _resolve_system_agent(agent_id: str) -> AgentDefinition:
    requested_id = str(agent_id or "").strip()
    if not requested_id:
        raise ValueError("agent_id is required")

    definitions = load_agent_definitions()
    for agent in definitions.values():
        canonical_agent_id = canonical_system_agent_key(agent)
        accepted_ids = {agent.agent_id, canonical_agent_id}
        if agent.folder_name == canonical_agent_id:
            accepted_ids.add(agent.folder_name)
        if requested_id in accepted_ids:
            return agent

    raise ValueError(f"Unknown system agent '{requested_id}'")


def _build_core_generated_content(agent: AgentDefinition) -> str:
    fragments: list[str] = []
    runtime_contract = _build_compact_runtime_contract(agent)
    if runtime_contract:
        fragments.append(runtime_contract)

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
    refs = [f"agent_config:{agent.agent_id}"]
    schema_key = str(agent.output_schema or "").strip()
    if schema_key:
        refs.append(f"output_schema:{schema_key}")
    if agent.tools:
        refs.append("tools:agent_yaml+package_tool_registry")
    domain_pack_id = _agent_domain_pack_id(agent)
    if domain_pack_id:
        refs.append(f"domain_pack:{domain_pack_id}")
    return "|".join(refs)


def _build_compact_runtime_contract(agent: AgentDefinition) -> str:
    lines: list[str] = []
    if agent.tools or agent.output_schema or _agent_domain_pack_id(agent):
        lines.append("## Generated Runtime Contract")

    if agent.tools:
        required_tools = required_tool_names_for_available_tools(
            agent.tools,
            required_package_tool_names_resolver=_required_package_tool_names,
        )
        if required_tools == DOCUMENT_REQUIRED_TOOL_NAMES:
            lines.append(
                "- Required tool-call policy: call at least one document retrieval tool "
                "(search_document, read_section, or read_subsection) before final output."
            )
        elif required_tools:
            lines.append(
                "- Required tool-call policy: call at least one of "
                f"{', '.join(sorted(required_tools))} before final output."
            )
        lines.extend(
            summary
            for tool_name, summary in TOOL_POLICY_SUMMARIES.items()
            if tool_name in agent.tools
        )

    if agent.output_schema:
        lines.append(
            f"- Output contract from agent.yaml: produce JSON matching {agent.output_schema}; "
            "the structured-output layer below is authoritative for final response shape."
        )

    domain_lines = _build_domain_pack_contract_lines(agent)
    lines.extend(domain_lines)

    if _agent_uses_extraction_safety_rule(agent):
        lines.append(
            "- Runtime safety rule: No extractor should invent exact ontology CURIEs to "
            "satisfy validation; validator-bound unresolved candidates must be allowed "
            "through the schema when evidence supports the candidate but normalized "
            "identity is pending."
        )

    return "\n".join(lines)


def _agent_domain_pack_id(agent: AgentDefinition) -> str | None:
    return str(agent.curation.domain_pack_id or "").strip() or None


def _agent_uses_extraction_safety_rule(agent: AgentDefinition) -> bool:
    domain_pack_id = _agent_domain_pack_id(agent)
    if not domain_pack_id:
        return False
    category = str(agent.category or "").strip().lower()
    return category == "extraction"


def _build_domain_pack_contract_lines(agent: AgentDefinition) -> list[str]:
    domain_pack_id = _agent_domain_pack_id(agent)
    if not domain_pack_id:
        return []

    registry = _domain_pack_validation_registries().get(domain_pack_id)
    if registry is None:
        raise ValueError(
            f"Domain pack '{domain_pack_id}' for agent '{agent.agent_id}' is not registered"
        )

    metadata = registry.domain_pack.metadata
    semantic_source = str(metadata.metadata.get("semantic_source") or "").strip()
    semantic_suffix = f"; semantic source {semantic_source}" if semantic_source else ""
    lines = [
        f"- Domain envelope pack: {metadata.pack_id} v{metadata.version} "
        f"({metadata.status.value}{semantic_suffix})."
    ]

    validator_fields = _format_validator_bound_fields(metadata.object_definitions)
    if validator_fields:
        lines.append(
            "- Validators own these fields; do not invent their identifiers: "
            f"{validator_fields}. "
            "Use get_agent_contract (topics validator_bindings and "
            "ontology_constraints, detail_level=detail) for the full bindings, "
            "selectors, and accepted ontology terms."
        )

    active_bindings = [
        binding
        for binding in registry.bindings
        if binding.state is ValidationBindingState.ACTIVE
    ]
    if active_bindings:
        lines.append(
            "- Active validator bindings own validator result fields and envelope "
            "validation findings; do not author validator outputs yourself."
        )

    return lines


def _format_validator_bound_fields(object_definitions: Sequence[Any]) -> str:
    fields: list[str] = []
    for object_definition in object_definitions:
        for field in object_definition.fields:
            binding_id = _metadata_text(field.metadata, "validator_binding_id")
            if binding_id:
                fields.append(f"{object_definition.object_type}.{field.field_path}")
    return _join_limited(fields, limit=10)


def _required_package_tool_names(available_tool_names: set[str]) -> set[str]:
    from src.lib.packages.tool_registry import load_tool_registry

    registry = load_tool_registry()
    metadata_by_name = {
        tool_name: binding.metadata
        for tool_name in available_tool_names
        if (binding := registry.get(tool_name)) is not None
    }
    return required_package_tool_names_from_metadata(
        available_tool_names,
        metadata_by_name,
    )


def _domain_pack_validation_registries() -> Mapping[str, Any]:
    from src.lib.flows.validation_attachments import domain_pack_validation_registries

    return domain_pack_validation_registries()


def _metadata_text(metadata: Mapping[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _join_limited(values: Sequence[str], *, limit: int) -> str:
    selected = [value for value in values if value][:limit]
    suffix = f", +{len(values) - limit} more" if len(values) > limit else ""
    return ", ".join(selected) + suffix


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


def _runtime_context_layer(
    *,
    agent_id: str,
    layer_id_suffix: str,
    title: str,
    content: str,
    source_ref: str,
) -> PromptLayer:
    suffix = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", str(layer_id_suffix or "").strip())
    if not suffix:
        raise ValueError("runtime context layer_id_suffix is required")
    return _make_layer(
        layer_id=f"{agent_id}:runtime_context:{suffix}",
        kind="runtime_context",
        title=title,
        content=content,
        provenance="runtime_context",
        editable=False,
        locked=True,
        source_ref=source_ref,
    )


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
