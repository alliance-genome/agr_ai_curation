"""Provider-neutral workspace projections and review-row materialization."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.orm import Session

from src.schemas.curation_workspace import (
    DomainEnvelopeEvidenceAnchorProjection,
    DomainEnvelopeReviewRow,
    DomainEnvelopeReviewRowsResponse,
    DomainEnvelopeReviewRowSummaryField,
    DomainEnvelopeValidationFindingProjection,
    DomainEnvelopeValidationStatus,
    DomainEnvelopeValidationSummaryProjection,
    EvidenceAnchor,
    EvidenceAnchorKind,
    EvidenceLocatorQuality,
    EvidenceSupportsDecision,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    ObjectRef,
    ValidationFinding,
    ValidationFindingSeverity,
    ValidationFindingStatus,
    parse_field_path,
)
from src.schemas.domain_pack_metadata import (
    DomainPackFieldDefinition,
    DomainPackMetadata,
    DomainPackObjectDefinition,
)
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
    ValidatorBindingMatch,
)


REVIEW_ROW_PROJECTION_TYPE = "workspace_review_row"
_MISSING = object()

VALIDATION_STATUS_RANK: dict[DomainEnvelopeValidationStatus, int] = {
    DomainEnvelopeValidationStatus.RESOLVED: 0,
    DomainEnvelopeValidationStatus.WAIVED: 0,
    DomainEnvelopeValidationStatus.PLANNED: 1,
    DomainEnvelopeValidationStatus.UNDER_DEVELOPMENT: 2,
    DomainEnvelopeValidationStatus.UNRESOLVED: 3,
    DomainEnvelopeValidationStatus.BLOCKED: 4,
}

SEVERITY_RANK: dict[str, int] = {
    ValidationFindingSeverity.INFO.value: 0,
    ValidationFindingSeverity.WARNING.value: 1,
    ValidationFindingSeverity.ERROR.value: 2,
    ValidationFindingSeverity.BLOCKER.value: 3,
}


class DomainEnvelopeMaterializationError(RuntimeError):
    """Raised when a persisted envelope cannot be materialized for review."""


class DomainEnvelopeRevisionUnavailableError(DomainEnvelopeMaterializationError):
    """Raised when the requested envelope revision is not the persisted revision."""


class DomainEnvelopeReviewRowMaterializer(Protocol):
    """Domain-pack-owned review-row materializer contract."""

    def materialize(
        self,
        envelope: DomainEnvelope,
        *,
        envelope_revision: int,
    ) -> list[DomainEnvelopeReviewRow]:
        """Return review rows regenerated from the supplied envelope revision."""


@dataclass(frozen=True)
class DomainPackMetadataReviewRowMaterializer:
    """Metadata-driven materializer that keeps provider mappings in domain packs."""

    metadata: DomainPackMetadata

    def materialize(
        self,
        envelope: DomainEnvelope,
        *,
        envelope_revision: int,
    ) -> list[DomainEnvelopeReviewRow]:
        """Project one review row per non-metadata-only envelope object."""

        if envelope.domain_pack_id != self.metadata.pack_id:
            raise DomainEnvelopeMaterializationError(
                "Envelope domain_pack_id does not match materializer metadata: "
                f"{envelope.domain_pack_id!r} != {self.metadata.pack_id!r}"
            )
        if envelope_revision < 1:
            raise DomainEnvelopeMaterializationError(
                "envelope_revision must be greater than zero"
            )

        object_definitions = {
            definition.object_type: definition
            for definition in self.metadata.object_definitions
        }
        validation_state_by_object = _validation_state_by_object(envelope)
        unavailable_capabilities = _unavailable_validator_capabilities_by_target(
            envelope,
            metadata=self.metadata,
        )
        rows: list[DomainEnvelopeReviewRow] = []

        for object_index, domain_object in enumerate(envelope.objects):
            object_definition = object_definitions.get(domain_object.object_type)
            object_id = stable_object_id(domain_object)
            object_role = _object_role(
                domain_object,
                object_definition,
                object_role_key=_object_role_key(self.metadata),
            )
            if object_role == "metadata_only":
                continue

            display_config = _workspace_display_config(domain_object, object_definition)
            summary_fields = _summary_fields(
                domain_object,
                object_definition=object_definition,
                display_config=display_config,
                unavailable_capabilities_by_field=(
                    unavailable_capabilities["by_field"]
                ),
            )
            display_label = _display_label(
                domain_object,
                summary_fields=summary_fields,
                display_config=display_config,
            )
            secondary_label = _secondary_label(
                domain_object,
                summary_fields=summary_fields,
                display_config=display_config,
            )

            rows.append(
                DomainEnvelopeReviewRow(
                    envelope_id=envelope.envelope_id,
                    object_id=object_id,
                    envelope_revision=envelope_revision,
                    domain_pack_id=envelope.domain_pack_id,
                    domain_pack_version=envelope.domain_pack_version,
                    object_type=domain_object.object_type,
                    object_role=object_role,
                    status=domain_object.status.value,
                    validation_state=validation_state_by_object[object_id],
                    projection_type=_projection_type(display_config),
                    projection_key=_projection_key(display_config, object_id=object_id),
                    display_label=display_label,
                    secondary_label=secondary_label,
                    summary_fields=summary_fields,
                    schema_provider=(
                        domain_object.schema_ref.provider
                        if domain_object.schema_ref is not None
                        else None
                    ),
                    schema_ref=(
                        domain_object.schema_ref.model_dump(mode="json")
                        if domain_object.schema_ref is not None
                        else {}
                    ),
                    object_model_ref=_object_model_ref(domain_object, object_definition),
                    model_field_ref=_model_field_ref(domain_object, object_definition),
                    metadata={
                        "semantic_source": "domain_envelope.objects",
                        "materializer": type(self).__name__,
                        "object_index": object_index,
                        **_unavailable_capabilities_metadata(
                            _capabilities_for_object(
                                object_id,
                                unavailable_capabilities=unavailable_capabilities,
                            )
                        ),
                    },
                )
            )

        return rows


def materialize_persisted_envelope_review_rows(
    db: Session,
    envelope_id: str,
    *,
    revision: int | None = None,
    materializer: DomainEnvelopeReviewRowMaterializer | None = None,
) -> DomainEnvelopeReviewRowsResponse:
    """Regenerate review rows from the currently persisted envelope JSON."""

    from src.lib.curation_workspace.models import DomainEnvelopeModel

    normalized_envelope_id = _required_string(envelope_id, field_name="envelope_id")
    envelope_row = db.get(DomainEnvelopeModel, normalized_envelope_id)
    if envelope_row is None:
        raise DomainEnvelopeMaterializationError(
            f"Domain envelope {normalized_envelope_id} was not found"
        )
    if revision is not None and envelope_row.revision != revision:
        raise DomainEnvelopeRevisionUnavailableError(
            f"Domain envelope {normalized_envelope_id} is at revision "
            f"{envelope_row.revision}, not requested revision {revision}"
        )

    envelope = DomainEnvelope.model_validate(envelope_row.envelope_json)
    resolved_materializer = materializer or _registered_materializer_for(
        envelope.domain_pack_id
    )
    rows = resolved_materializer.materialize(
        envelope,
        envelope_revision=envelope_row.revision,
    )
    return DomainEnvelopeReviewRowsResponse(
        envelope_id=envelope.envelope_id,
        envelope_revision=envelope_row.revision,
        row_count=len(rows),
        rows=rows,
    )


def project_evidence_anchor_projections(
    envelope: DomainEnvelope,
    *,
    envelope_revision: int,
    document_id: str | None = None,
    object_id: str | None = None,
) -> list[DomainEnvelopeEvidenceAnchorProjection]:
    """Project curator evidence navigation anchors from envelope metadata records."""

    records_by_id, record_ids_by_metadata_path = _evidence_record_indexes(
        envelope.metadata
    )
    projections: list[DomainEnvelopeEvidenceAnchorProjection] = []

    for domain_object in envelope.objects:
        domain_object_id = stable_object_id(domain_object)
        if object_id is not None and domain_object_id != object_id:
            continue

        seen_projection_keys: set[tuple[str, str | None]] = set()
        for evidence_record_id in _object_evidence_record_ids(
            domain_object,
            records_by_id,
            record_ids_by_metadata_path,
        ):
            evidence_record = records_by_id.get(evidence_record_id)
            if evidence_record is None:
                continue
            for field_path in _projection_field_paths(evidence_record):
                projection_key = (evidence_record_id, field_path)
                if projection_key in seen_projection_keys:
                    continue
                seen_projection_keys.add(projection_key)
                projections.append(
                    _evidence_anchor_projection(
                        envelope=envelope,
                        envelope_revision=envelope_revision,
                        domain_object=domain_object,
                        evidence_record_id=evidence_record_id,
                        evidence_record=evidence_record,
                        field_path=field_path,
                        document_id=document_id,
                    )
                )

    return sorted(
        projections,
        key=lambda projection: (
            projection.object_id,
            projection.field_path or "",
            projection.evidence_record_id,
            projection.anchor_id,
        ),
    )


def project_validation_summary_projections(
    envelope: DomainEnvelope,
    *,
    envelope_revision: int,
    object_id: str | None = None,
) -> list[DomainEnvelopeValidationSummaryProjection]:
    """Project validation state summaries grouped by envelope object and field path."""

    object_id_by_ref = _object_id_by_ref(envelope)
    object_type_by_id = {
        stable_object_id(domain_object): domain_object.object_type
        for domain_object in envelope.objects
    }
    grouped: dict[
        tuple[str | None, str | None],
        list[DomainEnvelopeValidationFindingProjection],
    ] = {}

    for finding_index, finding in enumerate(envelope.validation_findings):
        target_object_id, field_path = _finding_target(finding, object_id_by_ref)
        if object_id is not None and target_object_id != object_id:
            continue
        finding_projection = _validation_finding_projection(
            envelope=envelope,
            envelope_revision=envelope_revision,
            finding=finding,
            finding_index=finding_index,
            object_id=target_object_id,
            object_type=object_type_by_id.get(target_object_id or ""),
            field_path=field_path,
        )
        grouped.setdefault((target_object_id, field_path), []).append(
            finding_projection
        )

    summaries = [
        _validation_summary_projection(
            envelope_id=envelope.envelope_id,
            envelope_revision=envelope_revision,
            object_id=group_key[0],
            object_type=object_type_by_id.get(group_key[0] or ""),
            field_path=group_key[1],
            findings=findings,
        )
        for group_key, findings in grouped.items()
    ]
    return sorted(
        summaries,
        key=lambda summary: (
            summary.object_id or "",
            summary.field_path or "",
            summary.summary_id,
        ),
    )


def stable_object_id(domain_object: CuratableObjectEnvelope) -> str:
    """Return the stable object identifier used by envelope projections."""

    if domain_object.object_id is not None:
        return domain_object.object_id
    if domain_object.pending_ref_id is not None:
        return domain_object.pending_ref_id
    raise DomainEnvelopeMaterializationError(
        "CuratableObjectEnvelope has neither object_id nor pending_ref_id"
    )


def _registered_materializer_for(domain_pack_id: str) -> DomainEnvelopeReviewRowMaterializer:
    from src.lib.curation_workspace.adapter_registry import load_curation_adapter_registry

    registry = load_curation_adapter_registry()
    materializer = registry.get_review_row_materializer_for_domain_pack(domain_pack_id)
    if materializer is None:
        raise DomainEnvelopeMaterializationError(
            f"No review-row materializer is registered for domain_pack_id={domain_pack_id!r}"
        )
    return materializer


def _workspace_display_config(
    domain_object: CuratableObjectEnvelope,
    object_definition: DomainPackObjectDefinition | None,
) -> Mapping[str, Any]:
    object_config = domain_object.metadata.get("workspace_display")
    if isinstance(object_config, Mapping):
        return object_config
    if object_definition is None:
        return {}
    definition_config = object_definition.metadata.get("workspace_display")
    return definition_config if isinstance(definition_config, Mapping) else {}


def _summary_fields(
    domain_object: CuratableObjectEnvelope,
    *,
    object_definition: DomainPackObjectDefinition | None,
    display_config: Mapping[str, Any],
    unavailable_capabilities_by_field: Mapping[tuple[str, str], tuple[dict[str, Any], ...]],
) -> list[DomainEnvelopeReviewRowSummaryField]:
    field_definitions = {
        field.field_path: field
        for field in (object_definition.fields if object_definition is not None else [])
    }
    configured_paths = [
        path
        for path in display_config.get("summary_fields", [])
        if isinstance(path, str) and path.strip()
    ]
    field_paths = configured_paths or [
        field.field_path
        for field in (object_definition.fields if object_definition is not None else [])
        if _payload_value(domain_object.payload, field.field_path) is not _MISSING
    ]
    if not field_paths:
        field_paths = _leaf_payload_paths(domain_object.payload)

    summary_fields: list[DomainEnvelopeReviewRowSummaryField] = []
    seen: set[str] = set()
    for field_path in field_paths:
        if field_path in seen:
            continue
        value = _payload_value(domain_object.payload, field_path)
        if value is _MISSING:
            continue
        seen.add(field_path)
        field_definition = field_definitions.get(field_path)
        metadata = dict(field_definition.metadata) if field_definition is not None else {}
        metadata.update(
            _unavailable_capabilities_metadata(
                unavailable_capabilities_by_field.get(
                    (stable_object_id(domain_object), field_path),
                    (),
                )
            )
        )
        summary_fields.append(
            DomainEnvelopeReviewRowSummaryField(
                field_path=field_path,
                label=_field_label(field_path, field_definition),
                value=value,
                field_type=(
                    field_definition.field_type.value
                    if field_definition is not None
                    else _value_field_type(value)
                ),
                metadata=metadata,
            )
        )

    return summary_fields


def _unavailable_validator_capabilities_by_target(
    envelope: DomainEnvelope,
    *,
    metadata: DomainPackMetadata,
) -> dict[str, Any]:
    registry = DomainPackValidationRegistry.from_domain_pack(
        LoadedDomainPack(
            pack_id=metadata.pack_id,
            display_name=metadata.display_name,
            version=metadata.version,
            pack_path=Path("."),
            metadata_path=Path("."),
            metadata=metadata,
        )
    )
    matches = registry.match_bindings(
        envelope,
        states=[ValidationBindingState.UNDER_DEVELOPMENT],
    )
    by_object: dict[str, list[dict[str, Any]]] = {}
    by_field: dict[tuple[str, str], list[dict[str, Any]]] = {}
    global_capabilities: list[dict[str, Any]] = []

    for match in matches:
        capability = _unavailable_validator_capability(match)
        if match.object_envelope is None:
            global_capabilities.append(capability)
            continue

        object_id = stable_object_id(match.object_envelope)
        if match.field_path is None:
            by_object.setdefault(object_id, []).append(capability)
            continue

        by_field.setdefault((object_id, match.field_path), []).append(capability)

    return {
        "global": tuple(global_capabilities),
        "by_object": {
            object_id: tuple(capabilities)
            for object_id, capabilities in by_object.items()
        },
        "by_field": {
            target: tuple(capabilities)
            for target, capabilities in by_field.items()
        },
    }


def _unavailable_validator_capability(
    match: ValidatorBindingMatch,
) -> dict[str, Any]:
    binding = match.binding
    if not binding.display_name:
        raise DomainEnvelopeMaterializationError(
            "Under-development validator binding "
            f"{binding.binding_id!r} must declare display_name"
        )
    if not binding.reason:
        raise DomainEnvelopeMaterializationError(
            "Under-development validator binding "
            f"{binding.binding_id!r} must declare state_explanation"
        )
    affected_fields = _match_affected_fields(match)
    payload: dict[str, Any] = {
        "validator_binding_id": binding.binding_id,
        "state": binding.state.value,
        "label": binding.display_name,
        "state_explanation": binding.reason,
        "scope": "field" if match.field_path is not None else (
            "object" if match.object_envelope is not None else "pack"
        ),
        "affected_fields": affected_fields,
    }
    if match.object_type is not None:
        payload["object_type"] = match.object_type
    return {key: value for key, value in payload.items() if value not in (None, "", [])}


def _match_affected_fields(match: ValidatorBindingMatch) -> list[str]:
    if match.field_path is not None:
        return [match.field_path]
    if match.object_definition is not None:
        return [field.field_path for field in match.object_definition.fields]
    return list(match.binding.field_paths)


def _capabilities_for_object(
    object_id: str,
    *,
    unavailable_capabilities: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    capabilities = list(unavailable_capabilities.get("global", ()))
    by_object = unavailable_capabilities.get("by_object", {})
    if isinstance(by_object, Mapping):
        capabilities.extend(by_object.get(object_id, ()))
    by_field = unavailable_capabilities.get("by_field", {})
    if isinstance(by_field, Mapping):
        for (field_object_id, _field_path), field_capabilities in by_field.items():
            if field_object_id == object_id:
                capabilities.extend(field_capabilities)
    return tuple(capabilities)


def _unavailable_capabilities_metadata(
    capabilities: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    payload = [dict(capability) for capability in capabilities]
    if not payload:
        return {}
    return {"unavailable_validator_capabilities": payload}


def _display_label(
    domain_object: CuratableObjectEnvelope,
    *,
    summary_fields: Sequence[DomainEnvelopeReviewRowSummaryField],
    display_config: Mapping[str, Any],
) -> str:
    configured_field = display_config.get("primary_label_field")
    if isinstance(configured_field, str):
        configured_value = _payload_value(domain_object.payload, configured_field)
        if configured_value is not _MISSING:
            normalized = _display_value(configured_value)
            if normalized is not None:
                return normalized

    for field in summary_fields:
        normalized = _display_value(field.value)
        if normalized is not None:
            return normalized
    return stable_object_id(domain_object)


def _secondary_label(
    domain_object: CuratableObjectEnvelope,
    *,
    summary_fields: Sequence[DomainEnvelopeReviewRowSummaryField],
    display_config: Mapping[str, Any],
) -> str | None:
    configured_field = display_config.get("secondary_label_field")
    if isinstance(configured_field, str):
        configured_value = _payload_value(domain_object.payload, configured_field)
        if configured_value is not _MISSING:
            return _display_value(configured_value)

    if len(summary_fields) < 2:
        return None
    return _display_value(summary_fields[1].value)


def _projection_type(display_config: Mapping[str, Any]) -> str:
    value = display_config.get("projection_type")
    return (
        value.strip()
        if isinstance(value, str) and value.strip()
        else REVIEW_ROW_PROJECTION_TYPE
    )


def _projection_key(display_config: Mapping[str, Any], *, object_id: str) -> str:
    value = display_config.get("projection_key")
    return value.strip() if isinstance(value, str) and value.strip() else object_id


def _object_role(
    domain_object: CuratableObjectEnvelope,
    object_definition: DomainPackObjectDefinition | None,
    *,
    object_role_key: str,
) -> str | None:
    if domain_object.object_role is not None:
        return domain_object.object_role
    metadata_role = domain_object.metadata.get(object_role_key)
    if isinstance(metadata_role, str) and metadata_role.strip():
        return metadata_role.strip()
    if object_definition is not None:
        definition_role = object_definition.metadata.get(object_role_key)
        if isinstance(definition_role, str) and definition_role.strip():
            return definition_role.strip()
    return None


def _object_role_key(metadata: DomainPackMetadata) -> str:
    value = metadata.metadata.get("object_role_key")
    return value.strip() if isinstance(value, str) and value.strip() else "object_role"


def _object_model_ref(
    domain_object: CuratableObjectEnvelope,
    object_definition: DomainPackObjectDefinition | None,
) -> dict[str, Any]:
    refs = _selected_metadata_refs(
        domain_object.metadata,
        keys=("object_model_ref", "object_model_ref_json", "provider_refs"),
    )
    if refs or object_definition is None:
        return refs
    return _selected_metadata_refs(
        object_definition.metadata,
        keys=("object_model_ref", "object_model_ref_json", "provider_refs"),
    )


def _model_field_ref(
    domain_object: CuratableObjectEnvelope,
    object_definition: DomainPackObjectDefinition | None,
) -> dict[str, Any]:
    refs = _selected_metadata_refs(
        domain_object.metadata,
        keys=("model_field_ref", "model_field_ref_json", "field_provider_refs"),
    )
    if object_definition is None:
        return refs

    field_refs: dict[str, Any] = {}
    for field in object_definition.fields:
        provider_refs = _selected_metadata_refs(
            field.metadata,
            keys=("model_field_ref", "model_field_ref_json", "provider_refs"),
        )
        if provider_refs:
            field_refs[field.field_path] = provider_refs
    if field_refs:
        refs["domain_pack_fields"] = field_refs
    return refs


def _selected_metadata_refs(
    metadata: Mapping[str, Any],
    *,
    keys: tuple[str, ...],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, Mapping):
            payload[key] = dict(value)
    return payload


def _validation_state_by_object(envelope: DomainEnvelope) -> dict[str, str]:
    from src.lib.domain_envelopes.persistence import (
        OBJECT_VALIDATION_STATE_BLOCKED,
        OBJECT_VALIDATION_STATE_CLEAR,
        OBJECT_VALIDATION_STATE_ERROR,
        OBJECT_VALIDATION_STATE_INFO,
        OBJECT_VALIDATION_STATE_WARNING,
    )

    validation_state_by_severity = {
        ValidationFindingSeverity.INFO: OBJECT_VALIDATION_STATE_INFO,
        ValidationFindingSeverity.WARNING: OBJECT_VALIDATION_STATE_WARNING,
        ValidationFindingSeverity.ERROR: OBJECT_VALIDATION_STATE_ERROR,
        ValidationFindingSeverity.BLOCKER: OBJECT_VALIDATION_STATE_BLOCKED,
    }
    validation_state_rank = {
        OBJECT_VALIDATION_STATE_CLEAR: 0,
        OBJECT_VALIDATION_STATE_INFO: 1,
        OBJECT_VALIDATION_STATE_WARNING: 2,
        OBJECT_VALIDATION_STATE_ERROR: 3,
        OBJECT_VALIDATION_STATE_BLOCKED: 4,
    }
    object_id_by_ref = _object_id_by_ref(envelope)
    state_by_object = {
        stable_object_id(domain_object): OBJECT_VALIDATION_STATE_CLEAR
        for domain_object in envelope.objects
    }
    for finding in envelope.validation_findings:
        if finding.status is not ValidationFindingStatus.OPEN:
            continue
        object_ref = (
            finding.field_ref.object_ref
            if finding.field_ref is not None
            else finding.object_ref
        )
        if object_ref is None:
            continue
        object_id = _resolve_object_ref(object_ref, object_id_by_ref)
        if object_id is None or object_id not in state_by_object:
            continue
        candidate_state = validation_state_by_severity[finding.severity]
        if (
            validation_state_rank[candidate_state]
            > validation_state_rank[state_by_object[object_id]]
        ):
            state_by_object[object_id] = candidate_state
    return state_by_object


def _evidence_record_indexes(
    metadata: Mapping[str, Any],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, str]]:
    records_by_id: dict[str, Mapping[str, Any]] = {}
    record_ids_by_metadata_path: dict[str, str] = {}

    for metadata_path, raw_records in _metadata_evidence_record_lists(metadata):
        for record_index, raw_record in enumerate(raw_records):
            if not isinstance(raw_record, Mapping):
                continue
            evidence_record_id = _optional_string(raw_record.get("evidence_record_id"))
            if evidence_record_id is None:
                continue
            records_by_id[evidence_record_id] = raw_record
            record_ids_by_metadata_path[
                f"{metadata_path}[{record_index}]"
            ] = evidence_record_id

    return records_by_id, record_ids_by_metadata_path


def _metadata_evidence_record_lists(
    metadata: Mapping[str, Any],
) -> list[tuple[str, list[Any]]]:
    evidence_record_lists: list[tuple[str, list[Any]]] = []

    raw_records = metadata.get("evidence_records")
    if isinstance(raw_records, list):
        evidence_record_lists.append(("evidence_records", raw_records))

    extraction_metadata = metadata.get("extraction_metadata")
    if isinstance(extraction_metadata, Mapping):
        raw_nested_records = extraction_metadata.get("evidence_records")
        if isinstance(raw_nested_records, list):
            evidence_record_lists.append(
                ("extraction_metadata.evidence_records", raw_nested_records)
            )

    return evidence_record_lists


def _object_evidence_record_ids(
    domain_object: CuratableObjectEnvelope,
    records_by_id: Mapping[str, Mapping[str, Any]],
    record_ids_by_metadata_path: Mapping[str, str],
) -> list[str]:
    evidence_record_ids = _unique_strings(domain_object.evidence_record_ids)
    for metadata_ref in domain_object.metadata_refs:
        evidence_record_id = record_ids_by_metadata_path.get(metadata_ref.metadata_path)
        if (
            evidence_record_id is not None
            and evidence_record_id not in evidence_record_ids
        ):
            evidence_record_ids.append(evidence_record_id)
    for evidence_record_id, evidence_record in records_by_id.items():
        if evidence_record_id in evidence_record_ids:
            continue
        if _evidence_record_targets_object(evidence_record, domain_object):
            evidence_record_ids.append(evidence_record_id)
    return evidence_record_ids


def _evidence_record_targets_object(
    evidence_record: Mapping[str, Any],
    domain_object: CuratableObjectEnvelope,
) -> bool:
    domain_object_id = stable_object_id(domain_object)
    if _optional_string(evidence_record.get("object_id")) == domain_object_id:
        return True
    if (
        domain_object.pending_ref_id is not None
        and _optional_string(evidence_record.get("pending_ref_id"))
        == domain_object.pending_ref_id
    ):
        return True

    raw_object_ref = evidence_record.get("object_ref")
    if not isinstance(raw_object_ref, Mapping):
        return False
    if _optional_string(raw_object_ref.get("object_id")) == domain_object_id:
        return True
    return (
        domain_object.pending_ref_id is not None
        and _optional_string(raw_object_ref.get("pending_ref_id"))
        == domain_object.pending_ref_id
    )


def _evidence_anchor_projection(
    *,
    envelope: DomainEnvelope,
    envelope_revision: int,
    domain_object: CuratableObjectEnvelope,
    evidence_record_id: str,
    evidence_record: Mapping[str, Any],
    field_path: str | None,
    document_id: str | None,
) -> DomainEnvelopeEvidenceAnchorProjection:
    anchor = _evidence_anchor(evidence_record)
    domain_object_id = stable_object_id(domain_object)
    source_document_id = _first_string(
        evidence_record,
        "document_id",
        "source_document_id",
        "pdf_document_id",
    )
    envelope_document_id = _first_string(
        envelope.metadata,
        "source_document_id",
        "document_id",
    )
    projection_document_id = source_document_id or envelope_document_id or document_id
    chunk_ids = list(anchor.chunk_ids)
    return DomainEnvelopeEvidenceAnchorProjection(
        anchor_id=_projection_id(
            "evidence",
            envelope.envelope_id,
            envelope_revision,
            domain_object_id,
            field_path,
            evidence_record_id,
        ),
        evidence_record_id=evidence_record_id,
        envelope_id=envelope.envelope_id,
        object_id=domain_object_id,
        object_type=domain_object.object_type,
        field_path=field_path,
        envelope_revision=envelope_revision,
        document_id=projection_document_id,
        quote=_quote_from_anchor(anchor),
        page_number=anchor.page_number,
        page_label=anchor.page_label,
        chunk_id=chunk_ids[0] if chunk_ids else None,
        chunk_ids=chunk_ids,
        section_title=anchor.section_title,
        subsection_title=anchor.subsection_title,
        figure_reference=anchor.figure_reference,
        table_reference=anchor.table_reference,
        source_id=_first_string(evidence_record, "source_id", "source"),
        source_title=_first_string(evidence_record, "source_title", "title"),
        source_url=_first_string(evidence_record, "source_url", "url", "uri"),
        anchor=anchor,
        metadata={
            "object_evidence_record_ids": list(domain_object.evidence_record_ids),
            "source_record": dict(evidence_record),
        },
    )


def _evidence_anchor(evidence_record: Mapping[str, Any]) -> EvidenceAnchor:
    raw_anchor = evidence_record.get("anchor")
    if isinstance(raw_anchor, Mapping):
        return EvidenceAnchor.model_validate(dict(raw_anchor))

    quote = _first_string(
        evidence_record,
        "verified_quote",
        "quote",
        "snippet_text",
        "sentence_text",
        "text",
    )
    page_number = _page_number(
        evidence_record.get("page_number", evidence_record.get("page"))
    )
    section_title = _first_string(evidence_record, "section_title", "section")
    subsection_title = _first_string(evidence_record, "subsection_title", "subsection")
    chunk_ids = _chunk_ids(evidence_record)

    if quote:
        anchor_kind = EvidenceAnchorKind.SNIPPET
        locator_quality = EvidenceLocatorQuality.EXACT_QUOTE
    elif page_number is not None:
        anchor_kind = EvidenceAnchorKind.PAGE
        locator_quality = EvidenceLocatorQuality.PAGE_ONLY
    else:
        anchor_kind = EvidenceAnchorKind.DOCUMENT
        locator_quality = EvidenceLocatorQuality.DOCUMENT_ONLY

    return EvidenceAnchor(
        anchor_kind=anchor_kind,
        locator_quality=locator_quality,
        supports_decision=EvidenceSupportsDecision.SUPPORTS,
        snippet_text=quote,
        sentence_text=quote,
        normalized_text=_first_string(evidence_record, "normalized_text"),
        viewer_search_text=quote,
        viewer_highlightable=bool(quote),
        page_number=page_number,
        page_label=_first_string(evidence_record, "page_label"),
        section_title=section_title,
        subsection_title=subsection_title,
        figure_reference=_first_string(evidence_record, "figure_reference"),
        table_reference=_first_string(evidence_record, "table_reference"),
        chunk_ids=chunk_ids,
    )


def _validation_finding_projection(
    *,
    envelope: DomainEnvelope,
    envelope_revision: int,
    finding: ValidationFinding,
    finding_index: int,
    object_id: str | None,
    object_type: str | None,
    field_path: str | None,
) -> DomainEnvelopeValidationFindingProjection:
    summary_status = _validation_status(finding)
    finding_id = finding.finding_id or _projection_id(
        "validation-finding",
        envelope.envelope_id,
        envelope_revision,
        finding_index,
        finding.model_dump(mode="json"),
    )
    return DomainEnvelopeValidationFindingProjection(
        finding_id=finding_id,
        envelope_id=envelope.envelope_id,
        object_id=object_id,
        object_type=object_type,
        field_path=field_path,
        envelope_revision=envelope_revision,
        severity=finding.severity.value,
        finding_status=finding.status.value,
        summary_status=summary_status,
        code=finding.code,
        message=finding.message,
        details=dict(finding.details),
    )


def _validation_summary_projection(
    *,
    envelope_id: str,
    envelope_revision: int,
    object_id: str | None,
    object_type: str | None,
    field_path: str | None,
    findings: Sequence[DomainEnvelopeValidationFindingProjection],
) -> DomainEnvelopeValidationSummaryProjection:
    status = _highest_status(finding.summary_status for finding in findings)
    highest_severity = _highest_severity(finding.severity for finding in findings)
    ordered_findings = sorted(
        findings,
        key=lambda finding: (
            -SEVERITY_RANK.get(finding.severity, -1),
            -VALIDATION_STATUS_RANK[finding.summary_status],
            finding.finding_id,
        ),
    )
    return DomainEnvelopeValidationSummaryProjection(
        summary_id=_projection_id(
            "validation-summary",
            envelope_id,
            envelope_revision,
            object_id,
            field_path,
        ),
        envelope_id=envelope_id,
        object_id=object_id,
        object_type=object_type,
        field_path=field_path,
        envelope_revision=envelope_revision,
        status=status,
        highest_severity=highest_severity,
        finding_count=len(ordered_findings),
        open_finding_count=sum(
            1
            for finding in ordered_findings
            if finding.finding_status == ValidationFindingStatus.OPEN.value
        ),
        finding_ids=[finding.finding_id for finding in ordered_findings],
        codes=_unique_strings(finding.code for finding in ordered_findings),
        messages=_unique_strings(finding.message for finding in ordered_findings),
        findings=list(ordered_findings),
    )


def _validation_status(finding: ValidationFinding) -> DomainEnvelopeValidationStatus:
    if finding.status is ValidationFindingStatus.RESOLVED:
        return DomainEnvelopeValidationStatus.RESOLVED
    if finding.status is ValidationFindingStatus.WAIVED:
        return DomainEnvelopeValidationStatus.WAIVED

    details = dict(finding.details)
    validation_metadata = details.get("validation_metadata")
    if isinstance(validation_metadata, Mapping):
        binding_state = _optional_string(validation_metadata.get("binding_state"))
        if binding_state == DomainEnvelopeValidationStatus.BLOCKED.value:
            return DomainEnvelopeValidationStatus.BLOCKED
        if binding_state == DomainEnvelopeValidationStatus.PLANNED.value:
            return DomainEnvelopeValidationStatus.PLANNED
        if (
            _optional_string(validation_metadata.get("definition_state"))
            == "in_development"
        ):
            return DomainEnvelopeValidationStatus.UNDER_DEVELOPMENT

    if _optional_string(details.get("failure_classification")) == "blocked":
        return DomainEnvelopeValidationStatus.BLOCKED
    if _optional_string(details.get("failure_classification")) == "under_development":
        return DomainEnvelopeValidationStatus.UNDER_DEVELOPMENT

    lookup_attempts = details.get("lookup_attempts")
    if isinstance(lookup_attempts, list):
        lookup_statuses = {
            _optional_string(attempt.get("lookup_status"))
            for attempt in lookup_attempts
            if isinstance(attempt, Mapping)
        }
        if "blocked" in lookup_statuses:
            return DomainEnvelopeValidationStatus.BLOCKED
        if "under_development" in lookup_statuses:
            return DomainEnvelopeValidationStatus.UNDER_DEVELOPMENT

    return DomainEnvelopeValidationStatus.UNRESOLVED


def _finding_target(
    finding: ValidationFinding,
    object_id_by_ref: Mapping[tuple[str, str], str],
) -> tuple[str | None, str | None]:
    if finding.field_ref is not None:
        return (
            _resolve_object_ref(finding.field_ref.object_ref, object_id_by_ref),
            finding.field_ref.field_path,
        )
    if finding.object_ref is not None:
        return _resolve_object_ref(finding.object_ref, object_id_by_ref), None
    return None, None


def _projection_field_paths(evidence_record: Mapping[str, Any]) -> list[str | None]:
    field_paths = _unique_strings(evidence_record.get("field_paths"))
    if not field_paths:
        field_path = _optional_string(evidence_record.get("field_path"))
        if field_path is not None:
            field_paths = [field_path]
    return list(field_paths) if field_paths else [None]


def _quote_from_anchor(anchor: EvidenceAnchor) -> str | None:
    for value in (anchor.snippet_text, anchor.sentence_text, anchor.viewer_search_text):
        normalized = _optional_string(value)
        if normalized is not None:
            return normalized
    return None


def _chunk_ids(evidence_record: Mapping[str, Any]) -> list[str]:
    raw_chunk_ids = evidence_record.get("chunk_ids")
    chunk_ids = _unique_strings(raw_chunk_ids)
    chunk_id = _optional_string(evidence_record.get("chunk_id"))
    if chunk_id is not None and chunk_id not in chunk_ids:
        chunk_ids.append(chunk_id)
    return chunk_ids


def _object_id_by_ref(envelope: DomainEnvelope) -> dict[tuple[str, str], str]:
    object_id_by_ref: dict[tuple[str, str], str] = {}
    for domain_object in envelope.objects:
        object_id = stable_object_id(domain_object)
        if domain_object.object_id is not None:
            object_id_by_ref[("object_id", domain_object.object_id)] = object_id
        if domain_object.pending_ref_id is not None:
            object_id_by_ref[("pending_ref_id", domain_object.pending_ref_id)] = object_id
    return object_id_by_ref


def _resolve_object_ref(
    object_ref: ObjectRef,
    object_id_by_ref: Mapping[tuple[str, str], str],
) -> str | None:
    return object_id_by_ref.get(object_ref.ref_key())


def _payload_value(payload: Mapping[str, Any], field_path: str) -> Any:
    try:
        parts = parse_field_path(field_path)
    except ValueError:
        return _MISSING

    current: Any = payload
    for part in parts:
        if isinstance(part, str):
            if not isinstance(current, Mapping) or part not in current:
                return _MISSING
            current = current[part]
            continue
        if not isinstance(current, Sequence) or isinstance(
            current, (str, bytes, bytearray)
        ):
            return _MISSING
        if part >= len(current):
            return _MISSING
        current = current[part]
    return current


def _leaf_payload_paths(payload: Any, *, prefix: str = "") -> list[str]:
    if isinstance(payload, Mapping):
        paths: list[str] = []
        for key, value in payload.items():
            if not isinstance(key, str):
                continue
            field_key = f"{prefix}.{key}" if prefix else key
            paths.extend(_leaf_payload_paths(value, prefix=field_key))
        return paths
    if isinstance(payload, list):
        paths = []
        for index, value in enumerate(payload):
            if not prefix:
                continue
            paths.extend(_leaf_payload_paths(value, prefix=f"{prefix}[{index}]"))
        return paths
    return [prefix] if prefix else []


def _field_label(
    field_path: str,
    field_definition: DomainPackFieldDefinition | None,
) -> str:
    if field_definition is not None and field_definition.display_name:
        return field_definition.display_name
    segments = field_path.replace("[", ".").replace("]", "").split(".")
    normalized = [
        segment.replace("_", " ").strip().title()
        for segment in segments
        if segment
    ]
    return " / ".join(normalized) if normalized else field_path


def _value_field_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    return "any"


def _display_value(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    normalized = str(value).strip()
    return normalized or None


def _highest_status(
    statuses: Sequence[DomainEnvelopeValidationStatus] | Any,
) -> DomainEnvelopeValidationStatus:
    return max(
        statuses,
        key=lambda status: VALIDATION_STATUS_RANK[status],
        default=DomainEnvelopeValidationStatus.RESOLVED,
    )


def _highest_severity(severities: Sequence[str] | Any) -> str | None:
    highest: str | None = None
    for severity in severities:
        if severity not in SEVERITY_RANK:
            continue
        if highest is None or SEVERITY_RANK[severity] > SEVERITY_RANK[highest]:
            highest = severity
    return highest


def _projection_id(*parts: Any) -> str:
    payload = json.dumps(parts, sort_keys=True, default=str)
    digest = sha256(payload.encode("utf-8")).hexdigest()
    return f"domain-projection:{digest}"


def _first_string(record: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        normalized = _optional_string(record.get(key))
        if normalized is not None:
            return normalized
    return None


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _required_string(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainEnvelopeMaterializationError(
            f"{field_name} must be a non-empty string"
        )
    normalized = value.strip()
    if normalized != value:
        raise DomainEnvelopeMaterializationError(
            f"{field_name} must not include leading or trailing whitespace"
        )
    return normalized


def _unique_strings(values: Any) -> list[str]:
    if isinstance(values, str):
        iterable: Sequence[Any] = [values]
    elif isinstance(values, Iterable):
        iterable = values
    else:
        return []

    unique_values: list[str] = []
    seen: set[str] = set()
    for value in iterable:
        normalized = _optional_string(value)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        unique_values.append(normalized)
    return unique_values


def _page_number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 1:
        return value
    return None


__all__ = [
    "DomainEnvelopeMaterializationError",
    "DomainEnvelopeRevisionUnavailableError",
    "DomainEnvelopeReviewRowMaterializer",
    "DomainPackMetadataReviewRowMaterializer",
    "REVIEW_ROW_PROJECTION_TYPE",
    "materialize_persisted_envelope_review_rows",
    "project_evidence_anchor_projections",
    "project_validation_summary_projections",
    "stable_object_id",
]
