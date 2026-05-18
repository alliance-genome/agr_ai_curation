"""Read-only contract details for generated system agents."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Callable, Mapping

from pydantic import BaseModel

from src.lib.flows.validation_attachments import domain_pack_validation_registries
from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry


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


def get_agent_contract(
    agent_id: str,
    topic: str,
    field_path: str | None = None,
    detail_level: str = "summary",
    *,
    agent_registry: Mapping[str, Mapping[str, Any]] | None = None,
    registries: Mapping[str, DomainPackValidationRegistry] | None = None,
    tool_details_resolver: Callable[[str, str], Mapping[str, Any] | None] | None = None,
    output_schema_resolver: Callable[[str], type[BaseModel] | None] | None = None,
) -> dict[str, Any]:
    """Return deterministic read-only contract metadata for one runtime agent."""

    try:
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_topic = _required_text(topic, "topic").lower()
    except ValueError as exc:
        return _error(str(exc))
    normalized_detail_level = _optional_text(detail_level)
    normalized_field_path = _optional_text(field_path)

    if normalized_topic not in AGENT_CONTRACT_TOPICS:
        return _error(
            f"Unsupported contract topic '{normalized_topic}'.",
            agent_id=normalized_agent_id,
            topic=normalized_topic,
            detail_level=normalized_detail_level,
            allowed_topics=sorted(AGENT_CONTRACT_TOPICS),
        )
    if normalized_detail_level not in DETAIL_LEVELS:
        return _error(
            f"Unsupported detail_level '{normalized_detail_level}'.",
            agent_id=normalized_agent_id,
            topic=normalized_topic,
            detail_level=normalized_detail_level,
            allowed_detail_levels=sorted(DETAIL_LEVELS),
        )

    resolved_agent_registry = agent_registry or _default_agent_registry()
    entry = resolved_agent_registry.get(normalized_agent_id)
    if entry is None:
        return _error(
            f"Agent {normalized_agent_id} was not found.",
            agent_id=normalized_agent_id,
            topic=normalized_topic,
            detail_level=normalized_detail_level,
        )

    resolved_registries = registries if registries is not None else domain_pack_validation_registries()
    package_id = _optional_text(entry.get("package_id"))
    owned_domain_pack_ids = _owned_domain_pack_ids(entry)
    validator_domain_pack_ids = _validator_domain_pack_ids(
        normalized_agent_id,
        package_id=package_id,
        registries=resolved_registries,
    )

    base = {
        "success": True,
        "agent_id": normalized_agent_id,
        "topic": normalized_topic,
        "detail_level": normalized_detail_level,
        "read_only": True,
        "deterministic": True,
        "live_state": False,
        "writes": False,
    }

    if normalized_topic == "tools":
        return {
            **base,
            "tools": _tool_contracts(
                normalized_agent_id,
                entry,
                detail_level=normalized_detail_level,
                tool_details_resolver=tool_details_resolver,
            ),
        }

    if normalized_topic == "output_schema":
        return {
            **base,
            **_output_schema_contract(
                normalized_agent_id,
                entry,
                field_path=normalized_field_path,
                detail_level=normalized_detail_level,
                output_schema_resolver=output_schema_resolver,
            ),
        }

    if normalized_topic == "domain_envelope":
        return {
            **base,
            "domain_packs": [
                _domain_envelope_contract(resolved_registries[pack_id], normalized_detail_level)
                for pack_id in owned_domain_pack_ids
                if pack_id in resolved_registries
            ],
        }

    if normalized_topic == "validator_bindings":
        relevant_pack_ids = _ordered_unique([*owned_domain_pack_ids, *validator_domain_pack_ids])
        return {
            **base,
            "domain_packs": [
                _validator_binding_contract(
                    resolved_registries[pack_id],
                    normalized_agent_id,
                    package_id=package_id,
                    detail_level=normalized_detail_level,
                )
                for pack_id in relevant_pack_ids
                if pack_id in resolved_registries
            ],
        }

    if normalized_topic == "ontology_constraints":
        relevant_pack_ids = _ordered_unique([*owned_domain_pack_ids, *validator_domain_pack_ids])
        return {
            **base,
            "domain_packs": [
                _ontology_constraints_contract(
                    resolved_registries[pack_id],
                    detail_level=normalized_detail_level,
                )
                for pack_id in relevant_pack_ids
                if pack_id in resolved_registries
            ],
        }

    if normalized_topic == "field":
        if normalized_field_path is None:
            return _error(
                "field_path is required when topic is 'field'.",
                agent_id=normalized_agent_id,
                topic=normalized_topic,
                detail_level=normalized_detail_level,
            )
        relevant_pack_ids = _ordered_unique([*owned_domain_pack_ids, *validator_domain_pack_ids])
        matches = [
            _field_contract(
                resolved_registries[pack_id],
                normalized_field_path,
                detail_level=normalized_detail_level,
            )
            for pack_id in relevant_pack_ids
            if pack_id in resolved_registries
        ]
        matches = [match for match in matches if match is not None]
        if not matches:
            return _error(
                f"Field path '{normalized_field_path}' was not found for agent {normalized_agent_id}.",
                agent_id=normalized_agent_id,
                topic=normalized_topic,
                detail_level=normalized_detail_level,
                field_path=normalized_field_path,
            )
        return {**base, "field_path": normalized_field_path, "matches": matches}

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


def _default_agent_registry() -> Mapping[str, Mapping[str, Any]]:
    from src.lib.agent_studio.catalog_service import AGENT_REGISTRY

    return AGENT_REGISTRY


def _tool_details(agent_id: str, tool_id: str) -> Mapping[str, Any] | None:
    from src.lib.agent_studio.catalog_service import get_tool_for_agent

    return get_tool_for_agent(tool_id, agent_id)


def _output_schema_name(agent_id: str, entry: Mapping[str, Any]) -> str | None:
    direct = _optional_text(entry.get("output_schema") or entry.get("output_schema_key"))
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


def _tool_contracts(
    agent_id: str,
    entry: Mapping[str, Any],
    *,
    detail_level: str,
    tool_details_resolver: Callable[[str, str], Mapping[str, Any] | None] | None,
) -> list[dict[str, Any]]:
    resolver = tool_details_resolver or _tool_details
    contracts: list[dict[str, Any]] = []
    for tool_id in _string_list(entry.get("tools")):
        details = resolver(agent_id, tool_id)
        if details is None:
            contracts.append(
                {
                    "tool_id": tool_id,
                    "resolved": False,
                    "error": "Tool details were not found.",
                }
            )
            continue
        contract = {
            "tool_id": tool_id,
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
        if detail_level == "detail":
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
        contracts.append(contract)
    return contracts


def _output_schema_contract(
    agent_id: str,
    entry: Mapping[str, Any],
    *,
    field_path: str | None,
    detail_level: str,
    output_schema_resolver: Callable[[str], type[BaseModel] | None] | None,
) -> dict[str, Any]:
    schema_name = _output_schema_name(agent_id, entry)
    if schema_name is None:
        return {
            "output_schema": None,
            "fields": [],
            "field": None,
        }

    resolver = output_schema_resolver or _resolve_output_schema
    schema_type = resolver(schema_name)
    if schema_type is None:
        return {
            "output_schema": schema_name,
            "schema_resolved": False,
            "fields": [],
            "field": None,
        }

    schema = schema_type.model_json_schema()
    properties = schema.get("properties") if isinstance(schema, Mapping) else {}
    required = set(schema.get("required") or []) if isinstance(schema, Mapping) else set()
    fields = [
        _json_schema_field_summary(name, value, required=name in required)
        for name, value in sorted((properties or {}).items())
        if isinstance(value, Mapping)
    ]
    payload: dict[str, Any] = {
        "output_schema": schema_name,
        "schema_resolved": True,
        "fields": fields,
    }
    if detail_level == "detail":
        payload["schema_title"] = schema.get("title")
        payload["schema_description"] = schema.get("description")
    if field_path:
        payload["field"] = _json_schema_field(schema, field_path)
    return payload


def _domain_envelope_contract(
    registry: DomainPackValidationRegistry,
    detail_level: str,
) -> dict[str, Any]:
    metadata = registry.domain_pack.metadata
    contract = {
        "domain_pack_id": metadata.pack_id,
        "domain_pack_version": metadata.version,
        "display_name": metadata.display_name,
        "status": metadata.status.value,
        "metadata_api_version": metadata.metadata_api_version,
        "schema_refs": [_model_dump(schema_ref) for schema_ref in metadata.schema_refs],
        "object_count": len(metadata.object_definitions),
        "object_definitions": [
            _object_summary(object_definition)
            for object_definition in metadata.object_definitions
        ],
    }
    if detail_level == "detail":
        contract["model_definitions"] = [
            _model_definition(model_definition)
            for model_definition in metadata.model_definitions
        ]
        contract["object_definitions"] = [
            _object_detail(object_definition, registry=registry)
            for object_definition in metadata.object_definitions
        ]
    return contract


def _validator_binding_contract(
    registry: DomainPackValidationRegistry,
    agent_id: str,
    *,
    package_id: str | None,
    detail_level: str,
) -> dict[str, Any]:
    metadata = registry.domain_pack.metadata
    bindings = [binding.identity_details() for binding in registry.bindings]
    if agent_id:
        targeted = [
            details
            for details in bindings
            if _validator_agent_matches(
                details.get("validator_agent"),
                agent_id=agent_id,
                package_id=package_id,
            )
        ]
    else:
        targeted = []
    contract: dict[str, Any] = {
        "domain_pack_id": metadata.pack_id,
        "domain_pack_version": metadata.version,
        "bindings": targeted or bindings,
        "targeted_to_agent": bool(targeted),
        "binding_count": len(targeted or bindings),
    }
    if detail_level == "detail":
        contract["validators"] = [entry.identity_details() for entry in registry.validator_metadata]
        contract["field_policies"] = [
            policy.identity_details() for policy in registry.field_policies
        ]
        contract["validation_attachments"] = [
            option.to_dict() for option in registry.validation_attachment_options()
        ]
    return contract


def _ontology_constraints_contract(
    registry: DomainPackValidationRegistry,
    *,
    detail_level: str,
) -> dict[str, Any]:
    metadata = registry.domain_pack.metadata
    constrained_fields = []
    for object_definition in metadata.object_definitions:
        for field_definition in object_definition.fields:
            constraints = _field_constraints(
                registry,
                object_definition.object_type,
                field_definition,
            )
            if not constraints:
                continue
            item = {
                "object_type": object_definition.object_type,
                "field_path": field_definition.field_path,
                "constraints": constraints,
            }
            if detail_level == "detail":
                item["field"] = _field_definition(field_definition, registry, object_definition.object_type)
            constrained_fields.append(item)

    return {
        "domain_pack_id": metadata.pack_id,
        "domain_pack_version": metadata.version,
        "schema_refs": [_model_dump(schema_ref) for schema_ref in metadata.schema_refs],
        "constrained_field_count": len(constrained_fields),
        "constrained_fields": constrained_fields,
    }


def _field_contract(
    registry: DomainPackValidationRegistry,
    requested_path: str,
    *,
    detail_level: str,
) -> dict[str, Any] | None:
    metadata = registry.domain_pack.metadata
    matches: list[dict[str, Any]] = []
    for object_definition in metadata.object_definitions:
        object_prefix = f"{object_definition.object_type}."
        for field_definition in object_definition.fields:
            if requested_path not in {
                field_definition.field_path,
                f"{object_prefix}{field_definition.field_path}",
            }:
                continue
            field_payload = _field_definition(
                field_definition,
                registry,
                object_definition.object_type,
            )
            if detail_level == "summary":
                field_payload = {
                    key: field_payload[key]
                    for key in (
                        "field_path",
                        "display_name",
                        "description",
                        "field_type",
                        "required",
                        "definition_state",
                        "validation_policy",
                    )
                    if key in field_payload
                }
            matches.append(
                {
                    "domain_pack_id": metadata.pack_id,
                    "domain_pack_version": metadata.version,
                    "object_type": object_definition.object_type,
                    "object_display_name": object_definition.display_name,
                    "field": field_payload,
                    "validator_bindings": _validator_bindings_for_field(
                        registry,
                        object_definition.object_type,
                        field_definition.field_path,
                    ),
                }
            )
    if not matches:
        return None
    return {"domain_pack_id": metadata.pack_id, "fields": matches}


def _validator_domain_pack_ids(
    agent_id: str,
    *,
    package_id: str | None,
    registries: Mapping[str, DomainPackValidationRegistry],
) -> list[str]:
    pack_ids: list[str] = []
    for pack_id, registry in sorted(registries.items()):
        for binding in registry.bindings:
            details = binding.identity_details()
            if _validator_agent_matches(
                details.get("validator_agent"),
                agent_id=agent_id,
                package_id=package_id,
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
    agent_id: str,
    package_id: str | None,
) -> bool:
    if not isinstance(value, Mapping):
        return False
    if value.get("agent_id") != agent_id:
        return False
    if package_id is None:
        return True
    return value.get("package_id") in {None, package_id}


def _validator_bindings_for_field(
    registry: DomainPackValidationRegistry,
    object_type: str,
    field_path: str,
) -> list[dict[str, Any]]:
    bindings = []
    for binding in registry.bindings:
        details = binding.identity_details()
        object_types = set(details.get("object_types") or [])
        field_paths = set(details.get("field_paths") or [])
        if object_types and object_type not in object_types:
            continue
        if field_paths and field_path not in field_paths:
            continue
        bindings.append(details)
    return bindings


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


def _object_detail(
    object_definition: Any,
    *,
    registry: DomainPackValidationRegistry,
) -> dict[str, Any]:
    payload = _object_summary(object_definition)
    payload.update(
        {
            "schema_ref": _model_dump(object_definition.schema_ref),
            "definition_notes": list(object_definition.definition_notes),
            "provider_refs": _provider_refs(object_definition.metadata),
            "fields": [
                _field_definition(field_definition, registry, object_definition.object_type)
                for field_definition in object_definition.fields
            ],
        }
    )
    return payload


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


def _json_schema_field(schema: Mapping[str, Any], field_path: str) -> dict[str, Any] | None:
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
            return _json_schema_field_summary(segment, child, required=segment in required)
        resolved = _resolve_json_schema_ref(schema, child)
        current = resolved if resolved is not None else child
        required = set(current.get("required") or [])
    return None


def _resolve_json_schema_ref(
    root_schema: Mapping[str, Any],
    value: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    ref = value.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
        return None
    defs = root_schema.get("$defs")
    if not isinstance(defs, Mapping):
        return None
    resolved = defs.get(ref.removeprefix("#/$defs/"))
    return resolved if isinstance(resolved, Mapping) else None


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
    "get_agent_contract",
    "get_domain_pack_field_info",
    "get_extraction_contract",
]
