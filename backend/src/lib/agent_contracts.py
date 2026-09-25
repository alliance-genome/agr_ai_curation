"""Read-only, scoped and bounded contract details for system and custom agents.

Every topic resolves the requested agent, domain pack, object type and field
scope before any item is built, so an unknown selector fails explicitly instead
of widening to the whole registry. Items are returned one deterministic page at
a time: a page stops at the requested limit or at the serialized response
budget, whichever comes first, and carries an explicit continuation cursor. An
item larger than the per-item budget is returned as an outline whose omitted
values are read exactly through ``item_ref`` and ``detail_pointer`` drilldown,
so contract JSON is never sliced.

A custom agent (``ca_*``) resolves from its saved executable revision: the
running agent reads its own pinned revision, and any other custom agent is read
from its saved head only when the authenticated curator can see it under the
custom-agent visibility rules.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from pydantic import BaseModel

from src.lib.context import get_current_session_id, get_current_trace_id
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationAttachmentOption,
    ValidatorBinding,
)
from src.lib.flows.validation_attachments import domain_pack_validation_registries
from src.lib.observability.payload_contracts import (
    PayloadContractViolation,
    report_payload_contract_violation,
)
from src.lib.openai_agents.bounded_list import bounded_envelope, normalize_page_limit
from src.lib.openai_agents.config import (
    get_agent_contract_max_item_chars,
    get_agent_contract_max_response_chars,
    get_tool_page_default_limit,
    get_tool_page_max_limit,
)


AGENT_CONTRACT_TOPICS = frozenset(
    {
        "tools",
        "output_schema",
        "domain_envelope",
        "validator_bindings",
        "ontology_constraints",
        "field",
    }
)
DETAIL_LEVELS = frozenset({"summary", "detail"})
_PACK_SELECTORS = frozenset({"domain_pack_id", "object_type", "field_path"})
TOPIC_SELECTORS: Mapping[str, frozenset[str]] = {
    "tools": frozenset(),
    "output_schema": frozenset({"field_path"}),
    "domain_envelope": _PACK_SELECTORS,
    "validator_bindings": _PACK_SELECTORS,
    "ontology_constraints": _PACK_SELECTORS,
    "field": _PACK_SELECTORS,
}

_TOOL_NAME = "get_agent_contract"
_CUSTOM_AGENT_PREFIX = "ca_"
_CUSTOM_AGENT_NOT_VISIBLE_HINT = (
    "Custom agents are visible to their owner, or to members of the project "
    "they are shared with, within the agent's group restrictions."
)
_CURSOR_PATTERN = re.compile(r"^(0|[1-9][0-9]*):([0-9a-f]{12})$")
_LIST_INDEX_PATTERN = re.compile(r"0|[1-9][0-9]*")
# Room kept for the page envelope, continuation cursor and drilldown hint.
_PAGE_ENVELOPE_RESERVE_CHARS = 700
# Room kept per omitted-value stub when an oversized item is outlined.
_OUTLINE_STUB_RESERVE_CHARS = 160
_MAX_SUGGESTIONS = 5
_MAX_LISTED_NAMES = 50
_BINDING_SUMMARY_KEYS = (
    "validator_binding_id",
    "display_name",
    "binding_state",
    "origin",
    "validator_agent",
    "applies_to_domain_pack_id",
    "object_types",
    "object_roles",
    "field_paths",
    "field_types",
    "required",
    "blocking",
    "allow_opt_out",
)
_FIELD_SUMMARY_KEYS = (
    "field_path",
    "display_name",
    "description",
    "field_type",
    "required",
    "definition_state",
    "validation_policy",
)


class _ContractRequestError(ValueError):
    """A caller-correctable request problem returned as a structured error."""

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


@dataclass(frozen=True)
class _CustomAgentContract:
    """A custom agent's saved revision in the shape the topic builders read."""

    entry: Mapping[str, Any]
    identity: Mapping[str, Any]
    revision_fingerprint: str
    registries: Mapping[str, DomainPackValidationRegistry]
    # Packaged validator whose bindings this agent serves, if any.
    binding_agent_id: str | None
    binding_package_id: str | None
    # Packaged agent whose tool-method context applies to the saved tools.
    method_agent_id: str
    output_schema_note: str | None
    unavailable_profile_mappings: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class _Request:
    agent_id: str
    topic: str
    detail_level: str
    entry: Mapping[str, Any]
    package_id: str | None
    owned_pack_ids: tuple[str, ...]
    validator_pack_ids: tuple[str, ...]
    registries: Mapping[str, DomainPackValidationRegistry]
    domain_pack_id: str | None
    object_type: str | None
    field_path: str | None
    tool_details_resolver: Callable[[str, str], Mapping[str, Any] | None] | None
    output_schema_resolver: Callable[[str], type[BaseModel] | None] | None
    binding_agent_id: str | None
    method_agent_id: str
    custom_agent: _CustomAgentContract | None = None

    @property
    def detail(self) -> bool:
        return self.detail_level == "detail"


@dataclass(frozen=True)
class _Target:
    pack_id: str
    registry: DomainPackValidationRegistry
    object_definition: Any
    field_definition: Any = None

    @property
    def object_type(self) -> str:
        return self.object_definition.object_type

    @property
    def field_path(self) -> str:
        return self.field_definition.field_path


def get_agent_contract(
    agent_id: str,
    topic: str,
    field_path: str | None = None,
    detail_level: str = "summary",
    limit: int | None = None,
    cursor: str | None = None,
    domain_pack_id: str | None = None,
    object_type: str | None = None,
    item_ref: str | None = None,
    detail_pointer: str | None = None,
    *,
    agent_registry: Mapping[str, Mapping[str, Any]] | None = None,
    registries: Mapping[str, DomainPackValidationRegistry] | None = None,
    tool_details_resolver: Callable[[str, str], Mapping[str, Any] | None] | None = None,
    output_schema_resolver: Callable[[str], type[BaseModel] | None] | None = None,
    caller: Any = None,
) -> dict[str, Any]:
    """Return one scoped, bounded page of read-only contract metadata.

    ``domain_pack_id``, ``object_type`` and ``field_path`` narrow the topic
    before anything is built; a selector a topic does not support, or one that
    matches nothing, is an explicit error. ``limit`` (TOOL_PAGE_DEFAULT_LIMIT /
    TOOL_PAGE_MAX_LIMIT) and the serialized budgets
    (AGENT_CONTRACT_MAX_RESPONSE_CHARS / AGENT_CONTRACT_MAX_ITEM_CHARS) bound
    each page; pass ``page.next_cursor`` back as ``cursor`` to continue. Pass an
    item's ``ref`` as ``item_ref`` (with ``detail_pointer``) to read one item or
    one of its omitted values exactly.

    ``caller`` is the runtime agent invoking the tool. A custom agent asking for
    its own ``ca_*`` id reads the revision it is running; any other custom agent
    is resolved for the authenticated curator and the caller's groups, and one
    they cannot see is reported exactly like an unknown agent.
    """

    try:
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_topic = _required_text(topic, "topic").lower()
    except ValueError as exc:
        return _error(str(exc))
    normalized_detail_level = _optional_text(detail_level)
    echo = {
        "agent_id": normalized_agent_id,
        "topic": normalized_topic,
        "detail_level": normalized_detail_level,
    }

    if normalized_topic not in AGENT_CONTRACT_TOPICS:
        return _error(
            f"Unsupported contract topic '{normalized_topic}'.",
            **echo,
            allowed_topics=sorted(AGENT_CONTRACT_TOPICS),
        )
    if normalized_detail_level not in DETAIL_LEVELS:
        return _error(
            f"Unsupported detail_level '{normalized_detail_level}'.",
            **echo,
            allowed_detail_levels=sorted(DETAIL_LEVELS),
        )

    selectors = {
        "domain_pack_id": _optional_text(domain_pack_id),
        "object_type": _optional_text(object_type),
        "field_path": _optional_text(field_path),
    }
    scope = {name: value for name, value in selectors.items() if value is not None}
    supported = TOPIC_SELECTORS[normalized_topic]
    unsupported = sorted(name for name in scope if name not in supported)
    if unsupported:
        return _error(
            f"Selector(s) {', '.join(unsupported)} are not supported for topic "
            f"'{normalized_topic}'.",
            **echo,
            scope=scope,
            supported_selectors=sorted(supported),
        )
    normalized_item_ref = _optional_text(item_ref)
    if detail_pointer is not None and normalized_item_ref is None:
        return _error(
            "detail_pointer requires item_ref: pass the ref of the item returned "
            "by a previous call.",
            **echo,
        )
    if normalized_topic == "field" and selectors["field_path"] is None:
        return _error("field_path is required when topic is 'field'.", **echo)

    resolved_agent_registry = agent_registry or _default_agent_registry()
    custom: _CustomAgentContract | None = None
    if normalized_agent_id.startswith(_CUSTOM_AGENT_PREFIX):
        try:
            custom = _load_custom_agent(
                normalized_agent_id,
                caller=caller,
                agent_registry=resolved_agent_registry,
                registries=(
                    registries if registries is not None else domain_pack_validation_registries()
                ),
                output_schema_resolver=output_schema_resolver or _resolve_output_schema,
            )
        except _ContractRequestError as exc:
            return _error(exc.message, **echo, **exc.details)
        if custom is None:
            return _error(
                f"Agent {normalized_agent_id} was not found.",
                **echo,
                hint=_CUSTOM_AGENT_NOT_VISIBLE_HINT,
            )
        entry = custom.entry
        resolved_registries = custom.registries
        binding_agent_id = custom.binding_agent_id
        binding_package_id = custom.binding_package_id
        method_agent_id = custom.method_agent_id
    else:
        found = resolved_agent_registry.get(normalized_agent_id)
        if found is None:
            return _error(f"Agent {normalized_agent_id} was not found.", **echo)
        entry = found
        resolved_registries = (
            registries if registries is not None else domain_pack_validation_registries()
        )
        binding_agent_id = normalized_agent_id
        binding_package_id = _optional_text(entry.get("package_id"))
        method_agent_id = normalized_agent_id

    request = _Request(
        agent_id=normalized_agent_id,
        topic=normalized_topic,
        detail_level=normalized_detail_level,
        entry=entry,
        package_id=binding_package_id,
        owned_pack_ids=tuple(_owned_domain_pack_ids(entry)),
        validator_pack_ids=tuple(
            _validator_domain_pack_ids(
                binding_agent_id,
                package_id=binding_package_id,
                registries=resolved_registries,
            )
        ),
        registries=resolved_registries,
        domain_pack_id=selectors["domain_pack_id"],
        object_type=selectors["object_type"],
        field_path=selectors["field_path"],
        tool_details_resolver=tool_details_resolver,
        output_schema_resolver=output_schema_resolver,
        binding_agent_id=binding_agent_id,
        method_agent_id=method_agent_id,
        custom_agent=custom,
    )

    base: dict[str, Any] = {
        "success": True,
        **echo,
        "read_only": True,
        "deterministic": True,
        "live_state": False,
        "writes": False,
    }
    if scope:
        base["scope"] = scope
    if custom is not None:
        base["custom_agent"] = dict(custom.identity)

    try:
        header, items = _TOPIC_BUILDERS[normalized_topic](request)
        if not items and request.field_path is not None:
            header = {
                **header,
                "note": (
                    f"Field '{request.field_path}' exists in scope, but no "
                    f"{normalized_topic} entries for agent {normalized_agent_id} "
                    "cover it."
                ),
            }
        fingerprint = _request_fingerprint(
            request,
            item_refs=[item["ref"] for item in items],
            item_ref=normalized_item_ref,
            detail_pointer=detail_pointer,
        )
        if normalized_item_ref is not None:
            response = _item_detail_response(
                {**base, **header},
                items,
                item_ref=normalized_item_ref,
                detail_pointer=detail_pointer or "",
                limit=limit,
                cursor=cursor,
                fingerprint=fingerprint,
            )
        else:
            response = _page_response(
                {**base, **header},
                items,
                limit=limit,
                cursor=cursor,
                fingerprint=fingerprint,
                repeat_arguments={"topic": normalized_topic, "detail_level": normalized_detail_level, **scope},
            )
        return _within_response_budget(response)
    except _ContractRequestError as exc:
        return _error(exc.message, **echo, **({"scope": scope} if scope else {}), **exc.details)
    except PayloadContractViolation as violation:
        report_payload_contract_violation(
            violation,
            phase="tool_result",
            agent=normalized_agent_id,
            tool_name=_TOOL_NAME,
            trace_id=get_current_trace_id(),
            session_id=get_current_session_id(),
            correlation={
                "topic": normalized_topic,
                "detail_level": normalized_detail_level,
                "item_ref": normalized_item_ref,
                "detail_pointer": detail_pointer,
                **scope,
            },
        )
        return _error(
            "The contract response could not be produced within its configured "
            "contract and the failure was reported. Narrow the request with "
            "domain_pack_id, object_type, field_path or a smaller limit.",
            **echo,
            failure=violation.diagnostic(),
        )


def get_extraction_contract(
    agent_id: str,
    topic: str = "domain_envelope",
    field_path: str | None = None,
    detail_level: str = "summary",
    **kwargs: Any,
) -> dict[str, Any]:
    """Extractor-facing alias that preserves get_agent_contract as source of truth.

    The Linear contract scope intentionally pre-provisions narrow names for
    prompt-facing/package callers without creating a second metadata service.
    """

    return get_agent_contract(
        agent_id=agent_id,
        topic=topic,
        field_path=field_path,
        detail_level=detail_level,
        **kwargs,
    )


def get_domain_pack_field_info(
    agent_id: str,
    field_path: str,
    detail_level: str = "detail",
    **kwargs: Any,
) -> dict[str, Any]:
    """Field-focused alias that preserves get_agent_contract as source of truth.

    The Linear contract scope intentionally pre-provisions narrow names for
    prompt-facing/package callers without creating a second metadata service.
    """

    return get_agent_contract(
        agent_id=agent_id,
        topic="field",
        field_path=field_path,
        detail_level=detail_level,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Topic builders: resolve scope, then build only the scoped items.
# ---------------------------------------------------------------------------


def _tools_topic(request: _Request) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    resolver = request.tool_details_resolver or _tool_details
    items: list[dict[str, Any]] = []
    for tool_id in _string_list(request.entry.get("tools")):
        identity = {"ref": f"tool|{tool_id}", "kind": "tool", "tool_id": tool_id}
        details = resolver(request.method_agent_id, tool_id)
        if details is None:
            items.append(
                {**identity, "resolved": False, "error": "Tool details were not found."}
            )
            continue
        contract = {
            **identity,
            "name": _optional_text(details.get("name")) or tool_id,
            "category": _optional_text(details.get("category")),
            "description": _optional_text(details.get("description")),
            "required_context": list(details.get("required_context") or []),
        }
        agent_context = details.get("agent_context")
        if isinstance(agent_context, Mapping):
            methods = agent_context.get("methods")
            if isinstance(methods, list):
                contract["agent_methods"] = list(methods)
        if request.detail:
            contract["package_backed"] = bool(details.get("package_backed"))
            documentation = details.get("documentation")
            if isinstance(documentation, Mapping):
                contract["documentation"] = _compact_documentation(documentation)
            relevant_methods = details.get("relevant_methods")
            if isinstance(relevant_methods, Mapping):
                contract["relevant_methods"] = {
                    method_id: _method_summary(method)
                    for method_id, method in sorted(relevant_methods.items())
                    if isinstance(method, Mapping)
                }
        items.append(contract)
    return {}, items


def _output_schema_topic(request: _Request) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if request.custom_agent is not None:
        # A saved revision's output contract is authoritative; its parent's
        # packaged schema is never substituted for it.
        schema_name = _entry_output_schema(request.entry)
        note = request.custom_agent.output_schema_note
    else:
        schema_name = _output_schema_name(request.agent_id, request.entry)
        note = None
    if schema_name is None:
        _reject_schema_field(request, "has no output schema", hint=note)
        return {"output_schema": None, **({"note": note} if note else {})}, []

    resolver = request.output_schema_resolver or _resolve_output_schema
    schema_type = resolver(schema_name)
    if schema_type is None:
        _reject_schema_field(request, f"output schema {schema_name} could not be resolved")
        return {"output_schema": schema_name, "schema_resolved": False}, []

    schema = schema_type.model_json_schema()
    header: dict[str, Any] = {"output_schema": schema_name, "schema_resolved": True}
    if request.detail:
        header["schema_title"] = schema.get("title")
        header["schema_description"] = schema.get("description")
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])

    if request.field_path is None:
        return header, [
            {
                "ref": f"schema_field|{name}",
                "kind": "schema_field",
                **_json_schema_field_summary(name, value, required=name in required),
            }
            for name, value in sorted(properties.items())
            if isinstance(value, Mapping)
        ]

    located = _json_schema_field_node(schema, request.field_path)
    if located is None:
        raise _ContractRequestError(
            f"Field path '{request.field_path}' was not found in output schema "
            f"{schema_name}.",
            field_path=request.field_path,
            suggested_field_paths=_suggest(request.field_path, sorted(properties)),
            hint="List schema fields with topic=output_schema and no field_path.",
        )
    name, node, is_required = located
    item = {
        "ref": f"schema_field|{request.field_path}",
        "kind": "schema_field",
        **_json_schema_field_summary(name, node, required=is_required),
        "field_path": request.field_path,
    }
    if request.detail:
        item["schema"] = dict(node)
        item["definitions"] = _dependent_schema_definitions(schema, node)
    return header, [item]


def _domain_envelope_topic(request: _Request) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    pack_ids = _scoped_pack_ids(request, include_validator_packs=False)
    header = {
        "domain_packs": [
            _pack_header(request, pack_id, include_schema_refs=True) for pack_id in pack_ids
        ]
    }
    if request.field_path is not None:
        return header, [
            _field_definition_item(target, detail=request.detail)
            for target in _scoped_fields(request, pack_ids)
        ]

    objects = _scoped_objects(request, pack_ids)
    if not request.detail:
        return header, [_object_item(target, detail=False) for target in objects]

    items: list[dict[str, Any]] = []
    for pack_id in pack_ids:
        metadata = request.registries[pack_id].domain_pack.metadata
        if request.object_type is None:
            items.extend(
                {
                    "ref": f"model_definition|{pack_id}|{model.model_id}",
                    "kind": "model_definition",
                    "domain_pack_id": pack_id,
                    **_model_definition(model),
                }
                for model in metadata.model_definitions
            )
            items.extend(
                {
                    "ref": f"enum_definition|{pack_id}|{enum.enum_id}",
                    "kind": "enum_definition",
                    "domain_pack_id": pack_id,
                    **enum.model_dump(mode="json"),
                }
                for enum in metadata.enum_definitions
            )
        for target in objects:
            if target.pack_id != pack_id:
                continue
            items.append(_object_item(target, detail=True))
            items.extend(
                _field_definition_item(
                    _Target(pack_id, target.registry, target.object_definition, field),
                    detail=True,
                )
                for field in target.object_definition.fields
            )
    return header, items


def _validator_bindings_topic(
    request: _Request,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    pack_ids = _scoped_pack_ids(request, include_validator_packs=True)
    field_targets = _scoped_fields(request, pack_ids) if request.field_path else None
    object_targets = (
        _scoped_objects(request, pack_ids)
        if request.object_type is not None and field_targets is None
        else None
    )

    contributing: list[str] = []
    items: list[dict[str, Any]] = []
    for pack_id in pack_ids:
        registry = request.registries[pack_id]
        targeted = [binding for binding in registry.bindings if _binding_targets_agent(request, binding)]
        # A pack whose bindings name this agent returns only those bindings; a
        # pack the agent owns without being named returns its bindings.
        selected = targeted or list(registry.bindings)
        pack_field_targets: list[_Target] | None = None
        if field_targets is not None:
            pack_field_targets = [t for t in field_targets if t.pack_id == pack_id]
            if not pack_field_targets:
                continue
            covering = {
                binding.binding_id
                for target in pack_field_targets
                for binding in registry.bindings_for_field(target.object_type, target.field_path)
            }
            selected = [binding for binding in selected if binding.binding_id in covering]
        elif object_targets is not None:
            pack_objects = [t for t in object_targets if t.pack_id == pack_id]
            if not pack_objects:
                continue
            covering = {
                binding.binding_id
                for target in pack_objects
                for field in target.object_definition.fields
                for binding in registry.bindings_for_field(target.object_type, field.field_path)
            }
            selected = [binding for binding in selected if binding.binding_id in covering]

        contributing.append(pack_id)
        pack_object_types = (
            {target.object_type for target in object_targets if target.pack_id == pack_id}
            if object_targets is not None
            else None
        )
        if request.detail and pack_field_targets is not None:
            for target in pack_field_targets:
                policy = registry.policy_for(target.object_type, target.field_path)
                if policy is not None:
                    items.append(
                        {
                            "ref": f"field_policy|{pack_id}|{target.object_type}|{target.field_path}",
                            "kind": "field_policy",
                            **policy.identity_details(),
                        }
                    )
        attachments = registry.validation_attachment_options() if request.detail else ()
        items.extend(
            _binding_item(
                pack_id,
                binding,
                targeted=bool(targeted),
                detail=request.detail,
                attachments=attachments,
                field_targets=pack_field_targets,
            )
            for binding in selected
        )
        if request.detail and field_targets is None and object_targets is None:
            items.extend(
                {
                    "ref": f"validator|{pack_id}|{entry.validator_id}",
                    "kind": "validator",
                    "domain_pack_id": pack_id,
                    **entry.identity_details(),
                    "validation_attachments": [
                        option.to_dict()
                        for option in attachments
                        if option.validator_binding_id is None
                        and option.validator_id == entry.validator_id
                    ],
                }
                for entry in registry.validator_metadata
                if not targeted
                or (
                    entry.validator_agent is not None
                    and _validator_agent_matches(
                        entry.validator_agent.to_dict(),
                        agent_id=request.binding_agent_id,
                        package_id=request.package_id,
                    )
                )
            )
        items.extend(
            _unavailable_profile_mapping_items(
                request,
                pack_id,
                field_targets=pack_field_targets,
                object_types=pack_object_types,
            )
        )
    return {"domain_packs": [_pack_header(request, pack_id) for pack_id in contributing]}, items


def _ontology_constraints_topic(
    request: _Request,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    pack_ids = _scoped_pack_ids(request, include_validator_packs=True)
    targets = _scoped_fields(request, pack_ids)
    targeted_ids_by_pack = {
        pack_id: {
            binding.binding_id
            for binding in request.registries[pack_id].bindings
            if _binding_targets_agent(request, binding)
        }
        for pack_id in pack_ids
        if pack_id not in request.owned_pack_ids
    }

    contributing: list[str] = []
    items: list[dict[str, Any]] = []
    for target in targets:
        targeted_ids = targeted_ids_by_pack.get(target.pack_id)
        # In a pack the agent only validates, report the fields its bindings cover.
        if targeted_ids is not None and not any(
            binding.binding_id in targeted_ids
            for binding in target.registry.bindings_for_field(target.object_type, target.field_path)
        ):
            continue
        constraints = _field_constraints(target.registry, target.object_type, target.field_definition)
        if not constraints:
            continue
        item = {
            "ref": f"ontology_constraint|{target.pack_id}|{target.object_type}|{target.field_path}",
            "kind": "ontology_constraint",
            "domain_pack_id": target.pack_id,
            "object_type": target.object_type,
            "field_path": target.field_path,
            "constraints": constraints if request.detail else _compact_constraints(constraints),
        }
        if request.detail:
            item["field"] = _field_definition(target.field_definition, target.registry, target.object_type)
            item["dependent_definitions"] = _dependent_domain_definitions(target)
        items.append(item)
        if target.pack_id not in contributing:
            contributing.append(target.pack_id)
    return {"domain_packs": [_pack_header(request, pack_id) for pack_id in contributing]}, items


def _field_topic(request: _Request) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    pack_ids = _scoped_pack_ids(request, include_validator_packs=True)
    targets = _scoped_fields(request, pack_ids)
    items: list[dict[str, Any]] = []
    contributing: list[str] = []
    for target in targets:
        metadata = target.registry.domain_pack.metadata
        field_payload = _field_definition(target.field_definition, target.registry, target.object_type)
        if not request.detail:
            field_payload = {key: field_payload[key] for key in _FIELD_SUMMARY_KEYS if key in field_payload}
        item = {
            "ref": f"field|{target.pack_id}|{target.object_type}|{target.field_path}",
            "kind": "field",
            "domain_pack_id": target.pack_id,
            "domain_pack_version": metadata.version,
            "object_type": target.object_type,
            "object_display_name": target.object_definition.display_name,
            "field": field_payload,
            "validator_bindings": [
                _binding_item(
                    target.pack_id,
                    binding,
                    targeted=_binding_targets_agent(request, binding),
                    detail=request.detail,
                    attachments=(),
                    field_targets=None,
                    include_attachments=False,
                )
                for binding in target.registry.bindings_for_field(target.object_type, target.field_path)
            ],
        }
        if request.detail:
            item["dependent_definitions"] = _dependent_domain_definitions(target)
        items.append(item)
        if target.pack_id not in contributing:
            contributing.append(target.pack_id)
    return {"domain_packs": [_pack_header(request, pack_id) for pack_id in contributing]}, items


_TOPIC_BUILDERS: Mapping[str, Callable[[_Request], tuple[dict[str, Any], list[dict[str, Any]]]]] = {
    "tools": _tools_topic,
    "output_schema": _output_schema_topic,
    "domain_envelope": _domain_envelope_topic,
    "validator_bindings": _validator_bindings_topic,
    "ontology_constraints": _ontology_constraints_topic,
    "field": _field_topic,
}


# ---------------------------------------------------------------------------
# Custom agents
# ---------------------------------------------------------------------------


def custom_agent_contract_runtime_note(agent_key: str) -> str:
    """Per-run prompt line telling a custom agent the id of its own contract."""

    return (
        f"Your own agent_id is {agent_key}. Call get_agent_contract with "
        f"agent_id={agent_key} to read your saved tools, output structure, "
        "validator bindings and field rules."
    )


def _custom_agent_session():
    from src.models.sql.database import SessionLocal

    return SessionLocal()


def _load_custom_agent(
    agent_id: str,
    *,
    caller: Any,
    agent_registry: Mapping[str, Mapping[str, Any]],
    registries: Mapping[str, DomainPackValidationRegistry],
    output_schema_resolver: Callable[[str], type[BaseModel] | None],
) -> _CustomAgentContract | None:
    """Resolve one custom agent's saved revision, or None when it is not visible.

    The running agent reads the exact revision it was authorized and built
    from (its execution receipt), so its own contract is always retrievable.
    Any other custom agent requires an authenticated curator and is read from
    its saved head through the same visibility and group checks as execution.
    """

    from src.lib.agent_studio.custom_agent_service import parse_custom_agent_id
    from src.lib.agent_studio.custom_profile_validators import runtime_validator_user_id
    from src.lib.agent_studio import execution_revision_service as revisions
    from src.lib.agent_studio.profile_conformance import ProfileIdentityError
    from src.lib.group_tool_policy import resolve_group_tool_policy
    from src.models.sql.agent import Agent as AgentRow

    if parse_custom_agent_id(agent_id) is None:
        return None
    running_receipt = _running_receipt(caller, agent_id)
    active_group_ids = [
        str(group) for group in (getattr(caller, "authenticated_groups", None) or ())
    ]
    user_id = runtime_validator_user_id()
    with _custom_agent_session() as db:
        if running_receipt is not None:
            receipt = running_receipt
            row, saved = _running_revision(db, receipt)
            revision_source = "running_agent"
        else:
            if user_id is None:
                return None
            try:
                receipt = revisions.current_execution_receipt(
                    db, agent_id, user_id, active_group_ids=active_group_ids
                )
                row, saved = revisions.get_execution_revision(
                    db,
                    receipt.agent_id,
                    receipt.agent_revision_id,
                    user_id,
                    active_group_ids=active_group_ids,
                )
            except revisions.ExecutionRevisionNotFoundError:
                return None
            except ValueError as exc:
                raise _ContractRequestError(
                    f"The saved revision of agent {agent_id} could not be read: {exc}"
                ) from exc
            revision_source = "saved_head"
        head = db.get(AgentRow, receipt.agent_id)
        name = _optional_text(getattr(head, "name", None))

        output = saved.output_contract
        curation = dict(saved.curation) if saved.curation else None
        pack_id = _optional_text((curation or {}).get("domain_pack_id"))
        scoped_registries = registries
        unavailable: tuple[Mapping[str, Any], ...] = ()
        profile_ref = None
        if output.output_mode == "profile_bound_generic":
            from src.lib.domain_packs.profile_validation import resolve_profile_validation

            base = registries.get(pack_id) if pack_id else None
            if base is None:
                raise _ContractRequestError(
                    f"Domain pack '{pack_id}' of agent {agent_id}'s saved profile is not "
                    "available in this deployment."
                )
            try:
                context = resolve_profile_validation(
                    receipt,
                    base.domain_pack,
                    db=db,
                    user_id=user_id,
                    active_group_ids=active_group_ids,
                )
            except ProfileIdentityError as exc:
                raise _ContractRequestError(
                    f"The saved profile of agent {agent_id} could not be read: {exc}"
                ) from exc
            assert context is not None  # profile_bound_generic always resolves a context.
            scoped_registries = {pack_id: context.registry}
            unavailable = _unavailable_profile_mappings(pack_id, context)
            profile_ref = context.profile.receipt

    parent = _optional_text(saved.template_source)
    parent_entry = agent_registry.get(parent) if parent else None
    validates_for = (
        parent
        if parent_entry is not None
        and _serves_parent_validator(output, parent_entry, output_schema_resolver)
        else None
    )
    tool_ids = resolve_group_tool_policy(
        saved.tool_ids, saved.group_tool_policy, active_group_ids
    ).tool_ids
    identity: dict[str, Any] = {
        "agent_id": agent_id,
        "name": name,
        "parent_agent_id": parent,
        "execution_revision": row.revision,
        "revision_source": revision_source,
        "output_state": output.output_state,
        "output_mode": output.output_mode,
        "generic_profile_ref": profile_ref,
        "validates_for_agent_id": validates_for,
    }
    if profile_ref is not None:
        identity["unavailable_profile_mapping_count"] = len(unavailable)
    return _CustomAgentContract(
        entry={
            "name": name,
            "tools": list(tool_ids),
            "output_schema": output.output_schema_key,
            "curation": curation,
        },
        identity=identity,
        revision_fingerprint=row.fingerprint,
        registries=scoped_registries,
        binding_agent_id=validates_for,
        binding_package_id=(
            _optional_text(parent_entry.get("package_id")) if validates_for and parent_entry else None
        ),
        method_agent_id=parent if parent_entry is not None else agent_id,
        output_schema_note=_custom_output_note(output, pack_id),
        unavailable_profile_mappings=unavailable,
    )


def _running_receipt(caller: Any, agent_id: str) -> Any:
    """Return the caller's execution receipt when it is the requested agent."""

    if caller is None or getattr(caller, "agent_key", None) != agent_id:
        return None
    raw_receipt = getattr(caller, "execution_receipt", None)
    if raw_receipt is None:
        return None
    from src.schemas.agent_execution_revision import AgentExecutionReceipt

    receipt = AgentExecutionReceipt.model_validate(raw_receipt)
    if receipt.agent_key != agent_id:
        raise _ContractRequestError(
            f"The running agent's execution receipt does not match agent {agent_id}."
        )
    return receipt


def _running_revision(db: Any, receipt: Any) -> tuple[Any, Any]:
    """Read the exact revision the running agent was built from."""

    from src.models.sql.agent_execution_revision import AgentExecutionRevision
    from src.schemas.agent_execution_revision import AgentExecutionSnapshot

    row = db.get(AgentExecutionRevision, receipt.agent_revision_id)
    if (
        row is None
        or row.agent_id != receipt.agent_id
        or row.fingerprint != receipt.fingerprint
    ):
        raise _ContractRequestError(
            f"The saved revision of agent {receipt.agent_key} does not match the "
            "running agent's execution receipt."
        )
    saved = AgentExecutionSnapshot.model_validate(row.snapshot)
    if saved.fingerprint() != row.fingerprint:
        raise _ContractRequestError(
            f"The saved revision of agent {receipt.agent_key} does not match its "
            "recorded fingerprint."
        )
    return row, saved


def _serves_parent_validator(
    output: Any,
    parent_entry: Mapping[str, Any],
    output_schema_resolver: Callable[[str], type[BaseModel] | None],
) -> bool:
    """Whether a saved agent keeps its packaged validator parent's result contract.

    Mirrors the custom validator capability rule: only a domain-output revision
    that retains the parent's validator result schema can serve its bindings.
    """

    from src.schemas.domain_validator import is_domain_validator_result_schema

    schema_key = output.output_schema_key
    if output.output_mode != "domain" or not schema_key:
        return False
    if schema_key != _entry_output_schema(parent_entry):
        return False
    return is_domain_validator_result_schema(output_schema_resolver(schema_key))


def _unavailable_profile_mappings(pack_id: str, context: Any) -> tuple[dict[str, Any], ...]:
    from src.lib.domain_packs.profile_validation import profile_mapping_binding_id

    items: list[dict[str, Any]] = []
    for unavailable in context.unavailable:
        mapping = unavailable.mapping
        binding_id = profile_mapping_binding_id(context.profile, mapping)
        declared = [item.field_path for item in mapping.inputs.values() if item.source == "field"]
        declared.extend(mapping.outputs.values())
        items.append(
            {
                "ref": f"profile_mapping|{pack_id}|{binding_id}",
                "kind": "profile_validator_mapping",
                "domain_pack_id": pack_id,
                "object_type": "generic_object",
                "validator_binding_id": binding_id,
                "mapping_id": mapping.mapping_id,
                "available": False,
                "unavailable_reasons": list(unavailable.reasons),
                "field_paths": sorted({path.replace("[]", "") for path in declared if path}),
                "profile_validator_mapping": mapping.model_dump(mode="json"),
            }
        )
    return tuple(items)


def _custom_output_note(output: Any, pack_id: str | None) -> str | None:
    if output.output_state == "none":
        return "This agent's saved configuration has no structured output."
    if output.output_mode == "profile_bound_generic":
        return (
            "Output follows the agent's saved profile. Read its fields with "
            f"topic=domain_envelope or topic=field in domain pack '{pack_id}'."
        )
    if output.output_mode == "unprofiled_generic":
        return (
            "Output is an open generic extraction. Read its structure with "
            f"topic=domain_envelope in domain pack '{pack_id}'."
        )
    if output.output_schema_key is None:
        return (
            "Output is built with builder tools. Read its fields with "
            f"topic=domain_envelope or topic=field in domain pack '{pack_id}'."
        )
    return None


# ---------------------------------------------------------------------------
# Scope resolution
# ---------------------------------------------------------------------------


def _scoped_pack_ids(request: _Request, *, include_validator_packs: bool) -> list[str]:
    candidates = [*request.owned_pack_ids]
    if include_validator_packs:
        candidates.extend(request.validator_pack_ids)
    available = [pack_id for pack_id in _ordered_unique(candidates) if pack_id in request.registries]
    if request.domain_pack_id is not None:
        if request.domain_pack_id not in available:
            raise _ContractRequestError(
                f"Domain pack '{request.domain_pack_id}' is not in scope for agent "
                f"{request.agent_id} and topic '{request.topic}'.",
                available_domain_pack_ids=available,
            )
        return [request.domain_pack_id]
    if not available and (request.object_type or request.field_path):
        raise _ContractRequestError(
            f"Agent {request.agent_id} has no domain packs in scope for topic "
            f"'{request.topic}', so object_type and field_path cannot be resolved.",
            available_domain_pack_ids=[],
        )
    return available


def _scoped_objects(request: _Request, pack_ids: Sequence[str]) -> list[_Target]:
    objects = [
        _Target(pack_id, request.registries[pack_id], object_definition)
        for pack_id in pack_ids
        for object_definition in request.registries[pack_id].domain_pack.metadata.object_definitions
    ]
    if request.object_type is None:
        return objects
    selected = [target for target in objects if target.object_type == request.object_type]
    if not selected:
        available = sorted({target.object_type for target in objects})
        raise _ContractRequestError(
            f"Object type '{request.object_type}' was not found for agent "
            f"{request.agent_id} in domain packs {list(pack_ids)}.",
            searched_domain_pack_ids=list(pack_ids),
            available_object_types=available[:_MAX_LISTED_NAMES],
            available_object_type_count=len(available),
        )
    return selected


def _scoped_fields(request: _Request, pack_ids: Sequence[str]) -> list[_Target]:
    fields = [
        _Target(target.pack_id, target.registry, target.object_definition, field_definition)
        for target in _scoped_objects(request, pack_ids)
        for field_definition in target.object_definition.fields
    ]
    requested = request.field_path
    if requested is None:
        return fields
    selected = [
        target
        for target in fields
        if requested in {target.field_path, f"{target.object_type}.{target.field_path}"}
    ]
    if not selected:
        candidates = sorted(
            {target.field_path for target in fields}
            | {f"{target.object_type}.{target.field_path}" for target in fields}
        )
        raise _ContractRequestError(
            f"Field path '{requested}' was not found for agent {request.agent_id} in "
            f"domain packs {list(pack_ids)}. Nothing was returned for other fields; "
            "use one of suggested_field_paths, or list field paths with "
            "topic=ontology_constraints or topic=domain_envelope.",
            field_path=requested,
            searched_domain_pack_ids=list(pack_ids),
            suggested_field_paths=_suggest(requested, candidates),
        )
    return selected


def _binding_targets_agent(request: _Request, binding: ValidatorBinding) -> bool:
    return any(
        _validator_agent_matches(
            ref.to_dict(),
            agent_id=request.binding_agent_id,
            package_id=request.package_id,
        )
        for ref in binding.validator_agents()
    )


def _unavailable_profile_mapping_items(
    request: _Request,
    pack_id: str,
    *,
    field_targets: Sequence[_Target] | None,
    object_types: set[str] | None,
) -> list[dict[str, Any]]:
    """Saved profile mappings whose validator cannot run stay explicit entries."""

    if request.custom_agent is None:
        return []
    items: list[dict[str, Any]] = []
    for mapping in request.custom_agent.unavailable_profile_mappings:
        if mapping["domain_pack_id"] != pack_id:
            continue
        if object_types is not None and mapping["object_type"] not in object_types:
            continue
        if field_targets is not None and not any(
            _declared_path_covers(path, target.field_path)
            for target in field_targets
            for path in mapping["field_paths"]
        ):
            continue
        if request.detail:
            items.append(dict(mapping))
        else:
            items.append(
                {key: value for key, value in mapping.items() if key != "profile_validator_mapping"}
            )
    return items


def _declared_path_covers(declared: str, field_path: str) -> bool:
    return declared == field_path or declared.startswith(f"{field_path}.")


def _validator_domain_pack_ids(
    agent_id: str | None,
    *,
    package_id: str | None,
    registries: Mapping[str, DomainPackValidationRegistry],
) -> list[str]:
    if agent_id is None:
        return []
    pack_ids: list[str] = []
    for pack_id, registry in sorted(registries.items()):
        for binding in registry.bindings:
            if any(
                _validator_agent_matches(
                    ref.to_dict(),
                    agent_id=agent_id,
                    package_id=package_id,
                )
                for ref in binding.validator_agents()
            ):
                pack_ids.append(pack_id)
                break
    return _ordered_unique(pack_ids)


def _owned_domain_pack_ids(entry: Mapping[str, Any]) -> list[str]:
    curation = entry.get("curation")
    if not isinstance(curation, Mapping):
        return []
    domain_pack_id = _optional_text(curation.get("domain_pack_id"))
    return [domain_pack_id] if domain_pack_id else []


def _validator_agent_matches(
    value: Any,
    *,
    agent_id: str | None,
    package_id: str | None,
) -> bool:
    if agent_id is None or not isinstance(value, Mapping):
        return False
    if value.get("agent_id") != agent_id:
        return False
    if package_id is None:
        return True
    return value.get("package_id") in {None, package_id}


def _suggest(requested: str, candidates: Sequence[str]) -> list[str]:
    return difflib.get_close_matches(requested, list(candidates), n=_MAX_SUGGESTIONS, cutoff=0.6)


# ---------------------------------------------------------------------------
# Paging, budgets and drilldown
# ---------------------------------------------------------------------------


def _page_response(
    response: dict[str, Any],
    items: Sequence[dict[str, Any]],
    *,
    limit: int | None,
    cursor: str | None,
    fingerprint: str,
    repeat_arguments: Mapping[str, Any],
) -> dict[str, Any]:
    item_budget = get_agent_contract_max_item_chars()
    page, envelope = _bounded_page(
        response,
        items,
        limit=limit,
        cursor=cursor,
        fingerprint=fingerprint,
        present=lambda item: _bounded_item(item, item_budget),
    )
    result = {**response, "items": page, "page": envelope}
    if any(item.get("detail_complete") is False for item in page):
        result["drilldown"] = {
            "message": (
                "Items with detail_complete=false omit values larger than "
                "AGENT_CONTRACT_MAX_ITEM_CHARS. Repeat these arguments with "
                "item_ref=<item ref> and detail_pointer=<omitted_values[].detail_pointer> "
                "to read one exactly."
            ),
            "repeat_arguments": dict(repeat_arguments),
        }
    return result


def _item_detail_response(
    response: dict[str, Any],
    items: Sequence[dict[str, Any]],
    *,
    item_ref: str,
    detail_pointer: str,
    limit: int | None,
    cursor: str | None,
    fingerprint: str,
) -> dict[str, Any]:
    item = next((candidate for candidate in items if candidate["ref"] == item_ref), None)
    if item is None:
        raise _ContractRequestError(
            f"item_ref '{item_ref}' was not found for this agent, topic, "
            "detail_level and selectors. Repeat the exact arguments of the call "
            "that returned it.",
            item_ref=item_ref,
        )
    value = _resolve_pointer(item, detail_pointer)
    item_budget = get_agent_contract_max_item_chars()
    result = {
        **response,
        "item_ref": item_ref,
        "detail_pointer": detail_pointer,
        "value_type": _value_type(value),
    }
    value_chars = _serialized_chars(value)
    if value_chars <= item_budget:
        _cursor_offset(cursor, fingerprint=fingerprint, total=1)
        return {**result, "value": value, "detail_complete": True}

    if isinstance(value, Mapping):
        entry_key, children = "key", list(value.items())
    elif isinstance(value, list):
        entry_key, children = "index", list(enumerate(value))
    else:
        raise PayloadContractViolation(
            category="tool_result_budget_escape",
            component="agent_contracts.drilldown",
            message=(
                "A single contract value exceeds AGENT_CONTRACT_MAX_ITEM_CHARS and "
                "cannot be split without slicing it."
            ),
            measured=value_chars,
            limit=item_budget,
            setting="AGENT_CONTRACT_MAX_ITEM_CHARS",
            field=f"{item_ref}{detail_pointer}",
        )
    entries: list[dict[str, Any]] = []
    for key, child in children:
        child_chars = _serialized_chars(child)
        if child_chars <= item_budget:
            entries.append({entry_key: key, "value": child})
        else:
            entries.append(
                {
                    entry_key: key,
                    "detail_complete": False,
                    "detail_pointer": _pointer_child(detail_pointer, key),
                    "value_type": _value_type(child),
                    "serialized_chars": child_chars,
                }
            )
    page, envelope = _bounded_page(
        result,
        entries,
        limit=limit,
        cursor=cursor,
        fingerprint=fingerprint,
        present=lambda entry: entry,
    )
    return {**result, "detail_complete": False, "entries": page, "page": envelope}


def _bounded_page(
    response: Mapping[str, Any],
    items: Sequence[dict[str, Any]],
    *,
    limit: int | None,
    cursor: str | None,
    fingerprint: str,
    present: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    bounded_limit = normalize_page_limit(
        limit,
        default=get_tool_page_default_limit(),
        maximum=get_tool_page_max_limit(),
    )
    offset = _cursor_offset(cursor, fingerprint=fingerprint, total=len(items))
    page_budget = (
        get_agent_contract_max_response_chars()
        - _serialized_chars(response)
        - _PAGE_ENVELOPE_RESERVE_CHARS
    )
    page: list[dict[str, Any]] = []
    used = 0
    for item in items[offset : offset + bounded_limit]:
        presented = present(item)
        size = _serialized_chars(presented) + 2
        if page and used + size > page_budget:
            break
        page.append(presented)
        used += size
    next_offset = offset + len(page)
    has_more = next_offset < len(items)
    envelope = bounded_envelope(
        page,
        total_count=len(items),
        offset=offset,
        has_more=has_more,
        next_cursor=f"{next_offset}:{fingerprint}" if has_more else None,
    )
    envelope["offset"] = offset
    envelope["limit"] = bounded_limit
    if not has_more:
        envelope["stopped_by"] = "end"
    elif len(page) == bounded_limit:
        envelope["stopped_by"] = "limit"
    else:
        envelope["stopped_by"] = "response_budget"
    return page, envelope


def _bounded_item(item: dict[str, Any], item_budget: int) -> dict[str, Any]:
    size = _serialized_chars(item)
    if size <= item_budget:
        return item
    identity = {
        "ref": item["ref"],
        "kind": item["kind"],
        "detail_complete": False,
        "serialized_chars": size,
    }
    outline = dict(identity)
    omitted: list[dict[str, Any]] = []
    for key, value in item.items():
        if key in identity:
            continue
        value_chars = _serialized_chars(value)
        projected = (
            _serialized_chars(outline)
            + len(key)
            + value_chars
            + _OUTLINE_STUB_RESERVE_CHARS * (len(omitted) + 1)
        )
        if projected <= item_budget:
            outline[key] = value
        else:
            omitted.append(
                {
                    "key": key,
                    "detail_pointer": _pointer_child("", key),
                    "value_type": _value_type(value),
                    "serialized_chars": value_chars,
                }
            )
    outline["omitted_values"] = omitted
    if _serialized_chars(outline) <= item_budget:
        return outline
    return {
        **identity,
        "omitted_values": [
            {"key": None, "detail_pointer": "", "value_type": "object", "serialized_chars": size}
        ],
    }


def _within_response_budget(response: dict[str, Any]) -> dict[str, Any]:
    budget = get_agent_contract_max_response_chars()
    measured = _serialized_chars(response)
    if measured > budget:
        raise PayloadContractViolation(
            category="tool_result_budget_escape",
            component="agent_contracts",
            message="get_agent_contract response exceeded its serialized budget.",
            measured=measured,
            limit=budget,
            setting="AGENT_CONTRACT_MAX_RESPONSE_CHARS",
            field=str(response.get("topic")),
        )
    return response


def _cursor_offset(cursor: Any, *, fingerprint: str, total: int) -> int:
    """Validate a contract cursor locally; malformed or foreign cursors fail."""

    if cursor is None:
        return 0
    if not isinstance(cursor, str):
        raise _ContractRequestError(
            "cursor must be the page.next_cursor string from a previous response.",
            cursor=str(cursor),
        )
    text = cursor.strip()
    if not text:
        return 0
    match = _CURSOR_PATTERN.fullmatch(text)
    if match is None:
        raise _ContractRequestError(
            f"cursor '{text}' is not a get_agent_contract cursor. Pass "
            "page.next_cursor from the previous response unchanged, or omit "
            "cursor to start from the first page.",
            cursor=text,
        )
    offset, cursor_fingerprint = int(match.group(1)), match.group(2)
    if cursor_fingerprint != fingerprint:
        raise _ContractRequestError(
            f"cursor '{text}' belongs to a different request. Repeat the same "
            "agent, topic, detail_level, selectors, item_ref and detail_pointer, "
            "or omit cursor to start from the first page.",
            cursor=text,
        )
    if offset > 0 and offset >= total:
        raise _ContractRequestError(
            f"cursor '{text}' is past the end of the {total} available entries. "
            "Omit cursor to start from the first page.",
            cursor=text,
            total_count=total,
        )
    return offset


def _request_fingerprint(
    request: _Request,
    *,
    item_refs: Sequence[str],
    item_ref: str | None,
    detail_pointer: str | None,
) -> str:
    pack_versions = sorted(
        (pack_id, request.registries[pack_id].domain_pack.metadata.version)
        for pack_id in {*request.owned_pack_ids, *request.validator_pack_ids}
        if pack_id in request.registries
    )
    parts: list[Any] = [
        request.agent_id,
        request.topic,
        request.detail_level,
        request.domain_pack_id,
        request.object_type,
        request.field_path,
        item_ref,
        detail_pointer or "",
        pack_versions,
        list(item_refs),
    ]
    if request.custom_agent is not None:
        parts.append(request.custom_agent.revision_fingerprint)
    material = json.dumps(parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


def _serialized_chars(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=True))
    except (TypeError, ValueError) as exc:
        raise PayloadContractViolation(
            category="contract_serialization_failure",
            component="agent_contracts",
            message=f"Contract metadata is not JSON-serializable: {exc}",
        ) from exc


def _pointer_child(pointer: str, key: Any) -> str:
    token = str(key).replace("~", "~0").replace("/", "~1")
    return f"{pointer}/{token}"


def _resolve_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise _ContractRequestError(
            "detail_pointer must be a JSON pointer such as '/input_fields', or "
            "empty for the whole item.",
            detail_pointer=pointer,
        )
    current = document
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping) and token in current:
            current = current[token]
        elif (
            isinstance(current, list)
            and _LIST_INDEX_PATTERN.fullmatch(token)
            and int(token) < len(current)
        ):
            current = current[int(token)]
        else:
            available = sorted(str(key) for key in current) if isinstance(current, Mapping) else []
            raise _ContractRequestError(
                f"detail_pointer '{pointer}' does not exist in this item.",
                detail_pointer=pointer,
                available_keys=available[:_MAX_LISTED_NAMES],
                list_length=len(current) if isinstance(current, list) else None,
            )
    return current


def _value_type(value: Any) -> str:
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "null" if value is None else type(value).__name__


# ---------------------------------------------------------------------------
# Item shapes
# ---------------------------------------------------------------------------


def _pack_header(
    request: _Request,
    pack_id: str,
    *,
    include_schema_refs: bool = False,
) -> dict[str, Any]:
    metadata = request.registries[pack_id].domain_pack.metadata
    header = {
        "domain_pack_id": metadata.pack_id,
        "domain_pack_version": metadata.version,
        "display_name": metadata.display_name,
        "status": metadata.status.value,
        "relation": "owned" if pack_id in request.owned_pack_ids else "validator",
    }
    if include_schema_refs:
        header["metadata_api_version"] = metadata.metadata_api_version
        header["schema_refs"] = [_model_dump(schema_ref) for schema_ref in metadata.schema_refs]
    return header


def _binding_item(
    pack_id: str,
    binding: ValidatorBinding,
    *,
    targeted: bool,
    detail: bool,
    attachments: Sequence[ValidationAttachmentOption],
    field_targets: Sequence[_Target] | None,
    include_attachments: bool = True,
) -> dict[str, Any]:
    details = binding.identity_details()
    identity = {
        "ref": f"binding|{pack_id}|{binding.binding_id}",
        "kind": "validator_binding",
        "domain_pack_id": pack_id,
        "targeted_to_agent": targeted,
    }
    if not detail:
        return {**identity, **{key: details[key] for key in _BINDING_SUMMARY_KEYS if key in details}}
    item = {**identity, **details}
    profile_validation = binding.raw.get("profile_validation")
    if profile_validation is not None:
        item["profile_validation"] = profile_validation
    if include_attachments:
        item["validation_attachments"] = [
            option.to_dict()
            for option in attachments
            if option.validator_binding_id == binding.binding_id
            and _attachment_in_scope(option, field_targets)
        ]
    return item


def _attachment_in_scope(
    option: ValidationAttachmentOption,
    field_targets: Sequence[_Target] | None,
) -> bool:
    if field_targets is None:
        return True
    return any(
        option.object_type in {None, target.object_type}
        and (option.field_path == target.field_path or target.field_path in option.affected_fields)
        for target in field_targets
    )


def _object_item(target: _Target, *, detail: bool) -> dict[str, Any]:
    object_definition = target.object_definition
    item = {
        "ref": f"object|{target.pack_id}|{object_definition.object_type}",
        "kind": "object_definition",
        "domain_pack_id": target.pack_id,
        **_object_summary(object_definition),
        "field_count": len(object_definition.fields),
    }
    if detail:
        item["schema_ref"] = _model_dump(object_definition.schema_ref)
        item["definition_notes"] = list(object_definition.definition_notes)
        item["provider_refs"] = _provider_refs(object_definition.metadata)
        model = _model_definition_by_id(target.registry, object_definition.model_ref)
        item["dependent_definitions"] = {"model_definition": model} if model else {}
    return item


def _field_definition_item(target: _Target, *, detail: bool) -> dict[str, Any]:
    field_payload = _field_definition(target.field_definition, target.registry, target.object_type)
    if not detail:
        field_payload = {key: field_payload[key] for key in _FIELD_SUMMARY_KEYS if key in field_payload}
    item = {
        "ref": f"field|{target.pack_id}|{target.object_type}|{target.field_path}",
        "kind": "field_definition",
        "domain_pack_id": target.pack_id,
        "object_type": target.object_type,
        **field_payload,
    }
    if detail:
        item["dependent_definitions"] = _dependent_domain_definitions(target)
    return item


def _dependent_domain_definitions(target: _Target) -> dict[str, Any]:
    """Return the model, enum and object-type definitions one field refers to."""

    field_definition = target.field_definition
    metadata = target.registry.domain_pack.metadata
    dependents: dict[str, Any] = {}
    model = _model_definition_by_id(target.registry, field_definition.model_ref)
    if model is not None:
        dependents["model_definition"] = model
    if field_definition.enum_ref:
        enum = next(
            (item for item in metadata.enum_definitions if item.enum_id == field_definition.enum_ref),
            None,
        )
        if enum is not None:
            dependents["enum_definition"] = enum.model_dump(mode="json")
    if field_definition.object_type_ref:
        referenced = target.registry.object_definitions_by_type.get(field_definition.object_type_ref)
        if referenced is not None:
            dependents["object_type_ref"] = {
                "object_type": referenced.object_type,
                "display_name": referenced.display_name,
                "description": referenced.description,
                "model_ref": referenced.model_ref,
                "field_count": len(referenced.fields),
            }
    return dependents


def _model_definition_by_id(
    registry: DomainPackValidationRegistry,
    model_id: str | None,
) -> dict[str, Any] | None:
    if not model_id:
        return None
    model = next(
        (item for item in registry.domain_pack.metadata.model_definitions if item.model_id == model_id),
        None,
    )
    return _model_definition(model) if model is not None else None


def _compact_constraints(constraints: Mapping[str, Any]) -> dict[str, Any]:
    compact = {
        key: constraints[key]
        for key in ("field_type", "enum_ref", "model_ref", "object_type_ref", "source_of_truth")
        if key in constraints
    }
    policy = constraints.get("validation_policy")
    if isinstance(policy, Mapping):
        compact["validation"] = {
            key: policy[key]
            for key in ("required", "blocking", "validator_binding_ids")
            if key in policy
        }
    return compact


def _reject_schema_field(request: _Request, reason: str, *, hint: str | None = None) -> None:
    if request.field_path is not None:
        raise _ContractRequestError(
            f"Agent {request.agent_id} {reason}, so field_path "
            f"'{request.field_path}' cannot be resolved.",
            field_path=request.field_path,
            **({"hint": hint} if hint else {}),
        )


def _default_agent_registry() -> Mapping[str, Mapping[str, Any]]:
    from src.lib.agent_studio.catalog_service import AGENT_REGISTRY

    return AGENT_REGISTRY


def _tool_details(agent_id: str, tool_id: str) -> Mapping[str, Any] | None:
    from src.lib.agent_studio.catalog_service import get_tool_for_agent

    return get_tool_for_agent(tool_id, agent_id)


def _entry_output_schema(entry: Mapping[str, Any]) -> str | None:
    return _optional_text(entry.get("output_schema") or entry.get("output_schema_key"))


def _output_schema_name(agent_id: str, entry: Mapping[str, Any]) -> str | None:
    direct = _entry_output_schema(entry)
    if direct:
        return direct

    from src.lib.config.agent_loader import get_agent_by_folder, get_agent_definition

    definition = get_agent_definition(agent_id) or get_agent_by_folder(agent_id)
    if definition is None:
        return None
    return _optional_text(definition.output_schema)


def _resolve_output_schema(schema_name: str) -> type[BaseModel] | None:
    from src.lib.config.schema_discovery import resolve_output_schema

    return resolve_output_schema(schema_name)


def _field_constraints(
    registry: DomainPackValidationRegistry,
    object_type: str,
    field_definition: Any,
) -> dict[str, Any]:
    constraints: dict[str, Any] = {}
    for key, attr_name in (
        ("field_type", "field_type"),
        ("enum_ref", "enum_ref"),
        ("model_ref", "model_ref"),
        ("object_type_ref", "object_type_ref"),
    ):
        value = getattr(field_definition, attr_name, None)
        if hasattr(value, "value"):
            value = value.value
        if value is not None:
            constraints[key] = value
    provider_refs = _provider_refs(getattr(field_definition, "metadata", {}))
    if provider_refs:
        constraints["provider_refs"] = provider_refs
    source_of_truth = _optional_text(getattr(field_definition, "metadata", {}).get("source_of_truth"))
    if source_of_truth:
        constraints["source_of_truth"] = source_of_truth
    policy = registry.policy_for(object_type, field_definition.field_path)
    if policy is not None:
        constraints["validation_policy"] = policy.identity_details()
    return constraints


def _object_summary(object_definition: Any) -> dict[str, Any]:
    return {
        "object_type": object_definition.object_type,
        "display_name": object_definition.display_name,
        "description": object_definition.description,
        "model_ref": object_definition.model_ref,
        "definition_state": object_definition.definition_state.value,
        "field_paths": [field.field_path for field in object_definition.fields],
    }


def _model_definition(model_definition: Any) -> dict[str, Any]:
    return {
        "model_id": model_definition.model_id,
        "display_name": model_definition.display_name,
        "description": model_definition.description,
        "schema_ref": _model_dump(model_definition.schema_ref),
        "definition_state": model_definition.definition_state.value,
        "definition_notes": list(model_definition.definition_notes),
        "provider_refs": _provider_refs(model_definition.metadata),
    }


def _field_definition(
    field_definition: Any,
    registry: DomainPackValidationRegistry,
    object_type: str,
) -> dict[str, Any]:
    policy = registry.policy_for(object_type, field_definition.field_path)
    field_type = field_definition.field_type
    return {
        "field_path": field_definition.field_path,
        "display_name": field_definition.display_name,
        "description": field_definition.description,
        "field_type": field_type.value if hasattr(field_type, "value") else field_type,
        "required": field_definition.required,
        "enum_ref": field_definition.enum_ref,
        "model_ref": field_definition.model_ref,
        "object_type_ref": field_definition.object_type_ref,
        "definition_state": field_definition.definition_state.value,
        "definition_notes": list(field_definition.definition_notes),
        "provider_refs": _provider_refs(field_definition.metadata),
        "source_of_truth": _optional_text(field_definition.metadata.get("source_of_truth")),
        "validation_policy": policy.identity_details() if policy is not None else None,
    }


def _json_schema_field_summary(
    name: str,
    value: Mapping[str, Any],
    *,
    required: bool,
) -> dict[str, Any]:
    return {
        "field_path": name,
        "title": value.get("title"),
        "description": value.get("description"),
        "type": value.get("type"),
        "required": required,
    }


def _json_schema_field_node(
    schema: Mapping[str, Any],
    field_path: str,
) -> tuple[str, Mapping[str, Any], bool] | None:
    current: Mapping[str, Any] = schema
    required: set[str] = set(schema.get("required") or [])
    segments = field_path.split(".")
    for index, segment in enumerate(segments):
        properties = current.get("properties")
        if not isinstance(properties, Mapping):
            return None
        child = properties.get(segment)
        if not isinstance(child, Mapping):
            return None
        if index == len(segments) - 1:
            return segment, child, segment in required
        resolved = _resolve_json_schema_ref(schema, child)
        current = resolved if resolved is not None else child
        required = set(current.get("required") or [])
    return None


def _resolve_json_schema_ref(
    root_schema: Mapping[str, Any],
    value: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    refs = [value.get("$ref")]
    for combinator in ("anyOf", "allOf", "oneOf"):
        options = value.get(combinator)
        if isinstance(options, list):
            refs.extend(option.get("$ref") for option in options if isinstance(option, Mapping))
    defs = root_schema.get("$defs")
    if not isinstance(defs, Mapping):
        return None
    for ref in refs:
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            resolved = defs.get(ref.removeprefix("#/$defs/"))
            if isinstance(resolved, Mapping):
                return resolved
    return None


def _dependent_schema_definitions(
    root_schema: Mapping[str, Any],
    node: Mapping[str, Any],
) -> dict[str, Any]:
    """Return every ``$defs`` entry the node needs, transitively, by name."""

    defs = root_schema.get("$defs")
    if not isinstance(defs, Mapping):
        return {}
    collected: dict[str, Any] = {}
    pending = _schema_ref_names(node)
    while pending:
        name = pending.pop(0)
        if name in collected or not isinstance(defs.get(name), Mapping):
            continue
        collected[name] = defs[name]
        pending.extend(_schema_ref_names(defs[name]))
    return {name: collected[name] for name in sorted(collected)}


def _schema_ref_names(value: Any) -> list[str]:
    names: list[str] = []
    if isinstance(value, Mapping):
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            names.append(ref.removeprefix("#/$defs/"))
        for child in value.values():
            names.extend(_schema_ref_names(child))
    elif isinstance(value, list):
        for child in value:
            names.extend(_schema_ref_names(child))
    return names


def _compact_documentation(documentation: Mapping[str, Any]) -> dict[str, Any]:
    compact = {}
    for key in ("summary", "example_queries"):
        value = documentation.get(key)
        if value:
            compact[key] = value
    parameters = documentation.get("parameters")
    if isinstance(parameters, list):
        compact["parameters"] = [
            {
                item_key: item[item_key]
                for item_key in ("name", "type", "required", "description")
                if isinstance(item, Mapping) and item_key in item
            }
            for item in parameters
            if isinstance(item, Mapping)
        ]
    return compact


def _method_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value[key]
        for key in ("name", "description", "required_params", "optional_params", "example")
        if key in value
    }


def _provider_refs(metadata: Mapping[str, Any]) -> dict[str, Any]:
    provider_refs = metadata.get("provider_refs")
    return dict(provider_refs) if isinstance(provider_refs, Mapping) else {}


def _model_dump(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, Mapping):
        return dict(value)
    return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [text for item in value if (text := _optional_text(item))]


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _required_text(value: Any, field_name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f"{field_name} is required")
    return text


def _ordered_unique(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return {
        "success": False,
        "error": message,
        "read_only": True,
        "deterministic": True,
        "live_state": False,
        "writes": False,
        **extra,
    }


__all__ = [
    "AGENT_CONTRACT_TOPICS",
    "TOPIC_SELECTORS",
    "get_agent_contract",
    "get_domain_pack_field_info",
    "get_extraction_contract",
]
