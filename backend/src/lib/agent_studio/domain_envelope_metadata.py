"""Agent Studio projections for domain-pack envelope authoring metadata."""

from __future__ import annotations

from typing import Any, Mapping

from src.lib.flows.validation_attachments import (
    FlowValidationAttachmentError,
    domain_pack_validation_registries,
)
from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
from src.schemas.domain_envelope import SchemaRef
from src.schemas.domain_pack_metadata import (
    DomainPackFieldDefinition,
    DomainPackModelDefinition,
    DomainPackObjectDefinition,
)


SEMANTIC_SOURCE_NOTE = (
    "Domain envelope objects are the semantic source of truth; workspace and "
    "review rows are projections over persisted envelope objects, field paths, "
    "validation findings, history, and metadata."
)


def domain_envelope_metadata_catalog_by_agent(
    agent_registry: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Return domain-envelope authoring metadata keyed by agent ID."""

    registries = domain_pack_validation_registries()
    catalog: dict[str, dict[str, Any]] = {}

    for agent_id, entry in sorted(agent_registry.items()):
        domain_pack_id = _domain_pack_id_for_agent(entry)
        if domain_pack_id is None:
            continue

        registry = registries.get(domain_pack_id)
        if registry is None:
            raise FlowValidationAttachmentError(
                f"Agent declares unknown domain_pack_id '{domain_pack_id}'"
            )

        catalog[agent_id] = _domain_envelope_metadata(registry)

    return catalog


def _domain_pack_id_for_agent(entry: Mapping[str, Any]) -> str | None:
    curation = entry.get("curation")
    if not isinstance(curation, Mapping):
        return None

    raw_domain_pack_id = curation.get("domain_pack_id")
    if not isinstance(raw_domain_pack_id, str):
        return None

    domain_pack_id = raw_domain_pack_id.strip()
    return domain_pack_id or None


def _domain_envelope_metadata(
    registry: DomainPackValidationRegistry,
) -> dict[str, Any]:
    domain_pack = registry.domain_pack
    metadata = domain_pack.metadata
    validation_attachments = [
        option.to_dict() for option in registry.validation_attachment_options()
    ]

    source_of_truth_notes = [SEMANTIC_SOURCE_NOTE]
    source_of_truth_notes.extend(
        _source_of_truth_notes(metadata.object_definitions)
    )

    return {
        "domain_pack_id": metadata.pack_id,
        "domain_pack_version": metadata.version,
        "display_name": metadata.display_name,
        "description": metadata.description,
        "status": metadata.status.value,
        "metadata_api_version": metadata.metadata_api_version,
        "schema_refs": [_schema_ref_payload(schema_ref) for schema_ref in metadata.schema_refs],
        "provider_refs": _metadata_provider_refs(metadata.metadata),
        "semantic_source_note": SEMANTIC_SOURCE_NOTE,
        "source_of_truth_notes": _dedupe_strings(source_of_truth_notes),
        "validation_attachments": validation_attachments,
        "model_definitions": [
            _model_definition_payload(model_definition)
            for model_definition in metadata.model_definitions
        ],
        "object_definitions": [
            _object_definition_payload(
                object_definition,
                registry=registry,
                validation_attachments=validation_attachments,
            )
            for object_definition in metadata.object_definitions
        ],
        "validation_summary": _validation_summary(validation_attachments),
    }


def _model_definition_payload(
    model_definition: DomainPackModelDefinition,
) -> dict[str, Any]:
    return {
        "model_id": model_definition.model_id,
        "display_name": model_definition.display_name,
        "description": model_definition.description,
        "schema_ref": _schema_ref_payload(model_definition.schema_ref),
        "definition_state": model_definition.definition_state.value,
        "definition_notes": list(model_definition.definition_notes),
        "provider_refs": _metadata_provider_refs(model_definition.metadata),
    }


def _object_definition_payload(
    object_definition: DomainPackObjectDefinition,
    *,
    registry: DomainPackValidationRegistry,
    validation_attachments: list[dict[str, Any]],
) -> dict[str, Any]:
    object_validation_attachments = [
        attachment
        for attachment in validation_attachments
        if (
            attachment.get("scope") == "object"
            and attachment.get("object_type") == object_definition.object_type
        )
    ]

    return {
        "object_type": object_definition.object_type,
        "display_name": object_definition.display_name,
        "description": object_definition.description,
        "object_role": _optional_string(object_definition.metadata.get("object_role")),
        "model_ref": object_definition.model_ref,
        "schema_ref": _schema_ref_payload(object_definition.schema_ref),
        "definition_state": object_definition.definition_state.value,
        "definition_notes": list(object_definition.definition_notes),
        "provider_refs": _metadata_provider_refs(object_definition.metadata),
        "validation_attachments": object_validation_attachments,
        "fields": [
            _field_definition_payload(
                object_definition.object_type,
                field_definition,
                registry=registry,
                validation_attachments=validation_attachments,
            )
            for field_definition in object_definition.fields
        ],
    }


def _field_definition_payload(
    object_type: str,
    field_definition: DomainPackFieldDefinition,
    *,
    registry: DomainPackValidationRegistry,
    validation_attachments: list[dict[str, Any]],
) -> dict[str, Any]:
    field_validation_attachments = [
        attachment
        for attachment in validation_attachments
        if (
            attachment.get("scope") == "field"
            and attachment.get("object_type") == object_type
            and attachment.get("field_path") == field_definition.field_path
        )
    ]
    field_policy = registry.policy_for(object_type, field_definition.field_path)

    return {
        "field_path": field_definition.field_path,
        "display_name": field_definition.display_name,
        "description": field_definition.description,
        "field_type": field_definition.field_type.value,
        "required": field_definition.required,
        "enum_ref": field_definition.enum_ref,
        "model_ref": field_definition.model_ref,
        "object_type_ref": field_definition.object_type_ref,
        "definition_state": field_definition.definition_state.value,
        "definition_notes": list(field_definition.definition_notes),
        "provider_refs": _metadata_provider_refs(field_definition.metadata),
        "source_of_truth": _field_source_of_truth(field_definition.metadata),
        "validation_policy": (
            field_policy.identity_details() if field_policy is not None else None
        ),
        "validation_attachments": field_validation_attachments,
    }


def _schema_ref_payload(schema_ref: SchemaRef | None) -> dict[str, Any] | None:
    if schema_ref is None:
        return None
    return schema_ref.model_dump(mode="json", exclude_none=True)


def _metadata_provider_refs(metadata: Mapping[str, Any]) -> dict[str, Any]:
    provider_refs = metadata.get("provider_refs")
    if not isinstance(provider_refs, Mapping):
        return {}
    return dict(provider_refs)


def _field_source_of_truth(metadata: Mapping[str, Any]) -> str | None:
    repair_metadata = metadata.get("repair")
    if not isinstance(repair_metadata, Mapping):
        return None
    return _optional_string(repair_metadata.get("source_of_truth"))


def _source_of_truth_notes(
    object_definitions: list[DomainPackObjectDefinition],
) -> list[str]:
    notes: list[str] = []
    for object_definition in object_definitions:
        for note in object_definition.definition_notes:
            notes.append(f"{object_definition.display_name}: {note}")
        for field_definition in object_definition.fields:
            source_of_truth = _field_source_of_truth(field_definition.metadata)
            if source_of_truth is None:
                continue
            field_label = field_definition.display_name or field_definition.field_path
            notes.append(
                f"{object_definition.display_name} / {field_label}: "
                f"source of truth is {source_of_truth}."
            )
    return notes


def _validation_summary(
    validation_attachments: list[dict[str, Any]],
) -> dict[str, Any]:
    by_state = {"active": 0, "planned": 0, "blocked": 0}
    by_scope = {"pack": 0, "object": 0, "field": 0}
    default_enabled = 0
    required = 0
    export_blocking = 0
    opt_out_allowed = 0

    for attachment in validation_attachments:
        state = attachment.get("state")
        if state in by_state:
            by_state[state] += 1

        scope = attachment.get("scope")
        if scope in by_scope:
            by_scope[scope] += 1

        if attachment.get("default_enabled"):
            default_enabled += 1
        if attachment.get("required"):
            required += 1
        if attachment.get("export_blocking"):
            export_blocking += 1
        if attachment.get("allow_opt_out"):
            opt_out_allowed += 1

    return {
        "total": len(validation_attachments),
        "by_state": by_state,
        "by_scope": by_scope,
        "default_enabled": default_enabled,
        "required": required,
        "export_blocking": export_blocking,
        "opt_out_allowed": opt_out_allowed,
    }


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


__all__ = [
    "domain_envelope_metadata_catalog_by_agent",
]
