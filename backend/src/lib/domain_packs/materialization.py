"""Provider-neutral workspace projections and review-row materialization."""

from __future__ import annotations

import copy
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
    CuratableObjectStatus,
    DomainEnvelope,
    FieldRef,
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
from src.schemas.domain_validator import (
    DomainValidationRequest,
    DomainValidatorResultBase,
)
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
    ValidatorBindingMatch,
)
from src.lib.domain_packs.validator_result_classification import (
    lookup_status_for_validator_outcome,
    validator_failure_classification,
)
from src.lib.domain_packs.validator_result_policies import (
    allowed_term_policy_violations,
)
from src.lib.domain_packs.value_presence import missing_resolved_value
from src.lib.openai_agents.config import (
    get_validation_detail_list_limit,
    get_validation_detail_mapping_limit,
    get_validation_detail_string_limit,
)


REVIEW_ROW_PROJECTION_TYPE = "workspace_review_row"
_MISSING = object()
# Env-configurable (defaults unchanged); see config.py getters and .env.example:
#   VALIDATION_DETAIL_STRING_LIMIT, VALIDATION_DETAIL_LIST_LIMIT,
#   VALIDATION_DETAIL_MAPPING_LIMIT.
_VALIDATION_DETAIL_STRING_LIMIT = get_validation_detail_string_limit()
_VALIDATION_DETAIL_LIST_LIMIT = get_validation_detail_list_limit()
_VALIDATION_DETAIL_MAPPING_LIMIT = get_validation_detail_mapping_limit()

VALIDATION_STATUS_RANK: dict[DomainEnvelopeValidationStatus, int] = {
    DomainEnvelopeValidationStatus.RESOLVED: 0,
    DomainEnvelopeValidationStatus.WAIVED: 0,
    DomainEnvelopeValidationStatus.PLANNED: 1,
    DomainEnvelopeValidationStatus.UNDER_DEVELOPMENT: 1,
    DomainEnvelopeValidationStatus.UNRESOLVED: 2,
    DomainEnvelopeValidationStatus.BLOCKED: 3,
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


@dataclass(frozen=True)
class ValidatorResultMaterializationInput:
    """One validator request/result pair ready for envelope materialization."""

    match: ValidatorBindingMatch
    request: DomainValidationRequest
    result: DomainValidatorResultBase


@dataclass(frozen=True)
class ValidatorResultMaterializationResult:
    """Envelope changes produced from package-scoped validator results."""

    envelope: DomainEnvelope
    appended_findings: tuple[ValidationFinding, ...]
    materialized_objects: tuple[CuratableObjectEnvelope, ...]


class DomainEnvelopeReviewRowMaterializer(Protocol):
    """Domain-pack-owned review-row materializer contract."""

    def materialize(
        self,
        envelope: DomainEnvelope,
        *,
        envelope_revision: int,
    ) -> list[DomainEnvelopeReviewRow]:
        """Return review rows regenerated from the supplied envelope revision."""
        ...


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
            workspace_fields = _workspace_fields(
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

            metadata = {
                "semantic_source": "domain_envelope.objects",
                "materializer": type(self).__name__,
                "object_index": object_index,
                "payload_path": f"objects[{object_index}].payload",
                "evidence_record_ids": list(
                    domain_object.evidence_record_ids
                ),
                "metadata_refs": [
                    metadata_ref.model_dump(mode="json")
                    for metadata_ref in domain_object.metadata_refs
                ],
                "workspace_display": dict(display_config),
                **_unavailable_capabilities_metadata(
                    _capabilities_for_object(
                        object_id,
                        unavailable_capabilities=unavailable_capabilities,
                    )
                ),
            }
            if workspace_fields:
                # Only explicit workspace groups should disable the downstream
                # summary-field fallback. An empty list would mean "there was a
                # workspace contract and it intentionally has no editable
                # fields", which is not true for packs/fixtures that provide
                # summary_fields only.
                metadata["workspace_fields"] = [
                    field.model_dump(mode="json")
                    for field in workspace_fields
                ]

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
                    metadata=metadata,
                )
            )

        return _ordered_review_rows(rows)


# Review-row ordering by object role: lead with the units a curator acts on, keep
# supporting reference context after them, and place any remaining roles last. This is
# generic (driven by ``object_role``), so a Title-only validated_reference (e.g. the
# allele paper reference) never dominates the first impression of a review session.
_REVIEW_ROW_ROLE_ORDER: dict[str | None, int] = {
    "curatable_unit": 0,
    "validated_reference": 1,
}
_REVIEW_ROW_ROLE_DEFAULT_ORDER = 2


def _ordered_review_rows(
    rows: Sequence[DomainEnvelopeReviewRow],
) -> list[DomainEnvelopeReviewRow]:
    """Stable-sort review rows by object-role priority, preserving envelope order."""

    return sorted(
        rows,
        key=lambda row: _REVIEW_ROW_ROLE_ORDER.get(
            row.object_role,
            _REVIEW_ROW_ROLE_DEFAULT_ORDER,
        ),
    )


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


def materialize_validator_results_into_envelope(
    envelope: DomainEnvelope,
    metadata: DomainPackMetadata,
    items: Iterable[ValidatorResultMaterializationInput],
    *,
    actor_id: str = "domain_validator_materialization",
    source_envelope_revision: int | None = None,
) -> ValidatorResultMaterializationResult:
    """Apply active validator results as envelope findings and validated refs."""

    if source_envelope_revision is not None and source_envelope_revision < 1:
        raise DomainEnvelopeMaterializationError(
            "source_envelope_revision must be greater than zero"
        )
    if envelope.domain_pack_id != metadata.pack_id:
        raise DomainEnvelopeMaterializationError(
            "Envelope domain_pack_id does not match materializer metadata: "
            f"{envelope.domain_pack_id!r} != {metadata.pack_id!r}"
        )

    object_definitions = {
        definition.object_type: definition
        for definition in metadata.object_definitions
    }
    object_role_key = _object_role_key(metadata)
    working_envelope = envelope
    findings: list[ValidationFinding] = []
    materialized_objects: list[CuratableObjectEnvelope] = []

    for item in items:
        (
            working_envelope,
            patch_problem,
        ) = _patch_target_object_from_resolved_values(
            working_envelope,
            item,
            object_definitions=object_definitions,
            source_envelope_revision=source_envelope_revision,
        )
        if patch_problem is not None:
            findings.append(
                _finding_for_materialization_problem(
                    item,
                    patch_problem,
                    source_envelope_revision=source_envelope_revision,
                )
            )
            continue

        new_objects, materialization_problem = _materialized_objects_for_result(
            working_envelope,
            item,
            object_definitions=object_definitions,
            object_role_key=object_role_key,
            source_envelope_revision=source_envelope_revision,
        )
        if materialization_problem is None:
            working_envelope, linked_objects = _append_materialized_objects(
                working_envelope,
                item,
                new_objects,
            )
            materialized_objects.extend(linked_objects)
            validator_finding = _finding_for_validator_result(
                item,
                source_envelope_revision=source_envelope_revision,
            )
            findings.append(validator_finding)
            findings.extend(
                _field_findings_for_expected_result_fields(
                    working_envelope,
                    item,
                    validator_finding=validator_finding,
                    object_definitions=object_definitions,
                    materialized_objects=new_objects,
                    source_envelope_revision=source_envelope_revision,
                )
            )
            continue

        findings.append(
            _finding_for_materialization_problem(
                item,
                materialization_problem,
                source_envelope_revision=source_envelope_revision,
            )
        )

    from .validation_findings import append_validation_findings_to_envelope

    working_envelope, appended_findings = append_validation_findings_to_envelope(
        working_envelope,
        findings,
        actor_id=actor_id,
    )
    return ValidatorResultMaterializationResult(
        envelope=working_envelope,
        appended_findings=appended_findings,
        materialized_objects=tuple(materialized_objects),
    )


def _patch_target_object_from_resolved_values(
    envelope: DomainEnvelope,
    item: ValidatorResultMaterializationInput,
    *,
    object_definitions: Mapping[str, DomainPackObjectDefinition],
    source_envelope_revision: int | None,
) -> tuple[DomainEnvelope, str | None]:
    """Patch validator-owned scalar results onto the matched envelope object."""

    result = item.result
    matched_target = item.match.object_envelope
    if result.status != "resolved" or matched_target is None or not result.resolved_values:
        return envelope, None
    policy_violations = allowed_term_policy_violations(result, request=item.request)
    if policy_violations:
        return envelope, "; ".join(
            violation.message for violation in policy_violations
        )

    target = _current_object_for_match(envelope, matched_target)
    if target is None:
        return envelope, None

    object_definition = item.match.object_definition
    if object_definition is None:
        object_definition = object_definitions.get(target.object_type)
    if object_definition is None:
        return envelope, None

    declared_fields = {field.field_path: field for field in object_definition.fields}
    payload = copy.deepcopy(target.payload)
    changed = False

    for result_field, raw_field_path in item.request.expected_result_fields.items():
        if not isinstance(raw_field_path, str) or not raw_field_path.strip():
            return envelope, (
                "expected_result_fields values must be non-empty field path strings"
            )
        resolved_value = result.resolved_values.get(result_field)
        if missing_resolved_value(resolved_value):
            continue
        materialized_field_path = _materialized_field_path(
            raw_field_path,
            declared_fields=declared_fields,
        )
        if materialized_field_path is None:
            continue
        current_value = _payload_value(payload, materialized_field_path)
        if current_value is not _MISSING and current_value == resolved_value:
            continue
        _set_payload_value(payload, materialized_field_path, resolved_value)
        _propagate_materialized_mirror_paths(
            payload,
            materialized_field_path,
            resolved_value,
            declared_fields=declared_fields,
        )
        changed = True
    if not changed:
        return envelope, None

    original_values = _original_materialized_values(
        target.payload,
        item.request.expected_result_fields.values(),
        declared_fields=declared_fields,
    )

    metadata = dict(target.metadata)
    existing_patch_metadata = metadata.get("validator_resolved_value_materialization")
    patch_events: list[dict[str, Any]]
    if isinstance(existing_patch_metadata, list):
        patch_events = list(existing_patch_metadata)
    else:
        patch_events = []
    patch_events.append(
        {
            "source": "domain_validator_resolved_values",
            "request_id": result.request_id,
            "validator_binding_id": result.validator_binding_id,
            "validator_agent": result.validator_agent.model_dump(mode="json"),
            "selected_inputs": dict(item.request.selected_inputs),
            "input_selectors": dict(item.request.input_selectors),
            "original_values": original_values,
            **(
                {"source_envelope_revision": source_envelope_revision}
                if source_envelope_revision is not None
                else {}
            ),
        }
    )
    metadata["validator_resolved_value_materialization"] = patch_events

    patched_target = target.model_copy(
        update={
            "payload": payload,
            "status": CuratableObjectStatus.VALIDATED,
            "metadata": metadata,
        }
    )
    objects = [
        patched_target if _same_object_identity(candidate, target) else candidate
        for candidate in envelope.objects
    ]
    return envelope.model_copy(update={"objects": objects}), None


def _original_materialized_values(
    payload: Mapping[str, Any],
    raw_field_paths: Iterable[Any],
    *,
    declared_fields: Mapping[str, DomainPackFieldDefinition],
) -> dict[str, Any]:
    """Return existing target payload values for fields a validator may patch."""

    original_values: dict[str, Any] = {}
    for raw_field_path in raw_field_paths:
        if not isinstance(raw_field_path, str) or not raw_field_path.strip():
            continue
        materialized_field_path = _materialized_field_path(
            raw_field_path,
            declared_fields=declared_fields,
        )
        if materialized_field_path is None:
            continue
        original_value = _payload_value(payload, materialized_field_path)
        if original_value is not _MISSING:
            original_values[materialized_field_path] = original_value
    return original_values


def _current_object_for_match(
    envelope: DomainEnvelope,
    target: CuratableObjectEnvelope,
) -> CuratableObjectEnvelope | None:
    """Return the current envelope object corresponding to a match target."""

    for candidate in envelope.objects:
        if _same_object_identity(candidate, target):
            return candidate
    return None


def _same_object_identity(
    candidate: CuratableObjectEnvelope,
    target: CuratableObjectEnvelope,
) -> bool:
    """Return whether two envelope objects identify the same target object."""

    if target.object_id is not None:
        return candidate.object_id == target.object_id
    if target.pending_ref_id is not None:
        return (
            candidate.pending_ref_id == target.pending_ref_id
            and candidate.object_type == target.object_type
        )
    return candidate is target


def _materialized_objects_for_result(
    envelope: DomainEnvelope,
    item: ValidatorResultMaterializationInput,
    *,
    object_definitions: Mapping[str, DomainPackObjectDefinition],
    object_role_key: str,
    source_envelope_revision: int | None,
) -> tuple[list[CuratableObjectEnvelope], str | None]:
    result = item.result
    if result.status != "resolved":
        return [], None
    if not result.resolved_objects:
        return [], None

    resolved_objects = [
        raw_object
        for raw_object in result.resolved_objects
        if _looks_like_materializable_object(raw_object)
    ]
    if not resolved_objects:
        return [], None

    materialized_objects: list[CuratableObjectEnvelope] = []
    for object_index, raw_object in enumerate(resolved_objects):
        if not isinstance(raw_object, Mapping):
            return [], f"resolved_objects[{object_index}] must be an object"

        object_type = _optional_string(raw_object.get("object_type"))
        canonical_id = _optional_string(raw_object.get("canonical_id"))
        raw_payload = raw_object.get("payload")
        if object_type is None:
            return [], f"resolved_objects[{object_index}].object_type is required"
        if canonical_id is None:
            return [], f"resolved_objects[{object_index}].canonical_id is required"
        if not isinstance(raw_payload, Mapping):
            return [], f"resolved_objects[{object_index}].payload must be an object"

        object_definition = object_definitions.get(object_type)
        if object_definition is None:
            return [], f"resolved object type {object_type!r} is not declared"
        object_role = _definition_object_role(
            object_definition,
            object_role_key=object_role_key,
        )
        if object_role != "validated_reference":
            return (
                [],
                f"resolved object type {object_type!r} is not a validated_reference",
            )

        payload, problem = _validated_reference_payload(
            item,
            raw_payload,
            object_definition=object_definition,
        )
        if problem is not None:
            return [], problem

        object_id = _validated_reference_object_id(object_type, canonical_id)
        existing = _find_existing_object(envelope, object_id=object_id)
        if existing is not None:
            if (
                existing.object_type != object_type
                or dict(existing.payload) != payload
            ):
                return (
                    [],
                    "resolved object conflicts with an existing materialized "
                    f"object_id {object_id!r}",
                )
            materialized_objects.append(existing)
            continue

        materialized_objects.append(
            CuratableObjectEnvelope(
                object_type=object_type,
                object_id=object_id,
                status=CuratableObjectStatus.VALIDATED,
                schema_ref=object_definition.schema_ref,
                model_ref=object_definition.model_ref,
                payload=payload,
                definition_state=object_definition.definition_state,
                definition_notes=list(object_definition.definition_notes),
                metadata={
                    object_role_key: "validated_reference",
                    "validation_state": "validated",
                    "validator_materialization": {
                        "source": "domain_validator_result",
                        "request_id": result.request_id,
                        "validator_binding_id": result.validator_binding_id,
                        "validator_agent": result.validator_agent.model_dump(
                            mode="json"
                        ),
                        "canonical_id": canonical_id,
                        **(
                            {"source_envelope_revision": source_envelope_revision}
                            if source_envelope_revision is not None
                            else {}
                        ),
                    },
                },
            )
        )

    return materialized_objects, None


def _looks_like_materializable_object(raw_object: Any) -> bool:
    """Return whether a resolved object is an envelope materialization payload.

    Materialization payloads are identified by the materialization-specific keys
    ``canonical_id``/``payload`` -- NOT by ``object_type`` alone. Validators also
    report raw lookup hits in ``resolved_objects`` as diagnostic context (e.g. the
    gene validator's ``{object_type, resolved_id, provider_data, projection_type}``
    projection); those carry ``object_type`` but no ``canonical_id``/``payload`` and
    must be treated as diagnostic context and skipped, not force-materialized into a
    spurious ``validator_materialization_invalid`` finding. A genuine
    validated_reference payload always carries ``canonical_id`` (and ``payload``), so
    it still flows through the full role/structure validation below.
    """

    if not isinstance(raw_object, Mapping):
        return True
    return any(
        key in raw_object
        for key in ("canonical_id", "payload")
    )


def _validated_reference_payload(
    item: ValidatorResultMaterializationInput,
    raw_payload: Mapping[str, Any],
    *,
    object_definition: DomainPackObjectDefinition,
) -> tuple[dict[str, Any], str | None]:
    declared_fields = {field.field_path: field for field in object_definition.fields}
    payload: dict[str, Any] = {}

    for field_path in declared_fields:
        value = _payload_value(raw_payload, field_path)
        if value is not _MISSING:
            _set_payload_value(payload, field_path, value)

    for result_field, raw_field_path in item.request.expected_result_fields.items():
        if not isinstance(raw_field_path, str) or not raw_field_path.strip():
            return {}, (
                "expected_result_fields values must be non-empty field path strings"
            )
        resolved_value = item.result.resolved_values.get(result_field)
        if missing_resolved_value(resolved_value):
            continue
        materialized_field_path = _materialized_field_path(
            raw_field_path,
            declared_fields=declared_fields,
        )
        if materialized_field_path is None:
            continue
        _set_payload_value(payload, materialized_field_path, resolved_value)
        _propagate_materialized_mirror_paths(
            payload,
            materialized_field_path,
            resolved_value,
            declared_fields=declared_fields,
        )

    missing_required_fields = [
        field.field_path
        for field in object_definition.fields
        if field.required and _payload_value(payload, field.field_path) is _MISSING
    ]
    if missing_required_fields:
        return {}, (
            "resolved object payload is missing required field(s): "
            + ", ".join(missing_required_fields)
        )

    if not payload:
        return {}, (
            f"resolved object type {object_definition.object_type!r} did not include "
            "any fields permitted by the binding schema"
        )
    return payload, None


def _materialized_field_path(
    raw_field_path: str,
    *,
    declared_fields: Mapping[str, DomainPackFieldDefinition],
) -> str | None:
    field_path = raw_field_path.strip()
    if field_path in declared_fields:
        return field_path
    indexed_base = _multivalued_indexed_base(field_path, declared_fields)
    if indexed_base is not None:
        return field_path
    if "." not in field_path:
        return None
    _, suffix = field_path.split(".", 1)
    if suffix in declared_fields:
        return suffix
    if _multivalued_indexed_base(suffix, declared_fields) is not None:
        return suffix
    return None


def _multivalued_indexed_base(
    field_path: str,
    declared_fields: Mapping[str, DomainPackFieldDefinition],
) -> str | None:
    """Return the de-indexed declared field for an indexed multivalued write path.

    Lets validator write-back target a per-element slot while keeping the legacy
    ``field[0]`` literal convention out of scope. A single-level slot like
    ``evidence_code_curies[2]`` resolves to its multivalued base, and a nested slot like
    ``condition_relations[0].conditions[1].condition_class.curie`` resolves to its bare
    declared leaf — but only when every indexed segment corresponds to a declared
    ``multivalued: true`` prefix, so only fields that opted in accept an indexed write
    path here.
    """

    try:
        parts = parse_field_path(field_path)
    except ValueError:
        return None
    if not any(isinstance(part, int) for part in parts):
        return None

    # Validate each indexed segment names a declared multivalued prefix, and build the
    # de-indexed dotted base by dropping the list indices.
    bare_parts: list[str] = []
    prefix_parts: list[str] = []
    for part in parts:
        if isinstance(part, int):
            prefix = ".".join(prefix_parts)
            prefix_definition = declared_fields.get(prefix)
            if prefix_definition is None or not prefix_definition.multivalued:
                return None
            continue
        bare_parts.append(part)
        prefix_parts.append(part)
    bare_field_path = ".".join(bare_parts)
    if bare_field_path not in declared_fields:
        return None
    return bare_field_path


def _propagate_materialized_mirror_paths(
    payload: dict[str, Any],
    materialized_field_path: str,
    resolved_value: Any,
    *,
    declared_fields: Mapping[str, DomainPackFieldDefinition],
) -> None:
    """Copy a resolved value into the field's declared ``materializes_to_field_paths`` mirrors.

    The gene-expression domain pack declares that
    ``expression_annotation_subject.primary_external_id`` materializes to
    ``expression_experiment.entity_assayed.primary_external_id`` (and the gene_symbol pair), so a
    resolved subject gene must also land on ``entity_assayed`` to satisfy the LinkML
    "entity_assayed must match expression_annotation_subject" contract. The mirror targets are
    declared metadata, so this is domain-pack-driven, not gene-expression-specific code.
    """
    field_def = declared_fields.get(materialized_field_path)
    if field_def is None:
        return
    mirror_paths = field_def.metadata.get("materializes_to_field_paths")
    if not isinstance(mirror_paths, list):
        return
    for mirror_raw in mirror_paths:
        if not isinstance(mirror_raw, str) or not mirror_raw.strip():
            continue
        mirror_path = (
            _materialized_field_path(mirror_raw, declared_fields=declared_fields)
            or mirror_raw.strip()
        )
        if _payload_value(payload, mirror_path) != resolved_value:
            _set_payload_value(payload, mirror_path, resolved_value)


def _append_materialized_objects(
    envelope: DomainEnvelope,
    item: ValidatorResultMaterializationInput,
    new_objects: Sequence[CuratableObjectEnvelope],
) -> tuple[DomainEnvelope, tuple[CuratableObjectEnvelope, ...]]:
    if not new_objects:
        return envelope, ()

    existing_object_ids = {
        domain_object.object_id
        for domain_object in envelope.objects
        if domain_object.object_id is not None
    }
    objects = list(envelope.objects)
    appended: list[CuratableObjectEnvelope] = []
    for new_object in new_objects:
        if (
            new_object.object_id is not None
            and new_object.object_id not in existing_object_ids
        ):
            objects.append(new_object)
            existing_object_ids.add(new_object.object_id)
            appended.append(new_object)

    if item.match.object_envelope is not None:
        objects = _objects_with_target_refs(
            objects,
            target=item.match.object_envelope,
            referenced_objects=new_objects,
        )

    return envelope.model_copy(update={"objects": objects}), tuple(appended)


def _objects_with_target_refs(
    objects: Sequence[CuratableObjectEnvelope],
    *,
    target: CuratableObjectEnvelope,
    referenced_objects: Sequence[CuratableObjectEnvelope],
) -> list[CuratableObjectEnvelope]:
    target_keys = set(target.ref_keys())
    referenced_refs = [
        referenced_object.to_object_ref()
        for referenced_object in referenced_objects
        if referenced_object.object_type != target.object_type
    ]
    if not referenced_refs:
        return list(objects)

    updated_objects: list[CuratableObjectEnvelope] = []
    for domain_object in objects:
        if not target_keys.intersection(domain_object.ref_keys()):
            updated_objects.append(domain_object)
            continue
        existing_ref_keys = {ref.ref_key() for ref in domain_object.object_refs}
        next_refs = list(domain_object.object_refs)
        for object_ref in referenced_refs:
            if object_ref.ref_key() not in existing_ref_keys:
                next_refs.append(object_ref)
                existing_ref_keys.add(object_ref.ref_key())
        updated_objects.append(domain_object.model_copy(update={"object_refs": next_refs}))
    return updated_objects


def _finding_for_materialization_problem(
    item: ValidatorResultMaterializationInput,
    problem: str,
    *,
    source_envelope_revision: int | None,
) -> ValidationFinding:
    result = item.result
    details = _validator_result_finding_details(
        item,
        source_envelope_revision=source_envelope_revision,
    )
    details["failure_classification"] = "invalid_materialization_input"
    details["materialization_error"] = problem
    return ValidationFinding(
        severity=(
            ValidationFindingSeverity.BLOCKER
            if item.match.binding.blocking
            else ValidationFindingSeverity.WARNING
        ),
        status=ValidationFindingStatus.OPEN,
        code="domain_pack.validator_materialization_invalid",
        message=(
            result.curator_message
            or f"Validator result could not be materialized: {problem}"
        ),
        object_ref=_match_object_ref(item.match),
        field_ref=_match_field_ref(item.match),
        details={key: value for key, value in details.items() if value not in ([], {})},
    )


def _finding_for_validator_result(
    item: ValidatorResultMaterializationInput,
    *,
    source_envelope_revision: int | None,
) -> ValidationFinding:
    result = item.result
    resolved = result.status == "resolved"
    # A validator that could not RUN its lookup (e.g. a flaky validator tool call) records a
    # lookup attempt with outcome "error". That is distinct from a validator that ran and found
    # no match ("unresolved"): surface it as a separate, more prominent validator_error finding so
    # curators see it and we can grep legitimate validator failures in the logs. It is NOT fatal to
    # the chat turn — the extraction persists and the flagged field awaits curator review.
    is_validator_error = not resolved and any(
        attempt.outcome == "error" for attempt in result.lookup_attempts
    )
    details = _validator_result_finding_details(
        item,
        source_envelope_revision=source_envelope_revision,
    )
    if not resolved:
        details["failure_classification"] = validator_failure_classification(
            result,
            error_type=DomainEnvelopeMaterializationError,
        )

    if resolved:
        severity = ValidationFindingSeverity.INFO
        finding_status = ValidationFindingStatus.RESOLVED
        code = "domain_pack.validator_resolved"
        outcome_label = "resolved"
    elif is_validator_error:
        severity = (
            ValidationFindingSeverity.BLOCKER
            if item.match.binding.blocking
            else ValidationFindingSeverity.ERROR
        )
        finding_status = ValidationFindingStatus.OPEN
        code = "domain_pack.validator_error"
        outcome_label = "could not be run for"
    else:
        severity = (
            ValidationFindingSeverity.BLOCKER
            if item.match.binding.blocking
            else ValidationFindingSeverity.WARNING
        )
        finding_status = ValidationFindingStatus.OPEN
        code = "domain_pack.validator_unresolved"
        outcome_label = "did not resolve"

    return ValidationFinding(
        severity=severity,
        status=finding_status,
        code=code,
        message=(
            result.curator_message
            or result.explanation
            or (
                f"Validator binding '{item.request.validator_binding_id}' "
                f"{outcome_label} the target."
            )
        ),
        object_ref=_match_object_ref(item.match),
        field_ref=_match_field_ref(item.match),
        details={key: value for key, value in details.items() if value not in ([], {})},
    )


def _field_findings_for_expected_result_fields(
    envelope: DomainEnvelope,
    item: ValidatorResultMaterializationInput,
    *,
    validator_finding: ValidationFinding,
    object_definitions: Mapping[str, DomainPackObjectDefinition],
    materialized_objects: Sequence[CuratableObjectEnvelope],
    source_envelope_revision: int | None,
) -> list[ValidationFinding]:
    if not item.request.expected_result_fields:
        return []

    targets = _expected_result_field_targets(
        envelope,
        item,
        object_definitions=object_definitions,
        materialized_objects=materialized_objects,
    )
    if not targets:
        return []

    findings: list[ValidationFinding] = []
    seen_targets: set[tuple[tuple[str, str], str, str]] = set()
    unmapped_fields: list[tuple[str, str]] = []
    parent_field_ref = validator_finding.field_ref
    for result_field, raw_field_path in item.request.expected_result_fields.items():
        if not isinstance(raw_field_path, str) or not raw_field_path.strip():
            continue
        mapped_result_field = False
        for target in targets:
            materialized_paths = _materialized_field_paths(
                raw_field_path,
                declared_fields=target.declared_fields,
            )
            if materialized_paths:
                mapped_result_field = True
            for materialized_field_path in materialized_paths:
                field_ref = FieldRef(
                    object_ref=target.domain_object.to_object_ref(),
                    field_path=materialized_field_path,
                )
                if (
                    parent_field_ref is not None
                    and parent_field_ref.object_ref.ref_key()
                    == field_ref.object_ref.ref_key()
                    and parent_field_ref.field_path == field_ref.field_path
                ):
                    continue
                target_key = (
                    field_ref.object_ref.ref_key(),
                    field_ref.field_path,
                    result_field,
                )
                if target_key in seen_targets:
                    continue
                seen_targets.add(target_key)
                findings.append(
                    _field_finding_for_expected_result_field(
                        item,
                        validator_finding=validator_finding,
                        field_ref=field_ref,
                        result_field=result_field,
                        materialized_field_path=materialized_field_path,
                        source_envelope_revision=source_envelope_revision,
                    )
                )
        if not mapped_result_field:
            unmapped_fields.append((result_field, raw_field_path))
    if unmapped_fields and item.result.status == "resolved":
        findings.append(
            _finding_for_unmapped_expected_result_fields(
                item,
                validator_finding=validator_finding,
                unmapped_fields=unmapped_fields,
                source_envelope_revision=source_envelope_revision,
            )
        )
    return findings


@dataclass(frozen=True)
class _ExpectedResultFieldTarget:
    domain_object: CuratableObjectEnvelope
    declared_fields: Mapping[str, DomainPackFieldDefinition]


def _expected_result_field_targets(
    envelope: DomainEnvelope,
    item: ValidatorResultMaterializationInput,
    *,
    object_definitions: Mapping[str, DomainPackObjectDefinition],
    materialized_objects: Sequence[CuratableObjectEnvelope],
) -> tuple[_ExpectedResultFieldTarget, ...]:
    targets: list[_ExpectedResultFieldTarget] = []
    seen_refs: set[tuple[str, str]] = set()

    def add_target(domain_object: CuratableObjectEnvelope) -> None:
        ref_key = domain_object.to_object_ref().ref_key()
        if ref_key in seen_refs:
            return
        object_definition = object_definitions.get(domain_object.object_type)
        if object_definition is None:
            return
        declared_fields = {field.field_path: field for field in object_definition.fields}
        if not declared_fields:
            return
        seen_refs.add(ref_key)
        targets.append(
            _ExpectedResultFieldTarget(
                domain_object=domain_object,
                declared_fields=declared_fields,
            )
        )

    if item.match.object_envelope is not None:
        current_target = _current_object_for_match(envelope, item.match.object_envelope)
        if current_target is not None:
            add_target(current_target)

    for materialized_object in materialized_objects:
        add_target(materialized_object)

    return tuple(targets)


def _materialized_field_paths(
    raw_field_path: str,
    *,
    declared_fields: Mapping[str, DomainPackFieldDefinition],
) -> tuple[str, ...]:
    materialized_field_path = _materialized_field_path(
        raw_field_path,
        declared_fields=declared_fields,
    )
    if materialized_field_path is None:
        return ()

    field_paths = [materialized_field_path]
    field_def = declared_fields.get(materialized_field_path)
    if field_def is not None:
        mirror_paths = field_def.metadata.get("materializes_to_field_paths")
        if isinstance(mirror_paths, list):
            for mirror_raw in mirror_paths:
                if not isinstance(mirror_raw, str) or not mirror_raw.strip():
                    continue
                mirror_path = _materialized_field_path(
                    mirror_raw,
                    declared_fields=declared_fields,
                )
                if mirror_path is not None and mirror_path not in field_paths:
                    field_paths.append(mirror_path)
    return tuple(field_paths)


def _field_finding_for_expected_result_field(
    item: ValidatorResultMaterializationInput,
    *,
    validator_finding: ValidationFinding,
    field_ref: FieldRef,
    result_field: str,
    materialized_field_path: str,
    source_envelope_revision: int | None,
) -> ValidationFinding:
    result = item.result
    resolved_value = result.resolved_values.get(result_field)
    result_field_missing = result_field in result.missing_expected_fields
    if result.status == "resolved" and not (
        result_field_missing or missing_resolved_value(resolved_value)
    ):
        severity = ValidationFindingSeverity.INFO
        status = ValidationFindingStatus.RESOLVED
        code = validator_finding.code
        message = validator_finding.message
        extra_details: dict[str, Any] = {}
    elif result.status == "resolved":
        severity = (
            ValidationFindingSeverity.BLOCKER
            if item.match.binding.blocking
            else ValidationFindingSeverity.WARNING
        )
        status = ValidationFindingStatus.OPEN
        code = "domain_pack.validator_expected_field_missing"
        message = (
            result.curator_message
            or f"Validator binding '{item.request.validator_binding_id}' did not "
            f"resolve expected field '{result_field}'."
        )
        extra_details = {
            "failure_classification": "missing_expected_result_field",
            "missing_expected_fields": list(
                dict.fromkeys([*result.missing_expected_fields, result_field])
            ),
        }
    else:
        severity = validator_finding.severity
        status = validator_finding.status
        code = validator_finding.code
        message = validator_finding.message
        extra_details = {}

    details = copy.deepcopy(validator_finding.details)
    validation_metadata = details.get("validation_metadata")
    if not isinstance(validation_metadata, dict):
        validation_metadata = {}
    validation_metadata.update(
        {
            "parent_request_id": result.request_id,
            "materialized_result_field": result_field,
            "materialized_field_path": materialized_field_path,
            "generated_from_expected_result_field": True,
            **(
                {"source_envelope_revision": source_envelope_revision}
                if source_envelope_revision is not None
                else {}
            ),
        }
    )
    details["validation_metadata"] = validation_metadata
    details.update(extra_details)
    return ValidationFinding(
        severity=severity,
        status=status,
        code=code,
        message=message,
        field_ref=field_ref,
        details={key: value for key, value in details.items() if value not in ([], {})},
    )


def _finding_for_unmapped_expected_result_fields(
    item: ValidatorResultMaterializationInput,
    *,
    validator_finding: ValidationFinding,
    unmapped_fields: Sequence[tuple[str, str]],
    source_envelope_revision: int | None,
) -> ValidationFinding:
    details = copy.deepcopy(validator_finding.details)
    validation_metadata = details.get("validation_metadata")
    if not isinstance(validation_metadata, dict):
        validation_metadata = {}
    validation_metadata.update(
        {
            "parent_request_id": item.result.request_id,
            "generated_from_expected_result_field": True,
            "unmapped_expected_result_fields": [
                {"result_field": result_field, "field_path": field_path}
                for result_field, field_path in unmapped_fields
            ],
            **(
                {"source_envelope_revision": source_envelope_revision}
                if source_envelope_revision is not None
                else {}
            ),
        }
    )
    details["validation_metadata"] = validation_metadata
    details["failure_classification"] = "unmapped_expected_result_field"
    details["materialization_warning"] = (
        "Validator expected-result field path(s) could not be mapped to declared "
        "domain-pack fields."
    )
    object_ref = validator_finding.object_ref
    if object_ref is None and validator_finding.field_ref is not None:
        object_ref = validator_finding.field_ref.object_ref
    return ValidationFinding(
        severity=ValidationFindingSeverity.WARNING,
        status=ValidationFindingStatus.OPEN,
        code="domain_pack.validator_expected_field_unmapped",
        message=(
            "Validator result includes expected field path(s) that are not declared "
            "for review: "
            + ", ".join(field_path for _, field_path in unmapped_fields)
        ),
        object_ref=object_ref,
        details={key: value for key, value in details.items() if value not in ([], {})},
    )


def _validator_result_finding_details(
    item: ValidatorResultMaterializationInput,
    *,
    source_envelope_revision: int | None,
) -> dict[str, Any]:
    details = {
        "validation_metadata": {
            **item.match.binding.identity_details(),
            "target": item.match.target_details(),
            **(
                {"source_envelope_revision": source_envelope_revision}
                if source_envelope_revision is not None
                else {}
            ),
        },
        "validation_request": _validation_request_finding_payload(item.request),
        "validation_result": _validation_result_finding_payload(item.result),
        "lookup_attempts": _lookup_attempt_details(item),
        "candidate_matches": _candidate_matches(item.result),
    }
    if item.result.missing_expected_fields:
        details["missing_expected_fields"] = list(item.result.missing_expected_fields)
    if item.result.curator_message is not None:
        details["curator_message"] = item.result.curator_message
    return details


def _lookup_attempt_details(
    item: ValidatorResultMaterializationInput,
) -> list[dict[str, Any]]:
    attempts = []
    for attempt in item.result.lookup_attempts:
        payload = attempt.model_dump(mode="json", exclude_none=True)
        lookup_status = lookup_status_for_validator_outcome(
            payload.get("outcome"),
            error_type=DomainEnvelopeMaterializationError,
        )
        attempts.append(
            {
                "source": {
                    "validator_binding_id": item.request.validator_binding_id,
                    "validator_agent": item.request.validator_agent.model_dump(
                        mode="json"
                    ),
                },
                "attempted_query": {
                    "request_id": item.request.request_id,
                    "input_fields": _compact_validation_detail_value(
                        dict(item.request.selected_inputs)
                    ),
                    "provider_query": _compact_validation_detail_value(
                        payload.get("query", {})
                    ),
                },
                "lookup_status": lookup_status,
                "candidate_count": payload["result_count"],
                "resolved_id": _resolved_id(item.result),
                "resolved_label": _resolved_label(item.result),
                "explanation": payload.get("message") or item.result.explanation,
                "provider": payload.get("provider"),
                "method": payload.get("method"),
            }
        )
    return attempts


def _candidate_matches(result: DomainValidatorResultBase) -> list[dict[str, Any]]:
    return [
        _compact_candidate_payload(candidate.model_dump(mode="json", exclude_none=True))
        for candidate in result.candidates
    ]


def _validation_request_finding_payload(
    request: DomainValidationRequest,
) -> dict[str, Any]:
    payload = request.model_dump(mode="json", exclude_none=True)
    evidence = payload.pop("evidence", [])
    if isinstance(evidence, list):
        payload["evidence_count"] = len(evidence)
        evidence_record_ids = [
            record_id
            for record in evidence
            if isinstance(record, Mapping)
            and isinstance(record_id := record.get("evidence_record_id"), str)
            and record_id
        ]
        if evidence_record_ids:
            payload["evidence_record_ids"] = evidence_record_ids[
                :_VALIDATION_DETAIL_LIST_LIMIT
            ]
    return _compact_validation_detail_value(payload)


def _validation_result_finding_payload(
    result: DomainValidatorResultBase,
) -> dict[str, Any]:
    payload = result.model_dump(mode="json", exclude_none=True)
    compact: dict[str, Any] = {
        key: payload[key]
        for key in (
            "status",
            "request_id",
            "validator_binding_id",
            "validator_agent",
            "target",
            "resolved_values",
            "missing_expected_fields",
            "curator_message",
            "explanation",
        )
        if key in payload
    }
    resolved_objects = payload.get("resolved_objects")
    if isinstance(resolved_objects, list):
        compact["resolved_objects"] = [
            _compact_resolved_object_payload(item)
            for item in resolved_objects[:_VALIDATION_DETAIL_LIST_LIMIT]
            if isinstance(item, Mapping)
        ]
        if len(resolved_objects) > _VALIDATION_DETAIL_LIST_LIMIT:
            compact["resolved_object_count"] = len(resolved_objects)
    lookup_attempts = payload.get("lookup_attempts")
    if isinstance(lookup_attempts, list):
        compact["lookup_attempt_count"] = len(lookup_attempts)
    candidates = payload.get("candidates")
    if isinstance(candidates, list):
        compact["candidate_count"] = len(candidates)
    return _compact_validation_detail_value(compact)


def _compact_candidate_payload(candidate: Mapping[str, Any]) -> dict[str, Any]:
    compact = {
        key: candidate[key]
        for key in ("value", "label", "object_type", "score", "matched_fields")
        if key in candidate
    }
    details = candidate.get("details")
    if isinstance(details, Mapping):
        compact["details"] = _compact_validation_detail_value(details)
    return _compact_validation_detail_value(compact)


def _compact_resolved_object_payload(item: Mapping[str, Any]) -> dict[str, Any]:
    compact = {
        key: item[key]
        for key in ("object_type", "canonical_id", "label", "symbol", "name")
        if key in item
    }
    payload = item.get("payload")
    if isinstance(payload, Mapping):
        compact["payload"] = _compact_validation_detail_value(payload)
    return _compact_validation_detail_value(compact)


def _compact_validation_detail_value(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) <= _VALIDATION_DETAIL_STRING_LIMIT:
            return value
        return value[:_VALIDATION_DETAIL_STRING_LIMIT] + (
            f"... [truncated {len(value) - _VALIDATION_DETAIL_STRING_LIMIT} chars]"
        )
    if isinstance(value, Mapping):
        compact: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _VALIDATION_DETAIL_MAPPING_LIMIT:
                compact["__truncated_keys__"] = len(value) - _VALIDATION_DETAIL_MAPPING_LIMIT
                break
            compact[str(key)] = _compact_validation_detail_value(item)
        return compact
    if (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
    ):
        compact_list = [
            _compact_validation_detail_value(item)
            for item in value[:_VALIDATION_DETAIL_LIST_LIMIT]
        ]
        if len(value) > _VALIDATION_DETAIL_LIST_LIMIT:
            compact_list.append(
                {"__truncated_items__": len(value) - _VALIDATION_DETAIL_LIST_LIMIT}
            )
        return compact_list
    return value


def _resolved_id(result: DomainValidatorResultBase) -> str | None:
    for value in result.resolved_values.values():
        if isinstance(value, str) and value.strip():
            return value
    for resolved_object in result.resolved_objects:
        value = resolved_object.get("canonical_id")
        if isinstance(value, str) and value.strip():
            return value
    return None


def _resolved_label(result: DomainValidatorResultBase) -> str | None:
    for key in ("label", "symbol", "name"):
        value = result.resolved_values.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _match_object_ref(match: ValidatorBindingMatch) -> ObjectRef | None:
    if match.object_envelope is None or match.field_definition is not None:
        return None
    return match.object_envelope.to_object_ref()


def _match_field_ref(match: ValidatorBindingMatch) -> FieldRef | None:
    if (
        match.object_envelope is None
        or match.field_definition is None
        or match.field_path is None
    ):
        return None
    return FieldRef(
        object_ref=match.object_envelope.to_object_ref(),
        # ``match.field_path`` carries the element index for a fanned-out multivalued
        # match (``field[i]``) so per-element findings point at the right element (D6);
        # it equals the bare path for scalar/legacy matches.
        field_path=match.field_path,
    )


def _validated_reference_object_id(object_type: str, canonical_id: str) -> str:
    digest = sha256(
        json.dumps(
            {"object_type": object_type, "canonical_id": canonical_id},
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return f"validated-reference:{object_type}:{digest[:24]}"


def _definition_object_role(
    object_definition: DomainPackObjectDefinition,
    *,
    object_role_key: str,
) -> str | None:
    role = object_definition.metadata.get(object_role_key)
    return role.strip() if isinstance(role, str) and role.strip() else None


def _find_existing_object(
    envelope: DomainEnvelope,
    *,
    object_id: str,
) -> CuratableObjectEnvelope | None:
    for domain_object in envelope.objects:
        if domain_object.object_id == object_id:
            return domain_object
    return None


def _set_payload_value(payload: dict[str, Any], field_path: str, value: Any) -> None:
    parts = parse_field_path(field_path)
    current: Any = payload
    for index, part in enumerate(parts[:-1]):
        next_part = parts[index + 1]
        if isinstance(part, str):
            if not isinstance(current, dict):
                raise DomainEnvelopeMaterializationError(
                    f"Cannot materialize nested value into non-object path {field_path!r}"
                )
            container = _container_for_next_part(
                current.get(part), next_part, field_path
            )
            current[part] = container
            current = container
            continue
        # ``part`` is a list index: extend/descend into the staged list.
        current = _list_slot_container(current, part, next_part, field_path)
    leaf = parts[-1]
    if isinstance(leaf, str):
        if not isinstance(current, dict):
            raise DomainEnvelopeMaterializationError(
                f"Cannot materialize nested value into non-object path {field_path!r}"
            )
        current[leaf] = value
        return
    if not isinstance(current, list):
        raise DomainEnvelopeMaterializationError(
            f"Cannot materialize list index into non-list path {field_path!r}"
        )
    _extend_list_to_index(current, leaf)
    current[leaf] = value


def _container_for_next_part(
    existing: Any, next_part: str | int, field_path: str
) -> Any:
    """Return a container at a dict key suited to the following path part.

    A missing/``None`` slot is created as the right container type. An existing value of
    the wrong shape (e.g. a scalar where a list is required) raises rather than silently
    discarding staged curator data — preserving the original writer's safety guarantee.
    """

    if isinstance(next_part, int):
        if existing is None:
            return []
        if not isinstance(existing, list):
            raise DomainEnvelopeMaterializationError(
                f"Cannot materialize list index into non-list path {field_path!r}"
            )
        return existing
    if existing is None:
        return {}
    if not isinstance(existing, dict):
        raise DomainEnvelopeMaterializationError(
            f"Cannot materialize nested value into non-object path {field_path!r}"
        )
    return existing


def _list_slot_container(
    current: Any,
    index: int,
    next_part: str | int,
    field_path: str,
) -> Any:
    """Descend into ``current[index]``, extending the list and slotting a container."""

    if not isinstance(current, list):
        raise DomainEnvelopeMaterializationError(
            f"Cannot materialize list index into non-list path {field_path!r}"
        )
    _extend_list_to_index(current, index)
    container = _container_for_next_part(current[index], next_part, field_path)
    current[index] = container
    return container


def _extend_list_to_index(target: list[Any], index: int) -> None:
    """Pad ``target`` with ``None`` placeholders so ``index`` is assignable."""

    while len(target) <= index:
        target.append(None)


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
            for field_path in _projection_field_paths(evidence_record, domain_object):
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
        metadata = _field_definition_metadata(field_definition)
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


def _workspace_fields(
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
    configured_fields = _workspace_group_fields(display_config)
    if not configured_fields:
        return []

    workspace_fields: list[DomainEnvelopeReviewRowSummaryField] = []
    seen: set[str] = set()
    for order, (field_path, group_metadata) in enumerate(configured_fields):
        if field_path in seen:
            continue
        seen.add(field_path)
        value = _payload_value(domain_object.payload, field_path)
        field_definition = field_definitions.get(field_path)
        metadata = _field_definition_metadata(field_definition)
        metadata.update(group_metadata)
        metadata.update(
            _unavailable_capabilities_metadata(
                unavailable_capabilities_by_field.get(
                    (stable_object_id(domain_object), field_path),
                    (),
                )
            )
        )
        if metadata.get("hide_when_empty") is True and _is_empty_projection_value(value):
            continue
        if value is _MISSING:
            value = None

        workspace_fields.append(
            DomainEnvelopeReviewRowSummaryField(
                field_path=field_path,
                label=_field_label(field_path, field_definition),
                value=value,
                field_type=(
                    field_definition.field_type.value
                    if field_definition is not None
                    else _value_field_type(value)
                ),
                metadata={
                    **metadata,
                    "workspace_order": order,
                },
            )
        )

    return workspace_fields


def _is_empty_projection_value(value: Any) -> bool:
    if value is _MISSING or value is None:
        return True
    if isinstance(value, (str, list, tuple, dict, set)) and len(value) == 0:
        return True
    return False


def _workspace_group_fields(
    display_config: Mapping[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    raw_groups = display_config.get("groups")
    if not isinstance(raw_groups, list):
        return []

    configured_fields: list[tuple[str, dict[str, Any]]] = []
    for group_index, raw_group in enumerate(raw_groups):
        if not isinstance(raw_group, Mapping):
            raise DomainEnvelopeMaterializationError(
                "workspace_display.groups"
                f"[{group_index}] must be an object"
            )
        group_id = _optional_string(raw_group.get("id"))
        if group_id is None:
            continue
        group_label = _optional_string(raw_group.get("label"))
        if group_label is None:
            raise DomainEnvelopeMaterializationError(
                "workspace_display.groups"
                f"[{group_index}].label must be a non-empty string"
            )
        raw_fields = raw_group.get("fields")
        if not isinstance(raw_fields, list):
            continue
        for field_index, raw_field_path in enumerate(raw_fields):
            field_path = _optional_string(raw_field_path)
            if field_path is None:
                continue
            configured_fields.append(
                (
                    field_path,
                    {
                        "workspace_group": {
                            "id": group_id,
                            "label": group_label,
                            "order": group_index,
                            "field_order": field_index,
                        }
                    },
                )
            )
    return configured_fields


def _field_definition_metadata(
    field_definition: DomainPackFieldDefinition | None,
) -> dict[str, Any]:
    if field_definition is None:
        return {}
    protected = field_definition.metadata.get("protected") is True
    editable = field_definition.metadata.get("editable") is True
    return {
        **dict(field_definition.metadata),
        "required": field_definition.required,
        "editable": editable,
        "protected": protected,
        "read_only": protected or not editable,
        "definition_state": field_definition.definition_state.value,
    }


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
    for configured_field in _primary_label_field_candidates(display_config):
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


def _primary_label_field_candidates(
    display_config: Mapping[str, Any],
) -> list[str]:
    """Return ordered primary-label payload paths declared by a pack's workspace_display.

    Packs may declare a single ``primary_label_field`` or an ordered
    ``primary_label_fields`` fallback chain. The chain lets a pack name the best
    label field plus deterministic fallbacks (e.g. an allele label, then the
    associated gene symbol) so a curatable unit never falls back to its opaque
    pending id when a real label is present on the payload. Both keys are generic
    workspace_display metadata, so this stays domain-agnostic.
    """

    candidates: list[str] = []
    configured_fields = display_config.get("primary_label_fields")
    if isinstance(configured_fields, Sequence) and not isinstance(
        configured_fields, (str, bytes, bytearray)
    ):
        for raw_field in configured_fields:
            if isinstance(raw_field, str) and raw_field.strip():
                candidates.append(raw_field.strip())
    configured_field = display_config.get("primary_label_field")
    if isinstance(configured_field, str) and configured_field.strip():
        candidates.append(configured_field.strip())

    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        ordered.append(candidate)
    return ordered


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
    # metadata_refs are relative to the extraction-metadata namespace. Index evidence records from
    # there (falling back to top-level for envelopes that were never nested) under the relative
    # "evidence_records" key so refs like "evidence_records[N]" resolve regardless of nesting.
    extraction_metadata = metadata.get("extraction_metadata")
    namespace = (
        extraction_metadata if isinstance(extraction_metadata, Mapping) else metadata
    )
    raw_records = namespace.get("evidence_records")
    if isinstance(raw_records, list):
        return [("evidence_records", raw_records)]
    return []


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
        if binding_state == DomainEnvelopeValidationStatus.UNDER_DEVELOPMENT.value:
            return DomainEnvelopeValidationStatus.UNDER_DEVELOPMENT

    if _optional_string(details.get("failure_classification")) == "blocked":
        return DomainEnvelopeValidationStatus.BLOCKED
    lookup_attempts = details.get("lookup_attempts")
    if isinstance(lookup_attempts, list):
        lookup_statuses = {
            _optional_string(attempt.get("lookup_status"))
            for attempt in lookup_attempts
            if isinstance(attempt, Mapping)
        }
        if "blocked" in lookup_statuses:
            return DomainEnvelopeValidationStatus.BLOCKED

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


def _evidence_target_maps(evidence_record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    targets: list[Mapping[str, Any]] = []
    for raw_target in [evidence_record.get("envelope_target")]:
        if isinstance(raw_target, Mapping):
            targets.append(raw_target)
    raw_targets = evidence_record.get("envelope_targets")
    if isinstance(raw_targets, Sequence) and not isinstance(raw_targets, (str, bytes, bytearray)):
        for raw_target in raw_targets:
            if isinstance(raw_target, Mapping):
                targets.append(raw_target)
    return targets


def _evidence_target_matches_object(
    target: Mapping[str, Any],
    domain_object: CuratableObjectEnvelope,
) -> bool:
    target_object_id = _optional_string(target.get("object_id"))
    target_pending_ref_id = _optional_string(target.get("pending_ref_id"))
    stable_id = stable_object_id(domain_object)
    return (
        target_object_id is not None
        and target_object_id in {domain_object.object_id, stable_id}
    ) or (
        target_pending_ref_id is not None
        and target_pending_ref_id in {domain_object.pending_ref_id, stable_id}
    )


def _projection_field_paths(
    evidence_record: Mapping[str, Any],
    domain_object: CuratableObjectEnvelope,
) -> list[str | None]:
    targets = _evidence_target_maps(evidence_record)
    if targets:
        matching_targets = [
            target
            for target in targets
            if _evidence_target_matches_object(target, domain_object)
        ]
        if not matching_targets:
            return []
        target_field_paths = _unique_strings(
            target.get("field_path") for target in matching_targets
        )
        return list(target_field_paths) if target_field_paths else [None]

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
        iterable = list(values)
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
    "ValidatorResultMaterializationInput",
    "ValidatorResultMaterializationResult",
    "materialize_persisted_envelope_review_rows",
    "materialize_validator_results_into_envelope",
    "project_evidence_anchor_projections",
    "project_validation_summary_projections",
    "stable_object_id",
]
