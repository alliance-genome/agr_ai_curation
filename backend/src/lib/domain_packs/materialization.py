"""Provider-neutral workspace projections and review-row materialization."""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
import logging
from pathlib import Path
from typing import Any, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .profile_validation import ProfileValidationContext

from sqlalchemy.orm import Session

from src.schemas.curation_workspace import (
    DomainEnvelopeEvidenceAnchorProjection,
    DomainEnvelopeReviewCuratorOverride,
    DomainEnvelopeReviewFieldResolution,
    DomainEnvelopeReviewResolvedValue,
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
    DefinitionState,
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
    DomainPackFieldType,
    DomainPackMetadata,
    DomainPackObjectDefinition,
)
from src.schemas.domain_validator import (
    DomainValidationRequest,
    DomainValidatorResultBase,
    ValidatorFieldResolution,
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
from src.lib.domain_packs.resolvable_values import (
    CURATOR_OVERRIDE_KEY,
    DECISIVE_OUTCOMES,
    INVALID_RECORD_EXPLANATION,
    LEAF_VALUE_LABELS,
    LOOKUP_OUTCOME_KEY,
    LOOKUP_OUTCOME_LABELS,
    MENTION_KEY,
    OUTCOME_INVALID_SCHEMA,
    OUTCOME_MATCHED,
    OUTCOME_MISSING_EXPECTED_RESULT_FIELD,
    OUTCOME_NOT_VALIDATED,
    RESOLUTION_STATE_KEY,
    RESOLVED,
    ResolvableSpec,
    ResolvableValueError,
    UNRESOLVED,
    UNRESOLVED_DISPLAY,
    VALIDATOR_CURATOR_MESSAGE_KEY,
    VALIDATOR_EXPLANATION_KEY,
    VALIDATOR_MATERIALIZATION_METADATA_KEY,
    copy_resolution,
    declared_resolvable_fields,
    declared_spec_for,
    effective_payload,
    has_resolution_state,
    is_curator_override,
    lookup_outcome_for_failure,
    mark_resolved,
    mark_unresolved,
    stored_state_problem,
    unresolved_header_text,
    validator_event_covers,
)
from src.lib.domain_packs.value_presence import missing_resolved_value
from src.lib.openai_agents.config import (
    get_validation_detail_list_limit,
    get_validation_detail_mapping_limit,
    get_validation_detail_string_limit,
)


logger = logging.getLogger(__name__)

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
    dispatch_context: Mapping[str, Any] | None = None


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
    profile_context: ProfileValidationContext | None = None

    def materialize(
        self,
        envelope: DomainEnvelope,
        *,
        envelope_revision: int,
        stored_envelope: DomainEnvelope | None = None,
    ) -> list[DomainEnvelopeReviewRow]:
        """Project one review row per non-metadata-only envelope object.

        A pack that reads its values through a display copy passes the stored
        envelope as ``stored_envelope`` (same objects, same order): a value's
        stored identity, the ``before`` of an override, comes from storage.
        """

        if envelope.domain_pack_id != self.metadata.pack_id:
            raise DomainEnvelopeMaterializationError(
                "Envelope domain_pack_id does not match materializer metadata: "
                f"{envelope.domain_pack_id!r} != {self.metadata.pack_id!r}"
            )
        if envelope_revision < 1:
            raise DomainEnvelopeMaterializationError(
                "envelope_revision must be greater than zero"
            )
        if self.profile_context is not None:
            from src.lib.curation_workspace.execution_contracts import require_resolved_profile_conformance
            require_resolved_profile_conformance(self.profile_context.profile, self.profile_context.receipt,
                                                 envelope.model_dump(mode="json"))

        object_definitions = {
            definition.object_type: definition
            for definition in self.metadata.object_definitions
        }
        validation_state_by_object = _validation_state_by_object(envelope)
        value_display_source = _review_value_display_source(self.metadata)
        override_disagreements = _open_override_disagreements(envelope)
        unavailable_capabilities = _unavailable_validator_capabilities_by_target(
            envelope,
            metadata=self.metadata,
            profile_context=self.profile_context,
        )
        rows: list[DomainEnvelopeReviewRow] = []

        stored_objects = (stored_envelope or envelope).extracted_objects
        if len(stored_objects) != len(envelope.extracted_objects):
            raise DomainEnvelopeMaterializationError("stored_envelope must hold the same objects as the envelope")
        for object_index, domain_object in enumerate(envelope.extracted_objects):
            object_definition = object_definitions.get(domain_object.object_type)
            object_id = stable_object_id(domain_object)
            object_role = _object_role(
                domain_object,
                object_definition,
                object_role_key=_object_role_key(self.metadata),
            )
            if self.profile_context is not None:
                # Every closed-profile object is a curation record. Model-supplied
                # metadata cannot hide it as a packaged metadata/reference row.
                object_role = None
            if object_role == "metadata_only":
                continue

            display_config = _workspace_display_config(domain_object, object_definition)
            if self.profile_context is not None:
                display_config = object_definition.metadata.get("workspace_display", {}) if object_definition else {}
            resolvable_fields = declared_resolvable_fields(self.metadata, domain_object.object_type)
            if resolvable_fields:
                # The one read-time pass of the legacy rule: every surface of the
                # row (values, readings, labels) reads this copy.
                domain_object = domain_object.model_copy(update={"payload": dict(effective_payload(
                    domain_object.payload, resolvable_fields, object_metadata=domain_object.metadata,
                ))})
            value_reader = _review_value_reader(
                domain_object,
                stored_objects[object_index].payload,
                resolvable_fields,
                value_display_source,
                envelope_id=envelope.envelope_id,
                envelope_revision=envelope_revision,
                override_disagreements=override_disagreements.get(object_id, {}),
                field_definitions={
                    field.field_path: field
                    for field in (object_definition.fields if object_definition is not None else [])
                },
            )
            summary_fields = _summary_fields(
                domain_object,
                object_definition=object_definition,
                display_config=display_config,
                unavailable_capabilities_by_field=(
                    unavailable_capabilities["by_field"]
                ),
                value_reader=value_reader,
            )
            workspace_fields = _workspace_fields(
                domain_object,
                object_definition=object_definition,
                display_config=display_config,
                unavailable_capabilities_by_field=(
                    unavailable_capabilities["by_field"]
                ),
                value_reader=value_reader,
            )
            display_label = _display_label(
                domain_object,
                display_config=display_config,
                resolvable_fields=resolvable_fields,
            )
            secondary_label = _secondary_label(
                domain_object,
                display_config=display_config,
                resolvable_fields=resolvable_fields,
            )

            metadata = {
                "semantic_source": "domain_envelope.extracted_objects",
                "materializer": type(self).__name__,
                "object_index": object_index,
                # The legacy objects[n] review metadata path was dropped after
                # the downstream contract renamed the list to extracted_objects.
                "payload_path": f"extracted_objects[{object_index}].payload",
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
            if self.profile_context is not None:
                metadata.update(
                    execution_receipt=self.profile_context.receipt.model_dump(mode="json"),
                    generic_profile_ref=self.profile_context.profile.receipt,
                    profile_conformance="conforming", linkml_alignment="not_assessed",
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
                        if domain_object.schema_ref is not None and self.profile_context is None
                        else None
                    ),
                    schema_ref=(
                        domain_object.schema_ref.model_dump(mode="json")
                        if domain_object.schema_ref is not None and self.profile_context is None
                        else {}
                    ),
                    object_model_ref=_object_model_ref(domain_object, object_definition) if self.profile_context is None else {},
                    model_field_ref=_model_field_ref(domain_object, object_definition) if self.profile_context is None else {},
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
    active_group_ids: Sequence[str] | None = (),
    user_id: int | str | None = None,
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
    from src.lib.curation_workspace.adapter_registry import resolve_curation_domain_pack_by_id
    from src.lib.domain_packs.profile_validation import resolve_envelope_profile_validation
    domain_pack = resolve_curation_domain_pack_by_id(envelope.domain_pack_id)
    if domain_pack is None and envelope.metadata.get("execution_receipt") is not None:
        raise DomainEnvelopeMaterializationError("The saved execution's domain pack is unavailable for review")
    profile_context = (resolve_envelope_profile_validation(envelope, domain_pack, db=db,
                       active_group_ids=active_group_ids, user_id=user_id) if domain_pack is not None else None)
    if profile_context is not None:
        resolved_materializer = DomainPackMetadataReviewRowMaterializer(
            profile_context.registry.domain_pack.metadata, profile_context=profile_context,
        )
    else:
        resolved_materializer = materializer or _registered_materializer_for(envelope.domain_pack_id)
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
    resolvable_fields_by_type = {
        definition.object_type: declared_resolvable_fields(metadata, definition.object_type)
        for definition in metadata.object_definitions
    }

    for item in items:
        try:
            working_envelope, item_findings, linked_objects = _materialize_one_result(
                working_envelope,
                item,
                object_definitions=object_definitions,
                object_role_key=object_role_key,
                source_envelope_revision=source_envelope_revision,
                resolvable_fields_by_type=resolvable_fields_by_type,
            )
        except ResolvableValueError as exc:
            # One value that cannot be written never aborts the run or loses the
            # other results: it becomes this item's finding, nothing is written.
            item_findings = [
                _finding_for_materialization_problem(
                    item,
                    f"A validated value could not be written: {exc}",
                    source_envelope_revision=source_envelope_revision,
                )
            ]
            linked_objects = ()
        findings.extend(item_findings)
        materialized_objects.extend(linked_objects)

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


def _materialize_one_result(
    working_envelope: DomainEnvelope,
    item: ValidatorResultMaterializationInput,
    *,
    object_definitions: Mapping[str, DomainPackObjectDefinition],
    object_role_key: str,
    source_envelope_revision: int | None,
    resolvable_fields_by_type: Mapping[str, Mapping[str, ResolvableSpec]],
) -> tuple[DomainEnvelope, list[ValidationFinding], tuple[CuratableObjectEnvelope, ...]]:
    """One validator result's write-back, findings and linked reference objects."""

    findings: list[ValidationFinding] = []
    target_type = (
        item.match.object_envelope.object_type if item.match.object_envelope is not None else None
    )
    overrides = _curator_overridden_values(
        working_envelope,
        item,
        object_definitions=object_definitions,
        resolvable_fields=resolvable_fields_by_type.get(target_type or "", {}),
    )
    (
        working_envelope,
        patch_problem,
    ) = _patch_target_object_from_resolved_values(
        working_envelope,
        item,
        object_definitions=object_definitions,
        source_envelope_revision=source_envelope_revision,
        resolvable_fields=resolvable_fields_by_type.get(target_type or "", {}),
    )
    if patch_problem is not None:
        working_envelope = _write_back_to_referencing_objects(
            working_envelope,
            item,
            object_definitions=object_definitions,
            resolvable_fields_by_type=resolvable_fields_by_type,
            validated_references=None,
            source_envelope_revision=source_envelope_revision,
        )
        findings.append(
            _finding_for_materialization_problem(
                item,
                patch_problem,
                source_envelope_revision=source_envelope_revision,
            )
        )
        return working_envelope, findings, ()

    new_objects, materialization_problem = _materialized_objects_for_result(
        working_envelope,
        item,
        object_definitions=object_definitions,
        object_role_key=object_role_key,
        source_envelope_revision=source_envelope_revision,
    )
    if materialization_problem is None:
        if overrides.covers_every_write:
            # A validated reference object is not added or linked for values a
            # curator override sets.
            new_objects = []
        working_envelope, linked_objects = _append_materialized_objects(
            working_envelope,
            item,
            new_objects,
        )
        working_envelope = _write_back_to_referencing_objects(
            working_envelope,
            item,
            object_definitions=object_definitions,
            resolvable_fields_by_type=resolvable_fields_by_type,
            validated_references=new_objects,
            source_envelope_revision=source_envelope_revision,
        )
        validator_finding = _finding_for_validator_result(
            item,
            source_envelope_revision=source_envelope_revision,
        )
        if overrides.settles(item.result):
            # Curator overrides settle what this binding writes: the
            # validator's own outcome is not an open problem.
            validator_finding = _as_curator_override_finding(validator_finding)
        findings.append(validator_finding)
        findings.extend(
            _as_curator_override_finding(finding)
            if finding.field_ref is not None and overrides.covers_field(finding.field_ref.field_path)
            else finding
            for finding in _field_findings_for_expected_result_fields(
                working_envelope,
                item,
                validator_finding=validator_finding,
                object_definitions=object_definitions,
                materialized_objects=new_objects,
                source_envelope_revision=source_envelope_revision,
                resolvable_fields_by_type=resolvable_fields_by_type,
            )
        )
        findings.extend(
            _curator_override_disagreements(item, overrides, source_envelope_revision=source_envelope_revision)
        )
        return working_envelope, findings, tuple(linked_objects)

    working_envelope = _write_back_to_referencing_objects(
        working_envelope,
        item,
        object_definitions=object_definitions,
        resolvable_fields_by_type=resolvable_fields_by_type,
        validated_references=None,
        source_envelope_revision=source_envelope_revision,
    )
    findings.append(
        _finding_for_materialization_problem(
            item,
            materialization_problem,
            source_envelope_revision=source_envelope_revision,
        )
    )
    return working_envelope, findings, ()


@dataclass(frozen=True)
class _CuratorOverrides:
    """The curator-overridden values a validator result writes into (container path -> value)."""

    target: CuratableObjectEnvelope | None
    values: Mapping[str, Mapping[str, Any]]
    # Expected-result fields per overridden value: [(result field, materialized path)].
    fields: Mapping[str, Sequence[tuple[str, str]]]
    covers_every_write: bool
    # Result fields and value paths of declared values absent from the payload.
    absent: frozenset[str] = frozenset()

    def settles(self, result: DomainValidatorResultBase) -> bool:
        """Whether curator overrides settle this result's binding-level outcome.

        They do when they cover every present value the binding writes, or,
        for a composite result (``field_resolutions``), when every value it
        did not resolve is overridden or absent.
        """

        if self.covers_every_write:
            return True
        if not self.values or not result.field_resolutions:
            return False
        overridden = set(self.values) | {
            result_field for entries in self.fields.values() for result_field, _ in entries
        }
        return all(
            resolution.status == "resolved"
            for key, resolution in result.field_resolutions.items()
            if key not in overridden and key not in self.absent
        )

    def covers_field(self, field_path: str) -> bool:
        try:
            parts = parse_field_path(field_path)
        except ValueError:
            return False
        return _format_field_path(parts[:-1]) in self.values or field_path in self.values


def _curator_overridden_values(
    envelope: DomainEnvelope,
    item: ValidatorResultMaterializationInput,
    *,
    object_definitions: Mapping[str, DomainPackObjectDefinition],
    resolvable_fields: Mapping[str, ResolvableSpec],
) -> _CuratorOverrides:
    matched_target = item.match.object_envelope
    target = _current_object_for_match(envelope, matched_target) if matched_target is not None else None
    object_definition = item.match.object_definition or (
        object_definitions.get(target.object_type) if target is not None else None
    )
    if target is None or object_definition is None:
        return _CuratorOverrides(target, {}, {}, False)
    values, fields, mapped, absent = _overridden_writes(
        target.payload,
        item.request.expected_result_fields,
        declared_fields={field.field_path: field for field in object_definition.fields},
        resolvable_fields=resolvable_fields,
    )
    covered = sum(len(entries) for entries in fields.values())
    return _CuratorOverrides(target, values, fields, bool(values) and covered == mapped, absent)


def _overridden_writes(
    payload: Mapping[str, Any],
    expected_result_fields: Mapping[str, Any],
    *,
    declared_fields: Mapping[str, DomainPackFieldDefinition],
    resolvable_fields: Mapping[str, ResolvableSpec],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, list[tuple[str, str]]], int, frozenset[str]]:
    """What a binding writes into curator overrides on one object payload.

    Returns (overridden values by path, their (result field, path) writes,
    the number of writes into present values, the result fields and value
    paths of declared values absent from the payload). A write into a
    declared value the payload does not hold (e.g. an absent condition
    component) is not counted: nothing is there to settle.
    """

    values: dict[str, Mapping[str, Any]] = {}
    fields: dict[str, list[tuple[str, str]]] = {}
    absent: set[str] = set()
    mapped = 0
    for result_field, raw_field_path in expected_result_fields.items():
        if not isinstance(raw_field_path, str) or not raw_field_path.strip():
            continue
        materialized_field_path = _materialized_field_path(raw_field_path, declared_fields=declared_fields)
        if materialized_field_path is None:
            continue
        parts = parse_field_path(materialized_field_path)
        container_parts = parts[:-1] if isinstance(parts[-1], str) else parts
        if (
            declared_spec_for(resolvable_fields, container_parts) is not None
            and _payload_container(payload, _format_field_path(container_parts)) is None
        ):
            absent.update((result_field, _format_field_path(container_parts)))
            continue
        mapped += 1
        container_path = _resolvable_container_path(
            payload, materialized_field_path, resolvable_fields=resolvable_fields
        )
        if container_path is None:
            continue
        container = _payload_container(payload, container_path)
        if is_curator_override(container):
            values[container_path] = container
            fields.setdefault(container_path, []).append((result_field, materialized_field_path))
    return values, fields, mapped, frozenset(absent)


def expected_writes_settled_by_overrides(
    payload: Mapping[str, Any],
    expected_result_fields: Mapping[str, Any],
    *,
    object_definition: DomainPackObjectDefinition,
    resolvable_fields: Mapping[str, ResolvableSpec],
) -> bool:
    """Whether curator overrides now cover every present value a binding writes on this payload."""

    try:
        values, fields, mapped, _absent = _overridden_writes(
            payload,
            expected_result_fields,
            declared_fields={field.field_path: field for field in object_definition.fields},
            resolvable_fields=resolvable_fields,
        )
    except ResolvableValueError:
        return False
    return bool(values) and sum(len(entries) for entries in fields.values()) == mapped


def _as_curator_override_finding(finding: ValidationFinding) -> ValidationFinding:
    details = {
        key: value for key, value in finding.details.items() if key != "failure_classification"
    }
    return finding.model_copy(update={
        "severity": ValidationFindingSeverity.INFO,
        "status": ValidationFindingStatus.RESOLVED,
        "code": "domain_pack.curator_override",
        "message": "A curator override sets this value; a validator result does not change it.",
        "details": details,
    })


# An open finding: a validator disagrees with a curator override (the override stands).
CURATOR_OVERRIDE_DISAGREEMENT_CODE = "domain_pack.validator_disagrees_with_curator_override"


def _curator_override_disagreements(
    item: ValidatorResultMaterializationInput,
    overrides: _CuratorOverrides,
    *,
    source_envelope_revision: int | None,
) -> list[ValidationFinding]:
    """Open findings where a validator disagrees with a curator override (the override stands).

    A validator disagrees when it resolves a different identity or decides
    against the value (a decisive outcome); a non-decisive outcome or a
    matching identity is no disagreement.
    """

    result = item.result
    findings: list[ValidationFinding] = []
    for container_path, value in overrides.values.items():
        entries = overrides.fields[container_path]
        resolution = next(
            (
                result.field_resolutions[key]
                for key in result.field_resolutions
                if key == container_path or key in {result_field for result_field, _ in entries}
            ),
            None,
        )
        if resolution is not None:
            status, resolved_values, outcome = (
                resolution.status, resolution.resolved_values, resolution.lookup_outcome,
            )
        elif result.status == "resolved":
            status, resolved_values, outcome = "resolved", result.resolved_values, OUTCOME_MATCHED
        else:
            status, resolved_values = "unresolved", {}
            outcome = lookup_outcome_for_failure(
                validator_failure_classification(result, error_type=DomainEnvelopeMaterializationError)
            )
        if status == "resolved":
            differing = {
                str(parse_field_path(path)[-1]): resolved_values[result_field]
                for result_field, path in entries
                if not missing_resolved_value(resolved_values.get(result_field))
                # A key the override holds empty is not compared.
                and value.get(str(parse_field_path(path)[-1])) is not None
                and resolved_values[result_field] != value.get(str(parse_field_path(path)[-1]))
            }
            if not differing:
                continue
            detail = "it resolved " + ", ".join(f"{key} {item!r}" for key, item in differing.items())
        elif outcome in DECISIVE_OUTCOMES:
            detail = f"its lookup result is {LOOKUP_OUTCOME_LABELS[outcome]}"
        else:
            continue
        object_ref = overrides.target.to_object_ref()
        findings.append(ValidationFinding(
            severity=ValidationFindingSeverity.WARNING,
            status=ValidationFindingStatus.OPEN,
            code=CURATOR_OVERRIDE_DISAGREEMENT_CODE,
            message=f"Validator disagrees with the curator override: {detail}.",
            object_ref=None if container_path else object_ref,
            field_ref=FieldRef(object_ref=object_ref, field_path=container_path) if container_path else None,
            details={
                "validator_binding_id": result.validator_binding_id,
                "request_id": result.request_id,
                "lookup_outcome": outcome,
                "validator_explanation": result.explanation,
                **({"validator_curator_message": result.curator_message} if result.curator_message else {}),
                **({"source_envelope_revision": source_envelope_revision}
                   if source_envelope_revision is not None else {}),
            },
        ))
    return findings


def _patch_target_object_from_resolved_values(
    envelope: DomainEnvelope,
    item: ValidatorResultMaterializationInput,
    *,
    object_definitions: Mapping[str, DomainPackObjectDefinition],
    source_envelope_revision: int | None,
    resolvable_fields: Mapping[str, ResolvableSpec] | None = None,
) -> tuple[DomainEnvelope, str | None]:
    """Patch validator-owned results onto the matched envelope object.

    Plain fields receive resolved values as before. A resolvable value (a
    stored object with a ``mention`` or ``resolution_state``, see
    ``resolvable_values``) is written as a whole: resolved only when every
    expected field the binding writes into it came back, and otherwise marked
    unresolved with the validator's failure classification, never touching
    its id/label.
    """

    result = item.result
    matched_target = item.match.object_envelope
    if matched_target is None:
        return envelope, None
    target = _current_object_for_match(envelope, matched_target)
    object_definition = item.match.object_definition
    if object_definition is None and target is not None:
        object_definition = object_definitions.get(target.object_type)
    declared_fields = (
        {field.field_path: field for field in object_definition.fields}
        if object_definition is not None
        else {}
    )

    if result.field_resolutions:
        # A composite validator decides each value itself (ALL-1299).
        if target is None or object_definition is None:
            return envelope, None
        return _patch_target_object_from_field_resolutions(
            envelope,
            item,
            target,
            object_definition=object_definition,
            declared_fields=declared_fields,
            source_envelope_revision=source_envelope_revision,
            resolvable_fields=resolvable_fields,
        )

    if result.status != "resolved":
        if target is None or object_definition is None:
            return envelope, None
        outcome = lookup_outcome_for_failure(
            validator_failure_classification(
                result,
                error_type=DomainEnvelopeMaterializationError,
            )
        )
        return (
            _with_unresolved_values(
                envelope, item, target, declared_fields, outcome,
                resolvable_fields=resolvable_fields,
            ),
            None,
        )
    if not result.resolved_values:
        if target is None or object_definition is None:
            return envelope, None
        return (
            _with_unresolved_values(
                envelope,
                item,
                target,
                declared_fields,
                OUTCOME_MISSING_EXPECTED_RESULT_FIELD,
                resolvable_fields=resolvable_fields,
            ),
            None,
        )
    policy_violations = allowed_term_policy_violations(result, request=item.request)
    if policy_violations:
        if target is not None and object_definition is not None:
            envelope = _with_unresolved_values(
                envelope, item, target, declared_fields, OUTCOME_INVALID_SCHEMA,
                resolvable_fields=resolvable_fields,
            )
        return envelope, "; ".join(
            violation.message for violation in policy_violations
        )
    if target is None or object_definition is None:
        return envelope, None

    payload = copy.deepcopy(target.payload)
    has_materializable_resolved_value = False
    plain_writes: list[tuple[str, Any]] = []
    resolvable_writes: dict[str, list[tuple[str, Any]]] = {}

    for result_field, raw_field_path in item.request.expected_result_fields.items():
        if not isinstance(raw_field_path, str) or not raw_field_path.strip():
            return envelope, (
                "expected_result_fields values must be non-empty field path strings"
            )
        materialized_field_path = _materialized_field_path(
            raw_field_path,
            declared_fields=declared_fields,
        )
        if materialized_field_path is None:
            continue
        resolved_value = result.resolved_values.get(result_field)
        container_path = _resolvable_container_path(
            payload, materialized_field_path, resolvable_fields=resolvable_fields
        )
        if container_path is not None:
            missing = (
                result_field in result.missing_expected_fields
                or missing_resolved_value(resolved_value)
            )
            resolvable_writes.setdefault(container_path, []).append(
                (materialized_field_path, _MISSING if missing else resolved_value)
            )
            continue
        if missing_resolved_value(resolved_value):
            continue
        plain_writes.append((materialized_field_path, resolved_value))

    for materialized_field_path, resolved_value in plain_writes:
        has_materializable_resolved_value = True
        current_value = _payload_value(payload, materialized_field_path)
        if current_value is not _MISSING and current_value == resolved_value:
            continue
        _set_payload_value(payload, materialized_field_path, resolved_value)
        _propagate_materialized_mirror_paths(
            payload,
            materialized_field_path,
            resolved_value,
            declared_fields=declared_fields,
            resolvable_fields=resolvable_fields,
        )

    for container_path, writes in resolvable_writes.items():
        container = _payload_container(payload, container_path)
        if is_curator_override(container):
            # A curator override wins: the validator's result is reported, not written.
            continue
        if any(value is _MISSING for _, value in writes):
            # A partial identity is not a validated value.
            before = copy.deepcopy(container)
            mark_unresolved(
                container,
                OUTCOME_MISSING_EXPECTED_RESULT_FIELD,
                explanation=result.explanation,
                curator_message=result.curator_message,
                identity_keys=_container_identity_keys(
                    item, container_path, declared_fields=declared_fields, resolvable_fields=resolvable_fields,
                ),
            )
            if container == before:
                continue
            for materialized_field_path, _ in writes:
                _propagate_materialized_resolution_state(
                    payload, materialized_field_path, declared_fields=declared_fields,
                    resolvable_fields=resolvable_fields,
                )
            continue
        has_materializable_resolved_value = True
        mark_resolved(
            container,
            {
                str(parse_field_path(materialized_field_path)[-1]): resolved_value
                for materialized_field_path, resolved_value in writes
            },
            explanation=result.explanation,
            curator_message=result.curator_message,
            identity_keys=_container_identity_keys(
                item, container_path, declared_fields=declared_fields, resolvable_fields=resolvable_fields,
            ),
        )
        for materialized_field_path, resolved_value in writes:
            _propagate_materialized_mirror_paths(
                payload,
                materialized_field_path,
                resolved_value,
                declared_fields=declared_fields,
                resolvable_fields=resolvable_fields,
            )
    return _with_patched_target(
        envelope,
        item,
        target,
        payload,
        object_definition=object_definition,
        declared_fields=declared_fields,
        validated=has_materializable_resolved_value,
        materialized_field_paths=[
            *(path for path, _ in plain_writes),
            *(
                path
                for writes in resolvable_writes.values()
                if all(value is not _MISSING for _, value in writes)
                for path, _ in writes
            ),
        ],
        source_envelope_revision=source_envelope_revision,
    )


def _field_resolution_targets(
    item: ValidatorResultMaterializationInput,
    payload: Mapping[str, Any],
    declared_fields: Mapping[str, DomainPackFieldDefinition],
    *,
    resolvable_fields: Mapping[str, ResolvableSpec] | None = None,
) -> tuple[dict[str, tuple[str, list[tuple[str, str]]]], str | None]:
    """Resolve each ``field_resolutions`` key to the resolvable value it decides.

    A key is an expected-result field or the payload path of a resolvable
    value that expected-result fields write into. Returns {key: (container
    path, [(result field, materialized field path), ...])} or a problem.
    """

    fields_by_container: dict[str, list[tuple[str, str]]] = {}
    container_by_result_field: dict[str, str] = {}
    for result_field, raw_field_path in item.request.expected_result_fields.items():
        if not isinstance(raw_field_path, str) or not raw_field_path.strip():
            return {}, "expected_result_fields values must be non-empty field path strings"
        materialized_field_path = _materialized_field_path(
            raw_field_path,
            declared_fields=declared_fields,
        )
        if materialized_field_path is None:
            continue
        container_path = _resolvable_container_path(
            payload, materialized_field_path, resolvable_fields=resolvable_fields
        )
        if container_path is None:
            continue
        fields_by_container.setdefault(container_path, []).append(
            (result_field, materialized_field_path)
        )
        container_by_result_field[result_field] = container_path

    targets: dict[str, tuple[str, list[tuple[str, str]]]] = {}
    for key in item.result.field_resolutions:
        container_path = container_by_result_field.get(key)
        if container_path is None and key in fields_by_container:
            container_path = key
        if container_path is None:
            return {}, (
                f"field_resolutions key {key!r} names no resolvable value this binding writes"
            )
        if any(existing == container_path for existing, _ in targets.values()):
            return {}, (
                f"field_resolutions decide the value at {container_path!r} more than once"
            )
        targets[key] = (container_path, fields_by_container[container_path])
    return targets, None


def _patch_target_object_from_field_resolutions(
    envelope: DomainEnvelope,
    item: ValidatorResultMaterializationInput,
    target: CuratableObjectEnvelope,
    *,
    object_definition: DomainPackObjectDefinition,
    declared_fields: Mapping[str, DomainPackFieldDefinition],
    source_envelope_revision: int | None,
    resolvable_fields: Mapping[str, ResolvableSpec] | None = None,
) -> tuple[DomainEnvelope, str | None]:
    """Write a composite validator's per-value decisions (``field_resolutions``).

    Each listed resolvable value is marked resolved with its own values (all
    of the binding's expected fields for it must come back) or unresolved
    with its own lookup outcome; its explanation and curator message are the
    validator's own. Values not listed get no write, and the overall result
    status never overwrites a value with its own decision. Plain fields
    still take the overall resolved values. Each resolved decision obeys the
    request's allowed-term list: a violation leaves the value unresolved
    (``invalid_schema``, never touching a resolved one) and is reported.
    """

    result = item.result
    payload = copy.deepcopy(target.payload)
    targets, problem = _field_resolution_targets(
        item, payload, declared_fields, resolvable_fields=resolvable_fields
    )
    if problem is not None:
        return envelope, problem

    written: list[str] = []
    policy_problems: list[str] = []
    for key, (container_path, fields) in targets.items():
        resolution = result.field_resolutions[key]
        container = _payload_container(payload, container_path)
        if is_curator_override(container):
            # A curator override wins: the validator's decision is reported, not written.
            continue
        values = {
            materialized_field_path: resolution.resolved_values.get(result_field)
            for result_field, materialized_field_path in fields
        }
        violated = False
        if resolution.status == "resolved":
            # Each decision obeys the request's allowed-term list, like a whole result.
            violations = allowed_term_policy_violations(
                result.model_copy(
                    update={"status": "resolved", "resolved_values": dict(resolution.resolved_values)}
                ),
                request=item.request,
            )
            policy_problems.extend(violation.message for violation in violations)
            violated = bool(violations)
        if resolution.status == "resolved" and not violated and not any(
            missing_resolved_value(value) for value in values.values()
        ):
            mark_resolved(
                container,
                {str(parse_field_path(path)[-1]): value for path, value in values.items()},
                explanation=resolution.explanation,
                curator_message=resolution.curator_message,
                identity_keys=_container_identity_keys(
                    item, container_path, declared_fields=declared_fields, resolvable_fields=resolvable_fields,
                ),
            )
            for materialized_field_path, value in values.items():
                _propagate_materialized_mirror_paths(
                    payload, materialized_field_path, value, declared_fields=declared_fields,
                    resolvable_fields=resolvable_fields,
                )
            written.extend(values)
            continue
        before = copy.deepcopy(container)
        mark_unresolved(
            container,
            (
                OUTCOME_INVALID_SCHEMA
                if violated
                else resolution.lookup_outcome
                if resolution.status == "unresolved"
                else OUTCOME_MISSING_EXPECTED_RESULT_FIELD
            ),
            explanation=resolution.explanation,
            curator_message=resolution.curator_message,
            identity_keys=_container_identity_keys(
                item, container_path, declared_fields=declared_fields, resolvable_fields=resolvable_fields,
            ),
        )
        if container == before:
            continue
        for _result_field, materialized_field_path in fields:
            _propagate_materialized_resolution_state(
                payload, materialized_field_path, declared_fields=declared_fields,
                resolvable_fields=resolvable_fields,
            )

    overall_violations = (
        allowed_term_policy_violations(result, request=item.request) if result.status == "resolved" else []
    )
    policy_problems.extend(violation.message for violation in overall_violations)
    if result.status == "resolved" and not overall_violations:
        for result_field, raw_field_path in item.request.expected_result_fields.items():
            materialized_field_path = _materialized_field_path(
                raw_field_path,
                declared_fields=declared_fields,
            )
            resolved_value = result.resolved_values.get(result_field)
            if (
                materialized_field_path is None
                or missing_resolved_value(resolved_value)
                or _resolvable_container_path(
                    payload, materialized_field_path, resolvable_fields=resolvable_fields
                ) is not None
            ):
                continue
            if _payload_value(payload, materialized_field_path) != resolved_value:
                _set_payload_value(payload, materialized_field_path, resolved_value)
                _propagate_materialized_mirror_paths(
                    payload, materialized_field_path, resolved_value, declared_fields=declared_fields,
                    resolvable_fields=resolvable_fields,
                )
            written.append(materialized_field_path)

    patched, _ = _with_patched_target(
        envelope,
        item,
        target,
        payload,
        object_definition=object_definition,
        declared_fields=declared_fields,
        validated=bool(written),
        materialized_field_paths=written,
        source_envelope_revision=source_envelope_revision,
    )
    # The other decisions are written; an allowed-term violation is reported.
    return patched, ("; ".join(dict.fromkeys(policy_problems)) if policy_problems else None)


def _field_resolution_for(
    item: ValidatorResultMaterializationInput,
    materialized_field_path: str,
    declared_fields: Mapping[str, DomainPackFieldDefinition],
) -> ValidatorFieldResolution | None:
    """The composite decision covering the value an expected-result field writes into."""

    def container_of(field_path: str) -> str | None:
        try:
            parts = parse_field_path(field_path)
        except ValueError:
            return None
        return _format_field_path(parts[:-1])

    container = container_of(materialized_field_path)
    for key, resolution in item.result.field_resolutions.items():
        if key == container:
            return resolution
        raw_field_path = item.request.expected_result_fields.get(key)
        if not isinstance(raw_field_path, str) or not raw_field_path.strip():
            continue
        key_path = _materialized_field_path(raw_field_path, declared_fields=declared_fields)
        if key_path is not None and container_of(key_path) == container:
            return resolution
    return None


def _without_validator_words(value: Any) -> Any:
    """A payload without the validators' free-text words (identity, state and outcome only)."""

    if isinstance(value, list):
        return [_without_validator_words(item) for item in value]
    if isinstance(value, Mapping):
        return {
            key: _without_validator_words(item)
            for key, item in value.items()
            if key not in (VALIDATOR_EXPLANATION_KEY, VALIDATOR_CURATOR_MESSAGE_KEY)
        }
    return value


def _with_patched_target(
    envelope: DomainEnvelope,
    item: ValidatorResultMaterializationInput,
    target: CuratableObjectEnvelope,
    payload: dict[str, Any],
    *,
    object_definition: DomainPackObjectDefinition,
    declared_fields: Mapping[str, DomainPackFieldDefinition],
    validated: bool,
    materialized_field_paths: Sequence[str],
    source_envelope_revision: int | None,
) -> tuple[DomainEnvelope, str | None]:
    """Apply a write-back payload; a validated write also records its event and status."""

    result = item.result
    payload_changed = payload != target.payload
    if not validated:
        if not payload_changed:
            return envelope, None
        return _with_object_payload(envelope, target, payload), None

    definition_state = (
        DefinitionState.STABLE
        if item.match.binding.state is ValidationBindingState.ACTIVE
        and object_definition.definition_state is DefinitionState.STABLE
        else target.definition_state
    )
    if (
        # Only the decision counts: a validator's explanation words differ from
        # run to run, so they are updated in place without a new event.
        _without_validator_words(payload) == _without_validator_words(target.payload)
        and target.status is CuratableObjectStatus.VALIDATED
        and target.definition_state is definition_state
        # A value an earlier event already covers needs no new event; an
        # unchanged value no event covers yet (e.g. one stored before
        # validator events) is recorded so the legacy rule sees it verified.
        and all(validator_event_covers(target.metadata, path) for path in materialized_field_paths)
    ):
        if not payload_changed:
            return envelope, None
        return _with_object_payload(envelope, target, payload), None

    original_values = _original_materialized_values(
        target.payload,
        item.request.expected_result_fields.values(),
        declared_fields=declared_fields,
    )

    metadata = dict(target.metadata)
    existing_patch_metadata = metadata.get(VALIDATOR_MATERIALIZATION_METADATA_KEY)
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
            "materialized_field_paths": list(materialized_field_paths),
            **(
                {"source_envelope_revision": source_envelope_revision}
                if source_envelope_revision is not None
                else {}
            ),
        }
    )
    metadata[VALIDATOR_MATERIALIZATION_METADATA_KEY] = patch_events

    patched_target = target.model_copy(
        update={
            "payload": payload,
            "status": CuratableObjectStatus.VALIDATED,
            "definition_state": definition_state,
            "metadata": metadata,
        }
    )
    objects = [
        patched_target if _same_object_identity(candidate, target) else candidate
        for candidate in envelope.extracted_objects
    ]
    return envelope.model_copy(update={"extracted_objects": objects}), None


def _write_back_to_referencing_objects(
    envelope: DomainEnvelope,
    item: ValidatorResultMaterializationInput,
    *,
    object_definitions: Mapping[str, DomainPackObjectDefinition],
    resolvable_fields_by_type: Mapping[str, Mapping[str, ResolvableSpec]],
    validated_references: Sequence[CuratableObjectEnvelope] | None,
    source_envelope_revision: int | None,
) -> DomainEnvelope:
    """Carry a binding's outcome onto the objects that reference its target.

    A binding validates one target object, but a pack may declare that its
    value belongs on another object too: a field whose
    ``metadata.validation_result_binding_id`` names the binding, on an object
    holding an object_ref to the validated target. A scalar field names the
    result field it mirrors (``validation_result_field``); an object_ref field
    links the validated reference of its ``object_type_ref``.

    The referencing object follows the target's value as written for this
    result (``_referenced_decision``), so a curator override on the target
    drives it exactly like a validated identity. Resolved: the mirrored values
    are written, a resolvable value holding them takes the source's state, the
    matching validated reference is linked, and a write-back event is recorded.
    Otherwise only the outcome is recorded on such a value (a resolved one is
    overruled into hints by a decisive outcome), and a value left unresolved
    drops its now-stale links. The referencing value only ever follows its
    source (``follow_referenced_value`` carries a curator's edit of the source
    right away). ``validated_references`` is None when the result could not be
    materialized.
    """

    matched_target = item.match.object_envelope
    if matched_target is None:
        return envelope
    target = _current_object_for_match(envelope, matched_target)
    target_definition = item.match.object_definition or (
        object_definitions.get(target.object_type) if target is not None else None
    )
    if target is None or target_definition is None:
        return envelope
    result = item.result
    event = {
        "source": "domain_validator_referenced_target",
        "request_id": result.request_id,
        "validator_binding_id": result.validator_binding_id,
        "validator_agent": result.validator_agent.model_dump(mode="json"),
        "validated_target": matched_target.to_object_ref().model_dump(mode="json", exclude_none=True),
        **(
            {"source_envelope_revision": source_envelope_revision}
            if source_envelope_revision is not None
            else {}
        ),
    }
    return _with_referencing_objects_following(
        envelope,
        target,
        item.request.validator_binding_id,
        item.request.expected_result_fields,
        object_definitions=object_definitions,
        target_definition=target_definition,
        resolvable_fields_by_type=resolvable_fields_by_type,
        decide=lambda declared, target_fields, target_resolvable_fields: _referenced_decision(
            item,
            declared,
            target=target,
            target_fields=target_fields,
            target_resolvable_fields=target_resolvable_fields,
            validated_references=validated_references,
        ),
        validated_references=validated_references or (),
        fallback_text=(result.explanation, result.curator_message),
        event=event,
    )


def follow_referenced_value(
    envelope: DomainEnvelope,
    source_object: CuratableObjectEnvelope,
    *,
    metadata: DomainPackMetadata,
    expected_result_fields_by_binding: Mapping[str, Mapping[str, str]],
) -> DomainEnvelope:
    """Carry a curator's edit of a validated value to the objects that mirror it.

    The same declarations as the validator write-back
    (``_write_back_to_referencing_objects``): objects holding an object_ref to
    ``source_object`` whose fields name one of the bindings take the source
    value's identity and state straight away, exactly as the next validator
    run would. Links to validated references carrying another identity drop.
    """

    object_definitions = {definition.object_type: definition for definition in metadata.object_definitions}
    source_definition = object_definitions.get(source_object.object_type)
    if source_definition is None:
        return envelope
    resolvable_fields_by_type = {
        definition.object_type: declared_resolvable_fields(metadata, definition.object_type)
        for definition in metadata.object_definitions
    }
    for binding_id, expected_result_fields in expected_result_fields_by_binding.items():
        envelope = _with_referencing_objects_following(
            envelope,
            source_object,
            binding_id,
            expected_result_fields,
            object_definitions=object_definitions,
            target_definition=source_definition,
            resolvable_fields_by_type=resolvable_fields_by_type,
            decide=lambda declared, target_fields, target_resolvable_fields, binding=expected_result_fields: (
                _source_decision(
                    binding,
                    declared,
                    target=source_object,
                    target_fields=target_fields,
                    target_resolvable_fields=target_resolvable_fields,
                )
            ),
            validated_references=(),
            fallback_text=(None, None),
            event=None,
        )
    return envelope


def _with_referencing_objects_following(
    envelope: DomainEnvelope,
    target: CuratableObjectEnvelope,
    binding_id: str,
    expected_result_fields: Mapping[str, Any],
    *,
    object_definitions: Mapping[str, DomainPackObjectDefinition],
    target_definition: DomainPackObjectDefinition,
    resolvable_fields_by_type: Mapping[str, Mapping[str, ResolvableSpec]],
    decide: Any,
    validated_references: Sequence[CuratableObjectEnvelope],
    fallback_text: tuple[str | None, str | None],
    event: Mapping[str, Any] | None,
) -> DomainEnvelope:
    target_keys = set(target.ref_keys())
    target_fields = {field.field_path: field for field in target_definition.fields}
    target_resolvable_fields = resolvable_fields_by_type.get(target.object_type, {})
    objects = list(envelope.extracted_objects)
    objects_by_ref = {key: obj for obj in objects for key in obj.ref_keys()}
    changed = False
    for index, domain_object in enumerate(objects):
        if _same_object_identity(domain_object, target) or not any(
            ref.ref_key() in target_keys for ref in domain_object.object_refs
        ):
            continue
        object_definition = object_definitions.get(domain_object.object_type)
        declared = [
            field
            for field in (object_definition.fields if object_definition is not None else [])
            if field.metadata.get("validation_result_binding_id") == binding_id
        ]
        if not declared:
            continue
        decision = decide(declared, target_fields, target_resolvable_fields)
        if decision is None:
            continue
        updated = _referencing_object_with_result(
            domain_object,
            declared,
            decision,
            resolvable_fields=resolvable_fields_by_type.get(domain_object.object_type, {}),
            validated_references=validated_references,
            objects_by_ref=objects_by_ref,
            fallback_text=fallback_text,
            event=event,
        )
        if updated is not domain_object:
            objects[index] = updated
            changed = True
    if not changed:
        return envelope
    return envelope.model_copy(update={"extracted_objects": objects})


@dataclass(frozen=True)
class _ReferencedDecision:
    """What the referencing objects mirror for one validator result.

    ``values`` maps each referencing scalar path to its value; ``source`` is the
    target's resolvable value those came from (None for a plain target, where
    the validator result itself decides); ``target_keys`` maps each referencing
    scalar path to the key it mirrors on the target.
    """

    resolved: bool
    outcome: str | None
    values: Mapping[str, Any]
    target_keys: Mapping[str, str]
    source: Mapping[str, Any] | None


def _source_paths(
    expected_result_fields: Mapping[str, Any],
    declared: Sequence[DomainPackFieldDefinition],
    target_fields: Mapping[str, DomainPackFieldDefinition],
) -> dict[str, str | None]:
    """Each referencing scalar path -> the target path its result field is written to."""

    source_paths: dict[str, str | None] = {}
    for field in declared:
        result_field = field.metadata.get("validation_result_field")
        if not result_field:
            continue
        raw_path = expected_result_fields.get(str(result_field))
        source_paths[field.field_path] = (
            _materialized_field_path(raw_path, declared_fields=target_fields)
            if isinstance(raw_path, str) and raw_path.strip()
            else None
        )
    return source_paths


def _source_decision(
    expected_result_fields: Mapping[str, Any],
    declared: Sequence[DomainPackFieldDefinition],
    *,
    target: CuratableObjectEnvelope,
    target_fields: Mapping[str, DomainPackFieldDefinition],
    target_resolvable_fields: Mapping[str, ResolvableSpec],
) -> _ReferencedDecision | None:
    """The decision the target's resolvable value (validated or curator-set) makes, if it has one."""

    source_paths = _source_paths(expected_result_fields, declared, target_fields)
    containers = {
        _resolvable_container_path(target.payload, source, resolvable_fields=target_resolvable_fields)
        for source in source_paths.values()
        if source is not None
    }
    if len(containers) != 1 or None in containers:
        return None
    source = _payload_container(target.payload, next(iter(containers)))
    if not has_resolution_state(source):
        return None
    values = {
        path: _payload_value(target.payload, source_path)
        for path, source_path in source_paths.items()
        if source_path is not None
    }
    resolved = source[RESOLUTION_STATE_KEY] == RESOLVED
    return _ReferencedDecision(
        resolved=resolved,
        outcome=None if resolved else str(source[LOOKUP_OUTCOME_KEY]),
        values={path: None if value is _MISSING else value for path, value in values.items()},
        target_keys={
            path: str(parse_field_path(source_path)[-1]) if source_path else ""
            for path, source_path in source_paths.items()
        },
        source=source,
    )


def _referenced_decision(
    item: ValidatorResultMaterializationInput,
    declared: Sequence[DomainPackFieldDefinition],
    *,
    target: CuratableObjectEnvelope,
    target_fields: Mapping[str, DomainPackFieldDefinition],
    target_resolvable_fields: Mapping[str, ResolvableSpec],
    validated_references: Sequence[CuratableObjectEnvelope] | None,
) -> _ReferencedDecision:
    # The target's value, as the materializer (or a curator override) left it.
    decision = _source_decision(
        item.request.expected_result_fields,
        declared,
        target=target,
        target_fields=target_fields,
        target_resolvable_fields=target_resolvable_fields,
    )
    if decision is not None:
        return decision
    result = item.result
    source_paths = _source_paths(item.request.expected_result_fields, declared, target_fields)
    target_keys = {
        path: str(parse_field_path(source)[-1]) if source else ""
        for path, source in source_paths.items()
    }
    values = {
        field.field_path: result.resolved_values.get(str(field.metadata["validation_result_field"]))
        for field in declared
        if field.metadata.get("validation_result_field")
    }
    if result.status != "resolved":
        outcome = lookup_outcome_for_failure(
            validator_failure_classification(result, error_type=DomainEnvelopeMaterializationError)
        )
    elif any(missing_resolved_value(value) for value in values.values()):
        outcome = OUTCOME_MISSING_EXPECTED_RESULT_FIELD
    elif validated_references is None:
        outcome = OUTCOME_INVALID_SCHEMA
    else:
        outcome = None
    return _ReferencedDecision(
        resolved=outcome is None, outcome=outcome, values=values, target_keys=target_keys, source=None,
    )


def _reference_matches(reference: CuratableObjectEnvelope, decision: _ReferencedDecision) -> bool:
    """A validated reference carries the identity the referencing object mirrors."""

    return all(
        reference.payload.get(key) == decision.values[path]
        for path, key in decision.target_keys.items()
        if key and key in reference.payload
    )


def _referencing_object_with_result(
    domain_object: CuratableObjectEnvelope,
    declared: Sequence[DomainPackFieldDefinition],
    decision: _ReferencedDecision,
    *,
    resolvable_fields: Mapping[str, ResolvableSpec],
    validated_references: Sequence[CuratableObjectEnvelope],
    objects_by_ref: Mapping[tuple[str, str], CuratableObjectEnvelope],
    fallback_text: tuple[str | None, str | None],
    event: Mapping[str, Any] | None,
) -> CuratableObjectEnvelope:
    ref_types = {
        field.object_type_ref
        for field in declared
        if field.field_type is DomainPackFieldType.OBJECT_REF and field.object_type_ref
    }
    payload = copy.deepcopy(domain_object.payload)
    containers: dict[str, list[str]] = {}
    for field_path in decision.values:
        container_path = _resolvable_container_path(
            payload, field_path, resolvable_fields=resolvable_fields
        )
        if container_path is not None:
            containers.setdefault(container_path, []).append(field_path)
    explanation, curator_message = (
        (decision.source.get(VALIDATOR_EXPLANATION_KEY), decision.source.get(VALIDATOR_CURATOR_MESSAGE_KEY))
        if decision.source is not None
        else fallback_text
    )

    source_overridden = decision.source is not None and is_curator_override(decision.source)
    for container_path, field_paths in containers.items():
        container = _payload_container(payload, container_path)
        spec = declared_spec_for(
            resolvable_fields, parse_field_path(container_path) if container_path else (),
        )
        identity_keys = tuple(dict.fromkeys([
            *(spec.identity_keys if spec is not None else ()),
            *(str(parse_field_path(path)[-1]) for path in field_paths),
        ]))
        if is_curator_override(container) and not source_overridden:
            # The value only follows its source: a curator override copied from the
            # source is withdrawn with it, and the value takes the source's state.
            container.pop(CURATOR_OVERRIDE_KEY, None)
            for key in identity_keys:
                container[key] = None
            container[RESOLUTION_STATE_KEY] = UNRESOLVED
            container[LOOKUP_OUTCOME_KEY] = OUTCOME_NOT_VALIDATED
        if decision.resolved:
            identity = {str(parse_field_path(path)[-1]): decision.values[path] for path in field_paths}
            if decision.source is not None:
                container.update(identity)
                copy_resolution(decision.source, container, identity_keys=identity_keys)
            else:
                mark_resolved(
                    container, identity, explanation=explanation, curator_message=curator_message,
                )
        else:
            mark_unresolved(
                container,
                str(decision.outcome),
                explanation=explanation,
                curator_message=curator_message,
                identity_keys=identity_keys,
            )
    if decision.resolved:
        for field_path, value in decision.values.items():
            if not any(field_path in paths for paths in containers.values()):
                _set_payload_value(payload, field_path, value)

    states = [
        _payload_container(payload, container_path).get(RESOLUTION_STATE_KEY)
        for container_path in containers
    ]
    object_refs = list(domain_object.object_refs)
    if decision.resolved and all(state == RESOLVED for state in states):
        # Link the validated reference carrying this identity; drop links to any other.
        object_refs = [
            ref
            for ref in object_refs
            if ref.object_type not in ref_types
            or (
                (linked := objects_by_ref.get(ref.ref_key())) is not None
                and _reference_matches(linked, decision)
            )
        ]
        existing_ref_keys = {ref.ref_key() for ref in object_refs}
        for reference in validated_references:
            object_ref = reference.to_object_ref()
            if (
                reference.object_type in ref_types
                and object_ref.ref_key() not in existing_ref_keys
                and _reference_matches(reference, decision)
            ):
                object_refs.append(object_ref)
                existing_ref_keys.add(object_ref.ref_key())
    elif states and all(state != RESOLVED for state in states):
        # A link to a validated reference is stale on a value left unresolved.
        object_refs = [ref for ref in object_refs if ref.object_type not in ref_types]
    if payload == domain_object.payload and object_refs == list(domain_object.object_refs):
        return domain_object
    if not decision.resolved or event is None or source_overridden:
        # Only a validator's resolution is a write-back event; a curator's identity is
        # recorded by the copied curator_override.
        return domain_object.model_copy(update={"payload": payload, "object_refs": object_refs})

    metadata = dict(domain_object.metadata)
    events = metadata.get(VALIDATOR_MATERIALIZATION_METADATA_KEY)
    metadata[VALIDATOR_MATERIALIZATION_METADATA_KEY] = [
        *(events if isinstance(events, list) else []),
        {**event, "materialized_field_paths": list(decision.values)},
    ]
    return domain_object.model_copy(
        update={"payload": payload, "object_refs": object_refs, "metadata": metadata}
    )


def _with_unresolved_values(
    envelope: DomainEnvelope,
    item: ValidatorResultMaterializationInput,
    target: CuratableObjectEnvelope,
    declared_fields: Mapping[str, DomainPackFieldDefinition],
    outcome: str,
    *,
    resolvable_fields: Mapping[str, ResolvableSpec] | None = None,
) -> DomainEnvelope:
    """Record why the resolvable values a binding writes stay unresolved.

    The state, lookup outcome and the validator's own words change; a value
    that read as resolved (e.g. a builder's deterministic lookup) keeps its
    identity only as informational ``overruled_*`` keys (a decisive outcome;
    the validator is the authority).
    ``mention`` is untouched, and plain fields keep whatever the extractor staged.
    """

    payload = copy.deepcopy(target.payload)
    for raw_field_path in item.request.expected_result_fields.values():
        if not isinstance(raw_field_path, str) or not raw_field_path.strip():
            continue
        materialized_field_path = _materialized_field_path(
            raw_field_path,
            declared_fields=declared_fields,
        )
        if materialized_field_path is None:
            continue
        container_path = _resolvable_container_path(
            payload, materialized_field_path, resolvable_fields=resolvable_fields
        )
        if container_path is None:
            continue
        container = _payload_container(payload, container_path)
        before = copy.deepcopy(container)
        mark_unresolved(
            container,
            outcome,
            explanation=item.result.explanation,
            curator_message=item.result.curator_message,
            identity_keys=_container_identity_keys(
                item, container_path, declared_fields=declared_fields, resolvable_fields=resolvable_fields,
            ),
        )
        if container == before:
            # A non-decisive outcome left a resolved value as it was; so do its mirrors.
            continue
        _propagate_materialized_resolution_state(
            payload, materialized_field_path, declared_fields=declared_fields,
            resolvable_fields=resolvable_fields,
        )
    if payload == target.payload:
        return envelope
    return _with_object_payload(envelope, target, payload)


def _with_object_payload(
    envelope: DomainEnvelope,
    target: CuratableObjectEnvelope,
    payload: dict[str, Any],
) -> DomainEnvelope:
    patched_target = target.model_copy(update={"payload": payload})
    objects = [
        patched_target if _same_object_identity(candidate, target) else candidate
        for candidate in envelope.extracted_objects
    ]
    return envelope.model_copy(update={"extracted_objects": objects})


def _resolvable_container_path(
    payload: Mapping[str, Any],
    field_path: str,
    *,
    resolvable_fields: Mapping[str, ResolvableSpec] | None = None,
) -> str | None:
    """The path of the declared resolvable value holding ``field_path`` ("" for the root), or None.

    Only the pack's declarations (``declared_resolvable_fields``) make a
    container a resolvable value, never the keys it happens to hold: an
    object root with a ``mention`` key is not a resolvable value unless its
    model declares it. A declared value stored before the contract (no state,
    no mention) is written as one too, so it takes the contract shape.
    """

    try:
        parts = parse_field_path(field_path)
    except ValueError:
        return None
    if not parts or not isinstance(parts[-1], str):
        return None
    container_path = _format_field_path(parts[:-1])
    container = _payload_container(payload, container_path)
    if (
        resolvable_fields
        and isinstance(container, dict)
        and declared_spec_for(resolvable_fields, parts[:-1]) is not None
    ):
        return container_path
    if has_resolution_state(container):
        # A value in the contract shape the pack does not declare: a plain
        # write into it would leave it unresolved forever.
        raise ResolvableValueError(
            f"{container_path or 'the object root'} holds a resolvable value the pack does not declare"
        )
    return None


def _container_identity_keys(
    item: ValidatorResultMaterializationInput,
    container_path: str,
    *,
    declared_fields: Mapping[str, DomainPackFieldDefinition],
    resolvable_fields: Mapping[str, ResolvableSpec] | None,
) -> tuple[str, ...]:
    """Every key a validator supplies for one value: its declared identity plus the
    keys this binding writes into it (so an overruled identity is fully cleared)."""

    keys: list[str] = []
    try:
        container_tokens = parse_field_path(container_path) if container_path else ()
    except ValueError:
        container_tokens = ()
    spec = declared_spec_for(resolvable_fields, container_tokens) if resolvable_fields else None
    if spec is not None:
        keys.extend(spec.identity_keys)
    for raw_field_path in item.request.expected_result_fields.values():
        if not isinstance(raw_field_path, str) or not raw_field_path.strip():
            continue
        materialized_field_path = _materialized_field_path(raw_field_path, declared_fields=declared_fields)
        if materialized_field_path is None:
            continue
        parts = parse_field_path(materialized_field_path)
        if isinstance(parts[-1], str) and _format_field_path(parts[:-1]) == container_path:
            keys.append(parts[-1])
    return tuple(dict.fromkeys(keys))


def _payload_container(payload: Mapping[str, Any], container_path: str) -> Any:
    if not container_path:
        return payload
    value = _payload_value(payload, container_path)
    return None if value is _MISSING else value


def _format_field_path(parts: Sequence[str | int]) -> str:
    text = ""
    for part in parts:
        if isinstance(part, int):
            text += f"[{part}]"
        else:
            text = f"{text}.{part}" if text else part
    return text


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

    for candidate in envelope.extracted_objects:
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
    resolved_objects = [
        raw_object
        for raw_object in result.resolved_objects
        if _looks_like_materializable_object(raw_object)
    ]
    if not resolved_objects:
        inferred_object, inference_problem = (
            _materializable_object_from_qualified_resolved_values(
                item,
                object_definitions=object_definitions,
                object_role_key=object_role_key,
            )
        )
        if inference_problem is not None:
            return [], inference_problem
        if inferred_object is None:
            return [], None
        resolved_objects = [inferred_object]

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


def _materializable_object_from_qualified_resolved_values(
    item: ValidatorResultMaterializationInput,
    *,
    object_definitions: Mapping[str, DomainPackObjectDefinition],
    object_role_key: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Build a validated reference when a binding names its target object type.

    Validator prompts define ``resolved_objects`` as database facts, so those objects
    do not necessarily carry the materializer-only ``canonical_id``/``payload``
    wrapper. A binding can instead declare qualified result paths such as
    ``allele.primary_external_id``. When that qualifier unambiguously names a
    validated-reference object and the resolved scalar values fill its required
    fields, the domain-pack contract contains enough information to materialize it
    without relying on the model to invent an internal envelope shape.
    """

    qualified_prefixes: set[str] = set()
    for result_field, raw_field_path in item.request.expected_result_fields.items():
        if missing_resolved_value(item.result.resolved_values.get(result_field)):
            continue
        if not isinstance(raw_field_path, str) or "." not in raw_field_path:
            return None, None
        prefix, _ = raw_field_path.strip().split(".", 1)
        if not prefix:
            return None, None
        qualified_prefixes.add(prefix.casefold())

    if len(qualified_prefixes) != 1:
        return None, None
    qualified_prefix = next(iter(qualified_prefixes))
    candidates = [
        definition
        for definition in object_definitions.values()
        if definition.object_type.casefold() == qualified_prefix
        and _definition_object_role(
            definition,
            object_role_key=object_role_key,
        )
        == "validated_reference"
    ]
    if len(candidates) != 1:
        return None, None

    object_definition = candidates[0]
    payload, problem = _validated_reference_payload(
        item,
        {},
        object_definition=object_definition,
    )
    if problem is not None:
        return None, problem

    canonical_id = _optional_string(payload.get("primary_external_id"))
    if canonical_id is None:
        return None, (
            "qualified resolved values for validated-reference object "
            f"{object_definition.object_type!r} do not include primary_external_id"
        )
    return {
        "object_type": object_definition.object_type,
        "canonical_id": canonical_id,
        "payload": payload,
    }, None


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


def _mirror_field_paths(
    materialized_field_path: str,
    *,
    declared_fields: Mapping[str, DomainPackFieldDefinition],
) -> list[str]:
    field_def = declared_fields.get(materialized_field_path)
    if field_def is None:
        return []
    mirror_paths = field_def.metadata.get("materializes_to_field_paths")
    if not isinstance(mirror_paths, list):
        return []
    return [
        _materialized_field_path(mirror_raw, declared_fields=declared_fields)
        or mirror_raw.strip()
        for mirror_raw in mirror_paths
        if isinstance(mirror_raw, str) and mirror_raw.strip()
    ]


def _propagate_materialized_mirror_paths(
    payload: dict[str, Any],
    materialized_field_path: str,
    resolved_value: Any,
    *,
    declared_fields: Mapping[str, DomainPackFieldDefinition],
    resolvable_fields: Mapping[str, ResolvableSpec] | None = None,
) -> None:
    """Copy a resolved value into the field's declared ``materializes_to_field_paths`` mirrors.

    The gene-expression domain pack declares that
    ``expression_annotation_subject.primary_external_id`` materializes to
    ``expression_experiment.entity_assayed.primary_external_id`` (and the gene_symbol pair), so a
    resolved subject gene must also land on ``entity_assayed`` to satisfy the LinkML
    "entity_assayed must match expression_annotation_subject" contract. The mirror targets are
    declared metadata, so this is domain-pack-driven, not gene-expression-specific code.
    A mirror that is itself a resolvable value takes the source value's resolution state.
    """
    for mirror_path in _mirror_field_paths(
        materialized_field_path, declared_fields=declared_fields
    ):
        if _payload_value(payload, mirror_path) != resolved_value:
            _set_payload_value(payload, mirror_path, resolved_value)
    _propagate_materialized_resolution_state(
        payload, materialized_field_path, declared_fields=declared_fields,
        resolvable_fields=resolvable_fields,
    )


def _propagate_materialized_resolution_state(
    payload: dict[str, Any],
    materialized_field_path: str,
    *,
    declared_fields: Mapping[str, DomainPackFieldDefinition],
    resolvable_fields: Mapping[str, ResolvableSpec] | None = None,
) -> None:
    """Give each resolvable mirror of a written field its source value's resolution."""

    source_path = _resolvable_container_path(
        payload, materialized_field_path, resolvable_fields=resolvable_fields
    )
    if source_path is None:
        return
    source = _payload_container(payload, source_path)
    for mirror_path in _mirror_field_paths(
        materialized_field_path, declared_fields=declared_fields
    ):
        mirror_container_path = _resolvable_container_path(
            payload, mirror_path, resolvable_fields=resolvable_fields
        )
        if mirror_container_path is None:
            continue
        mirror_spec = declared_spec_for(
            resolvable_fields or {},
            parse_field_path(mirror_container_path) if mirror_container_path else (),
        )
        copy_resolution(
            source,
            _payload_container(payload, mirror_container_path),
            identity_keys=mirror_spec.identity_keys if mirror_spec is not None else (),
        )


def _append_materialized_objects(
    envelope: DomainEnvelope,
    item: ValidatorResultMaterializationInput,
    new_objects: Sequence[CuratableObjectEnvelope],
) -> tuple[DomainEnvelope, tuple[CuratableObjectEnvelope, ...]]:
    if not new_objects:
        return envelope, ()

    existing_object_ids = {
        domain_object.object_id
        for domain_object in envelope.extracted_objects
        if domain_object.object_id is not None
    }
    objects = list(envelope.extracted_objects)
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

    return envelope.model_copy(update={"extracted_objects": objects}), tuple(appended)


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
    resolvable_fields_by_type: Mapping[str, Mapping[str, ResolvableSpec]],
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
            field_resolution = (
                _field_resolution_for(item, materialized_paths[0], target.declared_fields)
                if materialized_paths and item.result.field_resolutions
                else None
            )
            if (
                item.result.field_resolutions
                and field_resolution is None
                and materialized_paths
                and _resolvable_container_path(
                    target.domain_object.payload,
                    materialized_paths[0],
                    resolvable_fields=resolvable_fields_by_type.get(target.domain_object.object_type),
                ) is not None
            ):
                # A composite validator made no decision for this value: no write, no finding.
                continue
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
                        field_resolution=field_resolution,
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
    field_resolution: ValidatorFieldResolution | None = None,
) -> ValidationFinding:
    result = item.result
    resolved_value = result.resolved_values.get(result_field)
    result_field_missing = result_field in result.missing_expected_fields
    if field_resolution is not None:
        # A composite validator's own decision for this value (ALL-1299).
        resolved_value = field_resolution.resolved_values.get(result_field)
        resolved = field_resolution.status == "resolved" and not missing_resolved_value(
            resolved_value
        )
        outcome = (
            field_resolution.lookup_outcome
            if field_resolution.status == "unresolved"
            else OUTCOME_MISSING_EXPECTED_RESULT_FIELD
        )
        severity = (
            ValidationFindingSeverity.INFO
            if resolved
            else ValidationFindingSeverity.BLOCKER
            if item.match.binding.blocking
            else ValidationFindingSeverity.WARNING
        )
        status = ValidationFindingStatus.RESOLVED if resolved else ValidationFindingStatus.OPEN
        code = "domain_pack.validator_resolved" if resolved else "domain_pack.validator_unresolved"
        message = (
            field_resolution.curator_message
            or field_resolution.explanation
            or validator_finding.message
        )
        extra_details = {} if resolved else {"failure_classification": outcome}
    elif result.status == "resolved" and not (
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
    if status is ValidationFindingStatus.RESOLVED:
        # A resolved value carries no failure classification (a composite's
        # parent result may be unresolved while this value resolved).
        details.pop("failure_classification", None)
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
                {"dispatch_context": dict(item.dispatch_context)}
                if item.dispatch_context is not None
                else {}
            ),
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
                **({"coverage": payload["coverage"]} if payload.get("coverage") is not None else {}),
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
    for domain_object in envelope.extracted_objects:
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

    for domain_object in envelope.extracted_objects:
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
        for domain_object in envelope.extracted_objects
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
    value_reader: _ReviewValueReader | None,
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
        resolution = value_reader.resolution(field_path) if value_reader is not None else None
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
                metadata=_with_resolution_edit_policy(metadata, resolution),
                resolution=resolution,
            )
        )

    return summary_fields


def _workspace_fields(
    domain_object: CuratableObjectEnvelope,
    *,
    object_definition: DomainPackObjectDefinition | None,
    display_config: Mapping[str, Any],
    unavailable_capabilities_by_field: Mapping[tuple[str, str], tuple[dict[str, Any], ...]],
    value_reader: _ReviewValueReader | None,
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

        resolution = value_reader.resolution(field_path) if value_reader is not None else None
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
                metadata=_with_resolution_edit_policy(
                    {**metadata, "workspace_order": order}, resolution,
                ),
                resolution=resolution,
            )
        )

    return workspace_fields


def _is_empty_projection_value(value: Any) -> bool:
    if value is _MISSING or value is None:
        return True
    if isinstance(value, (str, list, tuple, dict, set)) and len(value) == 0:
        return True
    return False


# --- Extracted vs validated values on review fields (ALL-1283) -----------------

# Curator-facing; the technical detail goes to the log only.
_UNREADABLE_ISSUE = (
    "This stored value could not be read; please re-run validation or contact the "
    "AI Curation developers."
)
# A value's own leaves; the paper-wording key is the spec's mention key.
_VALUE_LEAF_KEYS = (
    RESOLUTION_STATE_KEY,
    LOOKUP_OUTCOME_KEY,
    VALIDATOR_EXPLANATION_KEY,
    VALIDATOR_CURATOR_MESSAGE_KEY,
)


@dataclass(frozen=True)
class _ValueReading:
    """One resolvable value as a review field reads it."""

    path: tuple[str | int, ...]
    spec: ResolvableSpec
    reviewed: DomainEnvelopeReviewResolvedValue
    # The read-time value (legacy and invalid-record rules applied).
    value: Mapping[str, Any]


@dataclass(frozen=True)
class _ReviewValueReader:
    """Reads one object's declared resolvable values for its review fields.

    ``payload`` is the object's read-time payload (``effective_payload``: the
    legacy rule applied and invalid stored records read as unresolved);
    ``readings`` holds each resolvable value; ``field_displays`` holds the
    declared display spec per field path.
    """

    payload: Mapping[str, Any]
    readings: tuple[_ValueReading, ...]
    field_displays: Mapping[str, Mapping[str, Any]]

    def resolution(self, field_path: str) -> DomainEnvelopeReviewFieldResolution | None:
        """The extracted-vs-validated reading of one review field, or None.

        A field that is, or contains, resolvable values reads through its
        declared display ("label (ID)" or UNRESOLVED per value). A field that
        is one identity key of a value (its id, label or a declared validated
        key such as a taxon) shows that key when the value is resolved and
        UNRESOLVED otherwise. A field that is one of the value's own leaves
        (paper wording, status, lookup result, validator text) shows that
        leaf in plain words. Paper wording, the extractor's ``proposed_*``
        keys and a validator's ``overruled_*`` keys never fill a validated
        value's text.
        """

        from src.lib.flows.value_display import display_text

        try:
            field_tokens = parse_field_path(field_path)
        except ValueError:
            return None
        key = field_tokens[-1]
        for reading in self.readings:
            if field_tokens[:-1] != reading.path or not isinstance(key, str):
                continue
            if key in reading.spec.identity_keys:
                resolved = reading.reviewed.resolution_state == RESOLVED
                return DomainEnvelopeReviewFieldResolution(
                    display_text=(
                        display_text(reading.value.get(key)) if resolved else UNRESOLVED_DISPLAY
                    ),
                    values=[reading.reviewed],
                )
            if key == reading.spec.mention_key or key in _VALUE_LEAF_KEYS:
                leaf_key = MENTION_KEY if key == reading.spec.mention_key else key
                return DomainEnvelopeReviewFieldResolution(
                    display_text=_value_leaf_text(reading.reviewed, leaf_key),
                    values=[reading.reviewed],
                    leaf_key=leaf_key,
                )

        contained = [
            reading
            for reading in self.readings
            if reading.path[: len(field_tokens)] == field_tokens
        ]
        if not contained:
            return None
        field_value = _payload_value(self.payload, field_path)
        return DomainEnvelopeReviewFieldResolution(
            display_text=display_text(
                None if field_value is _MISSING else field_value,
                self.field_displays.get(field_path),
            ),
            values=[reading.reviewed for reading in contained],
        )


def _with_resolution_edit_policy(
    metadata: dict[str, Any],
    resolution: DomainEnvelopeReviewFieldResolution | None,
) -> dict[str, Any]:
    """A value's own leaves (paper wording, status, lookup result, validator
    text) are set by extraction and validation, never edited by a curator.

    A curator edits a value's identity keys instead, which records a
    validation override (``resolvable_values.apply_curator_identity``).
    """

    if resolution is None or resolution.leaf_key is None:
        return metadata
    return {**metadata, "editable": False, "read_only": True}


def _value_leaf_text(reviewed: DomainEnvelopeReviewResolvedValue, leaf_key: str) -> str:
    """A value's own leaf in the plain words exports use (resolvable_values labels)."""

    if leaf_key == MENTION_KEY:
        return reviewed.mention or ""
    if leaf_key == RESOLUTION_STATE_KEY:
        return LEAF_VALUE_LABELS[RESOLUTION_STATE_KEY][reviewed.resolution_state]
    if leaf_key == LOOKUP_OUTCOME_KEY:
        return reviewed.lookup_result
    if leaf_key == VALIDATOR_EXPLANATION_KEY:
        return reviewed.validator_explanation or ""
    return reviewed.validator_curator_message or ""


def _review_value_display_source(metadata: DomainPackMetadata) -> Any:
    """The pack's declared display specs, as exports resolve them."""

    from src.lib.flows.export_fields import PackagedExportSource

    return PackagedExportSource(
        LoadedDomainPack(
            pack_id=metadata.pack_id,
            display_name=metadata.display_name,
            version=metadata.version,
            pack_path=Path("."),
            metadata_path=Path("."),
            metadata=metadata,
        )
    )


def _review_value_reader(
    domain_object: CuratableObjectEnvelope,
    stored_payload: Mapping[str, Any],
    specs: Mapping[str, ResolvableSpec],
    display_source: Any,
    *,
    envelope_id: str,
    envelope_revision: int,
    override_disagreements: Mapping[str, Sequence[str]],
    field_definitions: Mapping[str, DomainPackFieldDefinition],
) -> _ReviewValueReader | None:
    if not specs:
        return None
    # The row's read-time copy (effective_payload already applied).
    payload = domain_object.payload
    readings: dict[tuple[str | int, ...], tuple[int, _ValueReading]] = {}
    declared = list(specs.items())
    # The most specific declaration reads a value declared both as a list and
    # as one of its elements, as effective_payload does; readings keep the
    # pack's declaration order, then payload order.
    for order, (declared_path, spec) in sorted(
        enumerate(declared), key=lambda item: -len(item[1][0]),
    ):
        try:
            tokens = parse_field_path(declared_path) if declared_path else ()
        except ValueError:
            continue
        for value_path in _concrete_value_paths(payload, tokens, ()):
            if value_path in readings or not has_resolution_state(
                _payload_container(payload, _format_field_path(value_path))
            ):
                # An empty value (nothing the legacy rule reads) has no reading.
                continue
            readings[value_path] = (
                order,
                _read_review_value(
                    domain_object,
                    stored_payload,
                    value_path,
                    spec,
                    envelope_id=envelope_id,
                    envelope_revision=envelope_revision,
                    override_disagreements=override_disagreements.get(
                        _format_field_path(value_path), (),
                    ),
                    field_definitions=field_definitions,
                ),
            )
    ordered = [
        reading
        for _order, reading in sorted(readings.values(), key=lambda item: item[0])
    ]
    prefix = f"object.pack.{domain_object.object_type}."
    return _ReviewValueReader(
        payload=payload,
        readings=tuple(ordered),
        field_displays={
            ref[len(prefix):]: spec
            for ref, spec in display_source.display_specs.items()
            if ref.startswith(prefix)
        },
    )


def _read_review_value(
    domain_object: CuratableObjectEnvelope,
    stored_payload: Mapping[str, Any],
    value_path: tuple[str | int, ...],
    spec: ResolvableSpec,
    *,
    envelope_id: str,
    envelope_revision: int,
    override_disagreements: Sequence[str],
    field_definitions: Mapping[str, DomainPackFieldDefinition],
) -> _ValueReading:
    """Read one value from the read-time payload; a broken stored record says so plainly."""

    from src.lib.domain_envelopes.patches import curator_override_allowed, is_generic_attribute_path
    from src.lib.flows.value_display import display_text

    path_text = _format_field_path(value_path)
    # The value as the row's read-time copy holds it: the legacy rule already
    # read it once (a plain-text value became legacy text), never again here.
    value = _payload_container(domain_object.payload, path_text)
    raw = _payload_container(stored_payload, path_text)
    # The legacy rule read a broken stored record as an invalid-record reading;
    # validator words that are not text are broken too.
    problem = (
        (stored_state_problem(raw, identity_keys=spec.identity_keys) if has_resolution_state(raw) else None)
        or "the stored validation record is invalid"
        if value.get(VALIDATOR_EXPLANATION_KEY) == INVALID_RECORD_EXPLANATION
        else next(
            (
                f"{text_key} must be text or null"
                for text_key in (VALIDATOR_EXPLANATION_KEY, VALIDATOR_CURATOR_MESSAGE_KEY)
                if value.get(text_key) is not None and not isinstance(value.get(text_key), str)
            ),
            None,
        )
    )
    broken = problem is not None
    if broken:
        logger.warning(
            "Review row reads an unreadable resolvable value as unresolved: "
            "envelope_id=%s envelope_revision=%s object_id=%s object_type=%s value_path=%r: %s",
            envelope_id, envelope_revision, stable_object_id(domain_object),
            domain_object.object_type, path_text, problem,
        )
    state, outcome = str(value[RESOLUTION_STATE_KEY]), str(value[LOOKUP_OUTCOME_KEY])
    mention = value.get(spec.mention_key)
    override = value.get(CURATOR_OVERRIDE_KEY) if not broken and is_curator_override(value) else None
    return _ValueReading(
        path=value_path,
        spec=spec,
        reviewed=DomainEnvelopeReviewResolvedValue(
            value_path=path_text,
            display_text=display_text(
                value,
                {
                    role: key
                    for role, key in (
                        ("label", spec.label_key),
                        ("id", spec.id_key),
                        ("mention", spec.mention_key),
                    )
                    if key
                }
                | ({"validated": list(spec.validated_keys)} if spec.validated_keys else {}),
            ),
            mention=mention.strip() if isinstance(mention, str) and mention.strip() else None,
            resolution_state=UNRESOLVED if broken else state,
            lookup_outcome=OUTCOME_INVALID_SCHEMA if broken else outcome,
            lookup_result=LOOKUP_OUTCOME_LABELS[OUTCOME_INVALID_SCHEMA if broken else outcome],
            validator_explanation=None if broken else value.get(VALIDATOR_EXPLANATION_KEY),
            validator_curator_message=None if broken else value.get(VALIDATOR_CURATOR_MESSAGE_KEY),
            issue=_UNREADABLE_ISSUE if broken else None,
            curator_override=(
                DomainEnvelopeReviewCuratorOverride(
                    actor_id=str(override["actor_id"]),
                    actor_display_name=str(override["actor_display_name"]),
                    at=str(override["at"]),
                )
                if override is not None
                else None
            ),
            override_disagreements=list(override_disagreements) if override is not None else [],
            identity_field_paths=[
                f"{path_text}.{key}" if path_text else key for key in spec.identity_keys
            ],
            id_key=spec.id_key,
            label_key=spec.label_key,
            validated_keys=list(spec.validated_keys),
            # As stored (a legacy identity included; null for a value stored as plain text).
            stored_identity={
                key: copy.deepcopy(raw.get(key)) if isinstance(raw, Mapping) else None
                for key in spec.identity_keys
            },
            # The value as stored, for the edits whose `before` is the whole value: a
            # saved profile's attribute value (a whole-value replace) and a list
            # element (a removal).
            stored_value=(
                copy.deepcopy(dict(raw))
                if isinstance(raw, Mapping)
                and path_text
                and (is_generic_attribute_path(path_text) or isinstance(value_path[-1], int))
                else None
            ),
            **curator_override_allowed(field_definitions, path_text, spec),
        ),
        value=value,
    )


def _open_override_disagreements(envelope: DomainEnvelope) -> dict[str, dict[str, list[str]]]:
    """Open validator-disagrees-with-override warnings: {object id: {value path: [messages]}}.

    The warning names the overridden value's payload path, or the object
    itself for a resolvable object root (value path "").
    """

    object_id_by_ref = _object_id_by_ref(envelope)
    disagreements: dict[str, dict[str, list[str]]] = {}
    for finding in envelope.validation_findings:
        if (
            finding.code != CURATOR_OVERRIDE_DISAGREEMENT_CODE
            or finding.status is not ValidationFindingStatus.OPEN
        ):
            continue
        object_id, field_path = _finding_target(finding, object_id_by_ref)
        if object_id is None:
            continue
        disagreements.setdefault(object_id, {}).setdefault(field_path or "", []).append(finding.message)
    return disagreements

def _concrete_value_paths(
    node: Any,
    tokens: Sequence[str | int],
    walked: tuple[str | int, ...],
) -> Iterable[tuple[str | int, ...]]:
    """Payload paths of the mappings a declared path names; unindexed lists fan out."""

    if tokens and isinstance(tokens[0], int):
        if isinstance(node, list) and tokens[0] < len(node):
            yield from _concrete_value_paths(node[tokens[0]], tokens[1:], (*walked, tokens[0]))
        return
    if isinstance(node, list):
        for index, item in enumerate(node):
            yield from _concrete_value_paths(item, tokens, (*walked, index))
        return
    if not isinstance(node, Mapping):
        return
    if not tokens:
        yield walked
        return
    if tokens[0] in node:
        yield from _concrete_value_paths(node[tokens[0]], tokens[1:], (*walked, tokens[0]))

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
    profile_context: ProfileValidationContext | None = None,
) -> dict[str, Any]:
    if profile_context is not None:
        from .profile_validation import profile_mapping_binding_id
        capabilities = tuple({
            "validator_binding_id": profile_mapping_binding_id(profile_context.profile, item.mapping),
            "state": "unavailable", "label": item.mapping.mapping_id,
            "state_explanation": "; ".join(item.reasons), "scope": "object",
            "object_type": "generic_object", "affected_fields": list(item.mapping.outputs.values()),
            "generic_profile_ref": profile_context.profile.receipt,
            "profile_validator_mapping": item.mapping.model_dump(mode="json"),
        } for item in profile_context.unavailable)
        return {"global": (), "by_object": {stable_object_id(obj): capabilities for obj in envelope.extracted_objects},
                "by_field": {}}
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
    display_config: Mapping[str, Any],
    resolvable_fields: Mapping[str, ResolvableSpec],
) -> str:
    """The row's label: the pack's single primary_label_field, else the object id.

    No other field fills an empty label. A label that names an unresolved
    value reads as its paper wording, labelled as such (ALL-1283).
    """

    configured_field = display_config.get("primary_label_field")
    if isinstance(configured_field, str) and configured_field.strip():
        label = _declared_label_text(domain_object, configured_field.strip(), resolvable_fields)
        if label is not None:
            return label
    return stable_object_id(domain_object)


def _secondary_label(
    domain_object: CuratableObjectEnvelope,
    *,
    display_config: Mapping[str, Any],
    resolvable_fields: Mapping[str, ResolvableSpec],
) -> str | None:
    configured_field = display_config.get("secondary_label_field")
    if isinstance(configured_field, str) and configured_field.strip():
        return _declared_label_text(domain_object, configured_field.strip(), resolvable_fields)
    return None


def _declared_label_text(
    domain_object: CuratableObjectEnvelope,
    field_path: str,
    resolvable_fields: Mapping[str, ResolvableSpec],
) -> str | None:
    paper_wording = unresolved_header_text(
        domain_object.payload,
        field_path,
        object_metadata=domain_object.metadata,
        resolvable_fields=resolvable_fields,
    )
    if paper_wording is not None:
        return paper_wording
    configured_value = _payload_value(domain_object.payload, field_path)
    if configured_value is _MISSING:
        return None
    return _display_value(configured_value)


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
        for domain_object in envelope.extracted_objects
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
    finding_id = validation_finding_projection_id(
        envelope=envelope,
        envelope_revision=envelope_revision,
        finding=finding,
        finding_index=finding_index,
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


def validation_finding_projection_id(
    *,
    envelope: DomainEnvelope,
    envelope_revision: int,
    finding: ValidationFinding,
    finding_index: int,
) -> str:
    """Return the authored finding ID or its stable projection identity."""

    return finding.finding_id or _projection_id(
        "validation-finding",
        envelope.envelope_id,
        envelope_revision,
        finding_index,
        finding.model_dump(mode="json"),
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
    for domain_object in envelope.extracted_objects:
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
