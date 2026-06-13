"""Domain-pack policy for supervisor extraction manifests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.schemas.domain_envelope import validate_field_path_syntax
from src.schemas.domain_pack_metadata import (
    DomainPackFieldDefinition,
    DomainPackFieldType,
    DomainPackMetadata,
    DomainPackObjectDefinition,
)


class SupervisorManifestPolicyError(ValueError):
    """Raised when a domain pack lacks a safe supervisor manifest policy."""


_SUPERVISOR_MANIFEST_ALLOWED_KEYS = frozenset(
    {
        "primary_label_field",
        "primary_label_fields",
        "secondary_label_field",
        "summary_fields",
    }
)
_SCALAR_FIELD_TYPES = frozenset(
    {
        DomainPackFieldType.STRING,
        DomainPackFieldType.INTEGER,
        DomainPackFieldType.NUMBER,
        DomainPackFieldType.BOOLEAN,
        DomainPackFieldType.ENUM,
    }
)
_EVIDENCE_FIELD_SEGMENT_DENYLIST = frozenset(
    {
        "evidence",
        "evidence_record",
        "evidence_records",
        "evidence_record_id",
        "evidence_record_ids",
        "evidence_quote",
        "quote",
        "verified_quote",
        "source_quote",
        "chunk",
        "chunk_id",
        "chunk_ids",
    }
)
_DEFAULT_MANIFEST_OBJECT_ROLES = frozenset(
    {
        "curatable_unit",
    }
)


@dataclass(frozen=True)
class SupervisorManifestField:
    """One payload field allowed in a default supervisor manifest."""

    path: str
    label: str


@dataclass(frozen=True)
class SupervisorManifestPolicy:
    """Normalized YAML-owned display policy for one object type."""

    object_type: str
    source: str
    primary_label_fields: tuple[SupervisorManifestField, ...]
    secondary_label_field: SupervisorManifestField | None
    summary_fields: tuple[SupervisorManifestField, ...]

    @property
    def field_paths(self) -> tuple[str, ...]:
        paths = [field.path for field in self.primary_label_fields]
        if self.secondary_label_field is not None:
            paths.append(self.secondary_label_field.path)
        paths.extend(field.path for field in self.summary_fields)
        return tuple(paths)


def is_default_supervisor_manifest_object(
    object_definition: DomainPackObjectDefinition,
) -> bool:
    """Return whether a domain object type must have manifest display policy."""

    metadata = object_definition.metadata
    role = _optional_string(metadata.get("object_role"))
    if role in _DEFAULT_MANIFEST_OBJECT_ROLES:
        return True
    if metadata.get("stageable") is True:
        return True
    generic_extraction = metadata.get("generic_extraction")
    return isinstance(generic_extraction, Mapping) and (
        generic_extraction.get("stageable") is True
    )


def supervisor_manifest_policy_for_object(
    metadata: DomainPackMetadata,
    object_type: str,
) -> SupervisorManifestPolicy:
    """Return the YAML-owned supervisor manifest policy for ``object_type``."""

    object_definition = _object_definition(metadata, object_type)
    if object_definition is None:
        raise SupervisorManifestPolicyError(
            f"{metadata.pack_id}.{object_type} has no domain-pack object definition"
        )
    return _policy_for_definition(metadata, object_definition)


def validate_supervisor_manifest_policies(metadata: DomainPackMetadata) -> None:
    """Validate manifest display policy for every retained/stageable object type."""

    errors: list[str] = []
    for object_definition in metadata.object_definitions:
        if not is_default_supervisor_manifest_object(object_definition):
            continue
        try:
            _policy_for_definition(metadata, object_definition)
        except SupervisorManifestPolicyError as exc:
            errors.append(str(exc))

    if errors:
        raise SupervisorManifestPolicyError("; ".join(errors))


def _policy_for_definition(
    metadata: DomainPackMetadata,
    object_definition: DomainPackObjectDefinition,
) -> SupervisorManifestPolicy:
    config, source = _display_config(object_definition)
    if config is None:
        raise SupervisorManifestPolicyError(
            f"{metadata.pack_id}.{object_definition.object_type} must declare "
            "metadata.supervisor_manifest or metadata.workspace_display for the "
            "default supervisor manifest"
        )

    if source == "supervisor_manifest":
        unknown_keys = sorted(set(config) - _SUPERVISOR_MANIFEST_ALLOWED_KEYS)
        if unknown_keys:
            raise SupervisorManifestPolicyError(
                f"{metadata.pack_id}.{object_definition.object_type}.supervisor_manifest "
                f"contains unknown key(s): {', '.join(unknown_keys)}"
            )

    field_definitions = {
        field.field_path: field for field in object_definition.fields
    }
    primary_label_fields = _manifest_fields(
        metadata,
        object_definition,
        field_definitions,
        _field_list(config, "primary_label_fields")
        + _field_list(config, "primary_label_field"),
        f"{source}.primary_label",
    )
    secondary_label_fields = _manifest_fields(
        metadata,
        object_definition,
        field_definitions,
        _field_list(config, "secondary_label_field"),
        f"{source}.secondary_label_field",
    )
    summary_fields = _manifest_fields(
        metadata,
        object_definition,
        field_definitions,
        _field_list(config, "summary_fields"),
        f"{source}.summary_fields",
    )
    secondary_label_field = (
        secondary_label_fields[0] if secondary_label_fields else None
    )
    if len(secondary_label_fields) > 1:
        raise SupervisorManifestPolicyError(
            f"{metadata.pack_id}.{object_definition.object_type}.{source} "
            "must declare at most one secondary_label_field"
        )

    all_paths = [
        field.path
        for field in [
            *primary_label_fields,
            *secondary_label_fields,
            *summary_fields,
        ]
    ]
    duplicates = _duplicates(all_paths)
    if duplicates:
        raise SupervisorManifestPolicyError(
            f"{metadata.pack_id}.{object_definition.object_type}.{source} "
            f"contains duplicate manifest field path(s): {', '.join(duplicates)}"
        )
    if not primary_label_fields and not summary_fields:
        raise SupervisorManifestPolicyError(
            f"{metadata.pack_id}.{object_definition.object_type}.{source} must "
            "declare at least one primary label field or summary field"
        )

    return SupervisorManifestPolicy(
        object_type=object_definition.object_type,
        source=source,
        primary_label_fields=tuple(primary_label_fields),
        secondary_label_field=secondary_label_field,
        summary_fields=tuple(summary_fields),
    )


def _display_config(
    object_definition: DomainPackObjectDefinition,
) -> tuple[Mapping[str, Any] | None, str]:
    supervisor_config = object_definition.metadata.get("supervisor_manifest")
    if isinstance(supervisor_config, Mapping):
        return supervisor_config, "supervisor_manifest"
    workspace_config = object_definition.metadata.get("workspace_display")
    if isinstance(workspace_config, Mapping):
        return workspace_config, "workspace_display"
    return None, ""


def _manifest_fields(
    metadata: DomainPackMetadata,
    object_definition: DomainPackObjectDefinition,
    field_definitions: Mapping[str, DomainPackFieldDefinition],
    paths: list[str],
    location: str,
) -> list[SupervisorManifestField]:
    fields: list[SupervisorManifestField] = []
    for path in paths:
        field_path = _validate_manifest_field_path(
            metadata,
            object_definition,
            field_definitions,
            path,
            location,
        )
        field_definition = field_definitions[field_path]
        fields.append(
            SupervisorManifestField(
                path=field_path,
                label=_field_label(field_definition),
            )
        )
    return fields


def _validate_manifest_field_path(
    metadata: DomainPackMetadata,
    object_definition: DomainPackObjectDefinition,
    field_definitions: Mapping[str, DomainPackFieldDefinition],
    raw_path: str,
    location: str,
) -> str:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise SupervisorManifestPolicyError(
            f"{metadata.pack_id}.{object_definition.object_type}.{location} "
            "must contain non-empty string field paths"
        )
    field_path = validate_field_path_syntax(raw_path.strip())
    if _is_evidence_text_or_container_path(field_path):
        raise SupervisorManifestPolicyError(
            f"{metadata.pack_id}.{object_definition.object_type}.{location} "
            f"may not expose evidence/quote/chunk path {field_path!r} in the "
            "default supervisor manifest"
        )
    field_definition = field_definitions.get(field_path)
    if field_definition is None:
        raise SupervisorManifestPolicyError(
            f"{metadata.pack_id}.{object_definition.object_type}.{location} "
            f"references undeclared field path {field_path!r}"
        )
    if field_definition.field_type not in _SCALAR_FIELD_TYPES:
        raise SupervisorManifestPolicyError(
            f"{metadata.pack_id}.{object_definition.object_type}.{location} "
            f"references non-scalar field path {field_path!r} "
            f"({field_definition.field_type.value})"
        )
    return field_path


def _field_list(config: Mapping[str, Any], key: str) -> list[str]:
    value = config.get(key)
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        if not all(isinstance(item, str) for item in value):
            return [item for item in value]  # type: ignore[list-item]
        return list(value)
    return [value]  # type: ignore[list-item]


def _is_evidence_text_or_container_path(field_path: str) -> bool:
    for raw_segment in field_path.replace("[", ".[").split("."):
        segment = raw_segment.split("[", 1)[0].strip().lower()
        if segment in _EVIDENCE_FIELD_SEGMENT_DENYLIST:
            return True
        if segment.endswith("_quote") or segment.endswith("_chunk"):
            return True
    return False


def _field_label(field_definition: DomainPackFieldDefinition) -> str:
    display_name = _optional_string(field_definition.display_name)
    if display_name:
        return display_name
    return field_definition.field_path.replace("_", " ")


def _object_definition(
    metadata: DomainPackMetadata,
    object_type: str,
) -> DomainPackObjectDefinition | None:
    for object_definition in metadata.object_definitions:
        if object_definition.object_type == object_type:
            return object_definition
    return None


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


__all__ = [
    "SupervisorManifestField",
    "SupervisorManifestPolicy",
    "SupervisorManifestPolicyError",
    "is_default_supervisor_manifest_object",
    "supervisor_manifest_policy_for_object",
    "validate_supervisor_manifest_policies",
]
