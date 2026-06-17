"""Provider-agnostic domain envelope contracts.

The envelope is the semantic source of truth for extracted curatable objects,
their object/field references, validation findings, and audit history. Domain
packs may define richer model semantics around these contracts, but the core
schema intentionally avoids LinkML, Alliance database, or provider-specific
requirements.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)

_FIELD_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*")
_LIST_INDEX_PATTERN = re.compile(r"^\[(\d+)\]")


class DomainEnvelopeBaseModel(BaseModel):
    """Strict base model for envelope contracts."""

    model_config = ConfigDict(extra="forbid")


class DefinitionState(str, Enum):
    """Lifecycle state for a schema or object definition."""

    STABLE = "stable"
    DRAFT = "draft"
    IN_DEVELOPMENT = "in_development"
    DEPRECATED = "deprecated"


class DomainEnvelopeStatus(str, Enum):
    """Provider-neutral lifecycle states for a full envelope."""

    EXTRACTION_PENDING = "extraction_pending"
    EXTRACTED = "extracted"
    VALIDATING = "validating"
    VALIDATED = "validated"
    READY_FOR_EXPORT = "ready_for_export"
    EXPORTED = "exported"
    SUBMITTED = "submitted"
    FAILED = "failed"


class CuratableObjectStatus(str, Enum):
    """Provider-neutral lifecycle states for one curatable object."""

    PENDING = "pending"
    EXTRACTED = "extracted"
    NEEDS_REVIEW = "needs_review"
    VALIDATING = "validating"
    VALIDATED = "validated"
    READY_FOR_EXPORT = "ready_for_export"
    REJECTED = "rejected"


class ValidationFindingSeverity(str, Enum):
    """Severity for envelope validation findings."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    BLOCKER = "blocker"


class ValidationFindingStatus(str, Enum):
    """Resolution state for a validation finding."""

    OPEN = "open"
    RESOLVED = "resolved"
    WAIVED = "waived"


class HistoryEventKind(str, Enum):
    """Generic event types emitted while an envelope changes."""

    CREATED = "created"
    OBJECT_EXTRACTED = "object_extracted"
    OBJECT_UPDATED = "object_updated"
    FIELD_UPDATED = "field_updated"
    CURATOR_FIELD_PATCH_ACCEPTED = "curator_field_patch_accepted"
    CURATOR_FIELD_PATCH_REJECTED = "curator_field_patch_rejected"
    VALIDATION_FINDING_ADDED = "validation_finding_added"
    VALIDATION_RERUN_REQUESTED = "validation_rerun_requested"
    STATUS_CHANGED = "status_changed"
    EXPORTED = "exported"
    SUBMITTED = "submitted"


class HistoryActorType(str, Enum):
    """Actor categories that may produce envelope history events."""

    SYSTEM = "system"
    AGENT = "agent"
    HUMAN = "human"
    TOOL = "tool"


def _validate_non_empty_identifier(
    value: Optional[str], field_name: str
) -> Optional[str]:
    if value is None:
        return None
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    if value != value.strip():
        raise ValueError(
            f"{field_name} must not include leading or trailing whitespace"
        )
    return value


def parse_field_path(field_path: str) -> tuple[str | int, ...]:
    """Parse a provider-neutral JSON field path into dict keys and list indexes.

    Supported paths are relative object-payload paths such as
    ``gene.symbol`` or ``evidence[0].snippet``. Object identity is carried by
    ``ObjectRef`` and must not be embedded in the field path itself.
    """

    if not isinstance(field_path, str) or not field_path:
        raise ValueError("field_path must not be empty")
    if field_path != field_path.strip():
        raise ValueError("field_path must not include leading or trailing whitespace")
    if field_path.startswith((".", "$", "[")) or field_path.endswith("."):
        raise ValueError("field_path must be a relative object payload path")
    if ".." in field_path:
        raise ValueError("field_path must not contain empty path segments")

    parsed_parts: list[str | int] = []
    for segment in field_path.split("."):
        if not segment:
            raise ValueError("field_path must not contain empty path segments")

        remaining = segment
        key_match = _FIELD_KEY_PATTERN.match(remaining)
        if key_match is None:
            raise ValueError(
                "field_path segments must start with a letter or underscore and only use "
                "letters, digits, underscores, hyphens, and numeric list indexes"
            )

        parsed_parts.append(key_match.group(0))
        remaining = remaining[key_match.end() :]

        while remaining:
            index_match = _LIST_INDEX_PATTERN.match(remaining)
            if index_match is None:
                raise ValueError(
                    "field_path list indexes must use bracketed non-negative integers"
                )
            parsed_parts.append(int(index_match.group(1)))
            remaining = remaining[index_match.end() :]

    return tuple(parsed_parts)


def validate_field_path_syntax(field_path: str) -> str:
    """Validate a field-path string and return it unchanged."""

    parse_field_path(field_path)
    return field_path


def field_path_exists(payload: Mapping[str, Any], field_path: str) -> bool:
    """Return whether ``field_path`` resolves inside a JSON-like object payload."""

    current: Any = payload
    for part in parse_field_path(field_path):
        if isinstance(part, str):
            if not isinstance(current, Mapping) or part not in current:
                return False
            current = current[part]
            continue

        if (
            not isinstance(current, Sequence)
            or isinstance(current, (str, bytes, bytearray))
            or part >= len(current)
        ):
            return False
        current = current[part]

    return True


class SchemaRef(DomainEnvelopeBaseModel):
    """Reference to a schema or contract without assuming a provider."""

    schema_id: str = Field(description="Stable schema identifier within its provider")
    provider: Optional[str] = Field(
        default=None,
        description="Optional provider key, such as a schema service or local domain pack",
    )
    name: Optional[str] = Field(default=None, description="Human-readable schema name")
    version: Optional[str] = Field(
        default=None, description="Provider-owned schema version"
    )
    uri: Optional[str] = Field(
        default=None, description="Optional resolvable schema URI"
    )
    checksum: Optional[str] = Field(
        default=None,
        description="Optional provider-owned checksum for immutable schema content",
    )
    definition_state: DefinitionState = Field(
        default=DefinitionState.STABLE,
        description="Lifecycle state for this referenced schema contract",
    )
    definition_notes: list[str] = Field(
        default_factory=list,
        description="Notes for draft or under-development schema contracts",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-owned metadata that does not affect core validation",
    )

    @field_validator("schema_id", "provider", "name", "version", "uri", "checksum")
    @classmethod
    def _validate_identifiers(cls, value: Optional[str], info) -> Optional[str]:
        return _validate_non_empty_identifier(value, info.field_name)


class ObjectRef(DomainEnvelopeBaseModel):
    """Reference to an object by durable object ID or extraction-time pending ref."""

    object_id: Optional[str] = Field(
        default=None,
        description="Durable object identifier after an object has been materialized",
    )
    pending_ref_id: Optional[str] = Field(
        default=None,
        description="Extraction-time identifier used before durable IDs exist",
    )
    object_type: Optional[str] = Field(
        default=None,
        description="Optional object type hint used by callers and diagnostics",
    )

    @field_validator("object_id", "pending_ref_id", "object_type")
    @classmethod
    def _validate_ref_fields(cls, value: Optional[str], info) -> Optional[str]:
        return _validate_non_empty_identifier(value, info.field_name)

    @model_validator(mode="after")
    def _validate_single_ref_kind(self) -> "ObjectRef":
        if (self.object_id is None) == (self.pending_ref_id is None):
            raise ValueError(
                "ObjectRef must provide exactly one of object_id or pending_ref_id"
            )
        return self

    def ref_key(self) -> tuple[str, str]:
        """Return a stable key for envelope-level reference validation."""

        if self.object_id is not None:
            return ("object_id", self.object_id)
        if self.pending_ref_id is not None:
            return ("pending_ref_id", self.pending_ref_id)
        raise ValueError("ObjectRef is missing object_id or pending_ref_id")


class FieldRef(DomainEnvelopeBaseModel):
    """Reference to one field path inside a curatable object payload."""

    object_ref: ObjectRef = Field(description="Object containing the referenced field")
    field_path: str = Field(description="Relative JSON object payload path")

    @field_validator("field_path")
    @classmethod
    def _validate_field_path(cls, value: str) -> str:
        return validate_field_path_syntax(value)


class EnvelopeMetadataRef(DomainEnvelopeBaseModel):
    """Reference to supporting metadata carried outside semantic objects."""

    metadata_path: str = Field(
        description=(
            "Relative JSON path inside envelope metadata, such as "
            "raw_mentions[0] or evidence_records[2]"
        )
    )
    role: Optional[str] = Field(
        default=None,
        description="Provider-owned role for the metadata reference",
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional curator-facing description of why this metadata is linked",
    )

    @field_validator("metadata_path")
    @classmethod
    def _validate_metadata_path(cls, value: str) -> str:
        return validate_field_path_syntax(value)

    @field_validator("role", "description")
    @classmethod
    def _validate_optional_strings(cls, value: Optional[str], info) -> Optional[str]:
        return _validate_non_empty_identifier(value, info.field_name)


class CuratableObjectEnvelope(DomainEnvelopeBaseModel):
    """One curatable object carried by a domain envelope."""

    object_type: str = Field(description="Domain-pack object type key")
    object_role: Optional[str] = Field(
        default=None,
        description=(
            "Provider-owned role for this object within the envelope, such as "
            "curatable_unit, supporting_reference, or evidence_quote"
        ),
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-neutral JSON payload extracted for this object",
    )
    object_id: Optional[str] = Field(
        default=None,
        description="Durable object identifier once one exists",
    )
    pending_ref_id: Optional[str] = Field(
        default=None,
        description="Extraction-time identifier for pending object refs",
    )
    schema_ref: Optional[SchemaRef] = Field(
        default=None,
        description="Optional schema ref that defines this object's payload contract",
    )
    model_ref: Optional[str] = Field(
        default=None,
        description="Optional domain-pack model_id that defines this object's payload contract",
    )
    status: CuratableObjectStatus = Field(default=CuratableObjectStatus.EXTRACTED)
    definition_state: DefinitionState = Field(
        default=DefinitionState.STABLE,
        description="Lifecycle state for this object's domain-pack definition",
    )
    definition_notes: list[str] = Field(
        default_factory=list,
        description="Notes explaining draft or under-development object contracts",
    )
    object_refs: list[ObjectRef] = Field(
        default_factory=list,
        description="Object-level references to other objects in the same envelope",
    )
    field_refs: list[FieldRef] = Field(
        default_factory=list,
        description="Field-level references to fields in objects in the same envelope",
    )
    evidence_record_ids: list[StrictStr] = Field(
        default_factory=list,
        description="Stable evidence record IDs supporting this curatable object",
    )
    metadata_refs: list[EnvelopeMetadataRef] = Field(
        default_factory=list,
        description="References to raw mentions, exclusions, ambiguities, notes, or evidence in envelope metadata",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Domain-pack-owned metadata that does not affect core validation",
    )

    @field_validator(
        "object_type", "object_role", "object_id", "pending_ref_id", "model_ref"
    )
    @classmethod
    def _validate_object_identifiers(cls, value: Optional[str], info) -> Optional[str]:
        return _validate_non_empty_identifier(value, info.field_name)

    @model_validator(mode="after")
    def _validate_object_identity(self) -> "CuratableObjectEnvelope":
        if self.object_id is None and self.pending_ref_id is None:
            raise ValueError(
                "CuratableObjectEnvelope must provide object_id or pending_ref_id"
            )
        return self

    def ref_keys(self) -> tuple[tuple[str, str], ...]:
        """Return every envelope reference key that resolves to this object."""

        keys: list[tuple[str, str]] = []
        if self.object_id is not None:
            keys.append(("object_id", self.object_id))
        if self.pending_ref_id is not None:
            keys.append(("pending_ref_id", self.pending_ref_id))
        return tuple(keys)

    def to_object_ref(self) -> ObjectRef:
        """Return this object's canonical envelope reference."""

        if self.object_id is not None:
            return ObjectRef(object_id=self.object_id, object_type=self.object_type)
        if self.pending_ref_id is not None:
            return ObjectRef(
                pending_ref_id=self.pending_ref_id,
                object_type=self.object_type,
            )
        raise ValueError(
            "CuratableObjectEnvelope must provide object_id or pending_ref_id"
        )


class ValidationFinding(DomainEnvelopeBaseModel):
    """Validation issue, warning, or informational note for an envelope."""

    severity: ValidationFindingSeverity = Field(description="Finding severity")
    message: str = Field(min_length=1, description="Curator-facing finding message")
    finding_id: Optional[str] = Field(
        default=None,
        description="Stable finding identifier when one has been assigned",
    )
    status: ValidationFindingStatus = Field(default=ValidationFindingStatus.OPEN)
    code: Optional[str] = Field(
        default=None,
        description="Provider- or validator-owned machine-readable finding code",
    )
    object_ref: Optional[ObjectRef] = Field(
        default=None,
        description="Object targeted by this finding when not field-specific",
    )
    field_ref: Optional[FieldRef] = Field(
        default=None,
        description="Field targeted by this finding when field-specific",
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Validator-owned structured details for diagnostics",
    )

    @field_validator("finding_id", "code")
    @classmethod
    def _validate_optional_identifiers(
        cls, value: Optional[str], info
    ) -> Optional[str]:
        return _validate_non_empty_identifier(value, info.field_name)

    @model_validator(mode="after")
    def _validate_target_consistency(self) -> "ValidationFinding":
        if (
            self.object_ref is not None
            and self.field_ref is not None
            and self.object_ref.ref_key() != self.field_ref.object_ref.ref_key()
        ):
            raise ValueError(
                "object_ref must match field_ref.object_ref when both are set"
            )
        return self


class HistoryEvent(DomainEnvelopeBaseModel):
    """Audit/history event emitted while an envelope changes."""

    event_type: HistoryEventKind = Field(description="Provider-neutral event kind")
    event_id: Optional[str] = Field(
        default=None,
        description="Stable event identifier when one has been assigned",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC event timestamp",
    )
    actor_type: HistoryActorType = Field(default=HistoryActorType.SYSTEM)
    actor_id: Optional[str] = Field(
        default=None,
        description="Provider-owned user, agent, tool, or service identifier",
    )
    message: Optional[str] = Field(
        default=None,
        description="Optional human-readable event summary",
    )
    object_ref: Optional[ObjectRef] = Field(
        default=None,
        description="Object targeted by this event when applicable",
    )
    field_ref: Optional[FieldRef] = Field(
        default=None,
        description="Field targeted by this event when applicable",
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-owned structured event details",
    )

    @field_validator("event_id", "actor_id", "message")
    @classmethod
    def _validate_optional_strings(cls, value: Optional[str], info) -> Optional[str]:
        return _validate_non_empty_identifier(value, info.field_name)

    @model_validator(mode="after")
    def _validate_target_consistency(self) -> "HistoryEvent":
        if (
            self.object_ref is not None
            and self.field_ref is not None
            and self.object_ref.ref_key() != self.field_ref.object_ref.ref_key()
        ):
            raise ValueError(
                "object_ref must match field_ref.object_ref when both are set"
            )
        return self


class DomainEnvelope(DomainEnvelopeBaseModel):
    """Top-level envelope for one provider-agnostic curation extraction."""

    envelope_id: str = Field(description="Stable envelope identifier")
    domain_pack_id: str = Field(description="Domain pack that owns object semantics")
    domain_pack_version: Optional[str] = Field(
        default=None,
        description="Domain pack version used to produce or interpret this envelope",
    )
    status: DomainEnvelopeStatus = Field(default=DomainEnvelopeStatus.EXTRACTED)
    schema_ref: Optional[SchemaRef] = Field(
        default=None,
        description="Optional top-level schema ref for the envelope contract",
    )
    # Extractors produce DomainEnvelopeExtractionResult.curatable_objects[] first.
    # This canonical downstream list is post-conversion and ready for review/export.
    extracted_objects: list[CuratableObjectEnvelope] = Field(
        default_factory=list,
        description="Post-conversion extracted objects carried by this envelope",
    )
    validation_findings: list[ValidationFinding] = Field(
        default_factory=list,
        description="Validation findings attached to the envelope",
    )
    history: list[HistoryEvent] = Field(
        default_factory=list,
        description="Provider-neutral envelope history events",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Domain-pack-owned metadata that does not affect core validation",
    )

    @field_validator("envelope_id", "domain_pack_id", "domain_pack_version")
    @classmethod
    def _validate_envelope_identifiers(
        cls, value: Optional[str], info
    ) -> Optional[str]:
        return _validate_non_empty_identifier(value, info.field_name)

    @model_validator(mode="after")
    def _validate_envelope_references(self) -> "DomainEnvelope":
        objects_by_key: dict[tuple[str, str], CuratableObjectEnvelope] = {}
        errors: list[str] = []

        for index, obj in enumerate(self.extracted_objects):
            for ref_key in obj.ref_keys():
                if ref_key in objects_by_key:
                    kind, value = ref_key
                    errors.append(
                        f"extracted_objects[{index}] duplicates {kind} '{value}'"
                    )
                    continue
                objects_by_key[ref_key] = obj

        for index, obj in enumerate(self.extracted_objects):
            for ref_index, object_ref in enumerate(obj.object_refs):
                self._validate_object_ref(
                    object_ref,
                    objects_by_key,
                    f"extracted_objects[{index}].object_refs[{ref_index}]",
                    errors,
                )
            for ref_index, field_ref in enumerate(obj.field_refs):
                self._validate_field_ref(
                    field_ref,
                    objects_by_key,
                    f"extracted_objects[{index}].field_refs[{ref_index}]",
                    errors,
                )

        for index, finding in enumerate(self.validation_findings):
            if finding.object_ref is not None:
                self._validate_object_ref(
                    finding.object_ref,
                    objects_by_key,
                    f"validation_findings[{index}].object_ref",
                    errors,
                )
            if finding.field_ref is not None:
                self._validate_object_ref(
                    finding.field_ref.object_ref,
                    objects_by_key,
                    f"validation_findings[{index}].field_ref.object_ref",
                    errors,
                )

        for index, event in enumerate(self.history):
            if event.object_ref is not None:
                self._validate_object_ref(
                    event.object_ref,
                    objects_by_key,
                    f"history[{index}].object_ref",
                    errors,
                )
            if event.field_ref is not None:
                self._validate_field_ref(
                    event.field_ref,
                    objects_by_key,
                    f"history[{index}].field_ref",
                    errors,
                )

        if errors:
            raise ValueError("; ".join(errors))
        return self

    @staticmethod
    def _validate_object_ref(
        object_ref: ObjectRef,
        objects_by_key: Mapping[tuple[str, str], CuratableObjectEnvelope],
        location: str,
        errors: list[str],
    ) -> Optional[CuratableObjectEnvelope]:
        ref_key = object_ref.ref_key()
        referenced_object = objects_by_key.get(ref_key)
        if referenced_object is None:
            kind, value = ref_key
            errors.append(f"{location} references unknown {kind} '{value}'")
            return None

        if (
            object_ref.object_type is not None
            and object_ref.object_type != referenced_object.object_type
        ):
            errors.append(
                f"{location} object_type '{object_ref.object_type}' does not match "
                f"referenced object_type '{referenced_object.object_type}'"
            )
        return referenced_object

    @classmethod
    def _validate_field_ref(
        cls,
        field_ref: FieldRef,
        objects_by_key: Mapping[tuple[str, str], CuratableObjectEnvelope],
        location: str,
        errors: list[str],
    ) -> None:
        referenced_object = cls._validate_object_ref(
            field_ref.object_ref,
            objects_by_key,
            f"{location}.object_ref",
            errors,
        )
        if referenced_object is None:
            return
        if not field_path_exists(referenced_object.payload, field_ref.field_path):
            errors.append(
                f"{location}.field_path '{field_ref.field_path}' does not exist on "
                f"referenced object"
            )


__all__ = [
    "CuratableObjectEnvelope",
    "CuratableObjectStatus",
    "DefinitionState",
    "DomainEnvelope",
    "DomainEnvelopeStatus",
    "EnvelopeMetadataRef",
    "FieldRef",
    "HistoryActorType",
    "HistoryEvent",
    "HistoryEventKind",
    "ObjectRef",
    "SchemaRef",
    "ValidationFinding",
    "ValidationFindingSeverity",
    "ValidationFindingStatus",
    "field_path_exists",
    "parse_field_path",
    "validate_field_path_syntax",
]
