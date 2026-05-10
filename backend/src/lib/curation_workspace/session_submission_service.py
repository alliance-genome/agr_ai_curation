"""Submission preview, execution, retry, and history service."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.lib.http_errors import raise_sanitized_http_exception
from src.lib.curation_workspace.export_adapters import build_default_export_adapter_registry
from src.lib.curation_workspace.models import (
    CurationActionLogEntry as SessionActionLogModel,
    CurationCandidate,
    CurationReviewSession as ReviewSessionModel,
    CurationSubmissionRecord as SubmissionModel,
    DomainEnvelopeModel,
    DomainEnvelopeProjectionIndex,
)
from src.lib.curation_workspace.session_common import (
    _actor_claims_payload,
    _normalize_uuid,
    _normalized_optional_string,
)
from src.lib.curation_workspace.session_queries import get_session_detail
from src.lib.curation_workspace.session_serializers import (
    _action_log_entry,
    _candidate_payload,
    _document_ref,
    _draft_detail,
    _serialize_submission_payload_contract,
    _submission_payload,
    _submission_payload_model_input,
    _submission_record,
)
from src.lib.curation_workspace.session_validation_service import (
    _load_session_for_validation,
    validate_session,
)
from src.lib.curation_workspace.submission_adapters import (
    DIRECT_SUBMISSION_RESULT_STATUSES,
    SubmissionTransportAdapter,
    SubmissionTransportError,
    SubmissionTransportResult,
    build_default_submission_adapter_registry,
    coerce_submission_transport_result,
    normalize_submission_transport_result,
)
from src.lib.curation_workspace.validation_runtime import dedupe
from src.lib.domain_packs.registry import LoadedDomainPack, load_domain_pack_registry
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    FieldValidationPolicy,
)
from src.models.sql.pdf_document import PDFDocument
from src.schemas.curation_workspace import (
    CurationActionLogEntry,
    CurationActionType,
    CurationActorType,
    CurationCandidateStatus,
    CurationCandidateSubmissionReadiness,
    CurationDraftField as CurationDraftFieldSchema,
    CurationSessionStatus,
    CurationSessionValidationRequest,
    CurationSubmissionExecuteRequest,
    CurationSubmissionExecuteResponse,
    CurationSubmissionHistoryResponse,
    CurationSubmissionPreviewRequest,
    CurationSubmissionPreviewResponse,
    CurationSubmissionRecord,
    CurationSubmissionRetryRequest,
    CurationSubmissionRetryResponse,
    CurationSubmissionStatus,
    CurationSubmissionReadinessBlocker,
    CurationValidationSnapshot as CurationValidationSnapshotSchema,
    FieldValidationResult,
    SubmissionDomainAdapter,
    SubmissionMode,
    SubmissionPayloadContract,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DefinitionState,
    DomainEnvelope,
    ValidationFinding,
    ValidationFindingSeverity,
    ValidationFindingStatus,
    field_path_exists,
    parse_field_path,
)
from src.schemas.domain_pack_metadata import (
    DomainPackFieldDefinition,
    DomainPackObjectDefinition,
)

logger = logging.getLogger(__name__)
SUBMISSION_TRANSPORT_FAILURE_MESSAGE = "Submission failed unexpectedly. Please try again."

_DOMAIN_BLOCKER_SEVERITIES = {
    ValidationFindingSeverity.ERROR,
    ValidationFindingSeverity.BLOCKER,
}
_BLOCKING_EXPORT_STATUSES = {"blocked", "not_supported", "unsupported"}
_NON_EXPORTABLE_FLAGS = ("exportable", "submit", "submittable")
_MISSING = object()


@lru_cache(maxsize=1)
def _export_adapter_registry():
    return build_default_export_adapter_registry()


@lru_cache(maxsize=1)
def _submission_adapter_registry():
    return build_default_submission_adapter_registry()


@dataclass(frozen=True)
class _DomainEnvelopeObjectContext:
    candidate_id: str
    envelope_row: DomainEnvelopeModel | None
    envelope: DomainEnvelope | None
    domain_object: CuratableObjectEnvelope | None
    object_definition: DomainPackObjectDefinition | None = None
    field_definitions: dict[str, DomainPackFieldDefinition] = field(default_factory=dict)
    field_policies: dict[str, FieldValidationPolicy] = field(default_factory=dict)
    projection_refs: tuple[dict[str, Any], ...] = ()
    blockers: tuple[CurationSubmissionReadinessBlocker, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _DomainEnvelopeSubmissionContext:
    object_contexts: dict[str, _DomainEnvelopeObjectContext] = field(default_factory=dict)
    envelope_snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)

    def blockers_for(self, candidate_id: str) -> tuple[CurationSubmissionReadinessBlocker, ...]:
        context = self.object_contexts.get(candidate_id)
        return context.blockers if context is not None else ()

    def warnings_for(self, candidate_id: str) -> tuple[str, ...]:
        context = self.object_contexts.get(candidate_id)
        return context.warnings if context is not None else ()

    def has_domain_candidate(self, candidate_id: str) -> bool:
        return candidate_id in self.object_contexts


def _load_submission_record(
    db: Session,
    *,
    session_id: str | UUID,
    submission_id: str | UUID,
) -> SubmissionModel:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    normalized_submission_id = _normalize_uuid(submission_id, field_name="submission_id")
    submission_row = db.scalars(
        select(SubmissionModel)
        .where(SubmissionModel.id == normalized_submission_id)
        .where(SubmissionModel.session_id == normalized_session_id)
    ).first()
    if submission_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Curation submission {normalized_submission_id} not found in session "
                f"{normalized_session_id}"
            ),
        )
    return submission_row

def _submission_validation_blocking_reason(
    field: CurationDraftFieldSchema | None,
    validation_result: FieldValidationResult,
) -> str | None:
    if _field_validation_is_warning_only(field):
        return None

    field_label = field.label if field is not None else "A submission field"

    if validation_result.status == "invalid_format":
        return f"{field_label} is empty or invalid."
    if validation_result.status == "ambiguous":
        return f"{field_label} is still ambiguous."
    if validation_result.status == "not_found":
        return f"{field_label} could not be resolved."
    if validation_result.status == "conflict":
        return f"{field_label} has conflicting validation results."

    return None


def _field_validation_is_warning_only(
    field: CurationDraftFieldSchema | None,
) -> bool:
    if field is None:
        return False

    validation_config = field.metadata.get("validation")
    if not isinstance(validation_config, Mapping):
        return False

    severity = validation_config.get("severity")
    return isinstance(severity, str) and severity.strip().lower() == "warning"


def _domain_envelope_candidate(candidate: CurationCandidate) -> bool:
    return (
        candidate.envelope_id is not None
        and candidate.object_id is not None
        and candidate.envelope_revision is not None
    )


def _stable_object_id(domain_object: CuratableObjectEnvelope) -> str:
    if domain_object.object_id is not None:
        return domain_object.object_id
    if domain_object.pending_ref_id is not None:
        return domain_object.pending_ref_id
    raise ValueError("Domain envelope object is missing object_id and pending_ref_id")


def _payload_value(payload: Mapping[str, Any], field_path: str) -> Any:
    current: Any = payload
    for part in parse_field_path(field_path):
        if isinstance(part, str):
            if not isinstance(current, Mapping) or part not in current:
                return _MISSING
            current = current[part]
            continue
        if (
            not isinstance(current, Sequence)
            or isinstance(current, (str, bytes, bytearray))
            or part >= len(current)
        ):
            return _MISSING
        current = current[part]
    return current


def _value_missing_or_blank(value: Any) -> bool:
    if value is _MISSING or value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _object_id_by_ref(envelope: DomainEnvelope) -> dict[tuple[str, str], str]:
    object_id_by_ref: dict[tuple[str, str], str] = {}
    for domain_object in envelope.objects:
        stable_object_id = _stable_object_id(domain_object)
        if domain_object.object_id is not None:
            object_id_by_ref[("object_id", domain_object.object_id)] = stable_object_id
        if domain_object.pending_ref_id is not None:
            object_id_by_ref[("pending_ref_id", domain_object.pending_ref_id)] = (
                stable_object_id
            )
    return object_id_by_ref


def _finding_target(
    finding: ValidationFinding,
    object_id_by_ref: Mapping[tuple[str, str], str],
) -> tuple[str | None, str | None]:
    if finding.field_ref is not None:
        return (
            object_id_by_ref.get(finding.field_ref.object_ref.ref_key()),
            finding.field_ref.field_path,
        )
    if finding.object_ref is not None:
        return object_id_by_ref.get(finding.object_ref.ref_key()), None
    return None, None


def _readiness_blocker(
    *,
    envelope_id: str,
    object_id: str | None,
    field_path: str | None,
    severity: str,
    status_value: str,
    code: str,
    message: str,
    provider_refs: Mapping[str, Any] | None = None,
    projection_ref: Mapping[str, Any] | None = None,
    details: Mapping[str, Any] | None = None,
) -> CurationSubmissionReadinessBlocker:
    return CurationSubmissionReadinessBlocker(
        envelope_id=envelope_id,
        object_id=object_id,
        field_path=field_path,
        severity=severity,
        status=status_value,
        code=code,
        message=message,
        provider_refs=dict(provider_refs or {}),
        projection_ref=dict(projection_ref or {}),
        details=dict(details or {}),
    )


def _projection_ref_payload(row: DomainEnvelopeProjectionIndex) -> dict[str, Any]:
    return {
        "envelope_id": row.envelope_id,
        "object_id": row.object_id,
        "envelope_revision": row.envelope_revision,
        "projection_type": row.projection_type,
        "projection_key": row.projection_key,
        "projection_status": row.projection_status,
        "schema_provider": row.schema_provider,
        "schema_ref": dict(row.schema_ref_json or {}),
        "object_model_ref": dict(row.object_model_ref_json or {}),
        "model_field_ref": dict(row.model_field_ref_json or {}),
    }


def _projection_refs_by_object(
    db: Session,
    *,
    envelope_ids: Sequence[str],
) -> dict[tuple[str, str], tuple[dict[str, Any], ...]]:
    if not envelope_ids:
        return {}
    projection_rows = db.scalars(
        select(DomainEnvelopeProjectionIndex).where(
            DomainEnvelopeProjectionIndex.envelope_id.in_(list(dict.fromkeys(envelope_ids)))
        )
    ).all()
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in projection_rows:
        grouped.setdefault((row.envelope_id, row.object_id), []).append(
            _projection_ref_payload(row)
        )
    return {
        key: tuple(
            sorted(
                values,
                key=lambda item: (
                    str(item.get("projection_type") or ""),
                    str(item.get("projection_key") or ""),
                ),
            )
        )
        for key, values in grouped.items()
    }


def _loaded_domain_pack_for_envelope(envelope: DomainEnvelope) -> LoadedDomainPack | None:
    return load_domain_pack_registry().get_pack(envelope.domain_pack_id)


def _field_definitions_for(
    object_definition: DomainPackObjectDefinition | None,
) -> dict[str, DomainPackFieldDefinition]:
    if object_definition is None:
        return {}
    return {
        field_definition.field_path: field_definition
        for field_definition in object_definition.fields
    }


def _metadata_provider_refs(*metadata_items: Mapping[str, Any]) -> dict[str, Any]:
    provider_refs: dict[str, Any] = {}
    for metadata in metadata_items:
        raw_provider_refs = metadata.get("provider_refs")
        if isinstance(raw_provider_refs, Mapping):
            provider_refs.update(dict(raw_provider_refs))
    return provider_refs


def _export_behavior_for(
    domain_object: CuratableObjectEnvelope,
    object_definition: DomainPackObjectDefinition | None,
) -> dict[str, Any]:
    behavior: dict[str, Any] = {}
    for metadata in (
        object_definition.metadata if object_definition is not None else {},
        domain_object.metadata,
    ):
        raw_behavior = metadata.get("export_behavior")
        if isinstance(raw_behavior, Mapping):
            behavior.update(dict(raw_behavior))
    return behavior


def _metadata_allows_curator_override(metadata: Mapping[str, Any]) -> bool:
    if metadata.get("allow_curator_override") is True:
        return True
    if metadata.get("allow_override") is True:
        return True
    if metadata.get("allow_opt_out") is True:
        return True
    for key in ("curator_override", "override", "validation"):
        raw_policy = metadata.get(key)
        if isinstance(raw_policy, Mapping) and (
            raw_policy.get("allowed") is True
            or raw_policy.get("allow") is True
            or raw_policy.get("allow_curator_override") is True
            or raw_policy.get("allow_opt_out") is True
        ):
            return True
    return False


def _metadata_override_requires_reason(metadata: Mapping[str, Any]) -> bool:
    if metadata.get("opt_out_reason_required") is True:
        return True
    for key in ("curator_override", "override", "validation"):
        raw_policy = metadata.get(key)
        if isinstance(raw_policy, Mapping) and (
            raw_policy.get("reason_required") is True
            or raw_policy.get("opt_out_reason_required") is True
        ):
            return True
    return False


def _field_allows_curator_override(
    field_definition: DomainPackFieldDefinition | None,
    field_policy: FieldValidationPolicy | None = None,
) -> bool:
    if (
        field_definition is not None
        and _metadata_allows_curator_override(field_definition.metadata)
    ):
        return True
    return field_policy is not None and field_policy.allow_opt_out


def _field_override_requires_reason(
    field_definition: DomainPackFieldDefinition | None,
    field_policy: FieldValidationPolicy | None = None,
) -> bool:
    if (
        field_definition is not None
        and _metadata_override_requires_reason(field_definition.metadata)
    ):
        return True
    return (
        field_policy is not None
        and field_policy.allow_opt_out
        and field_policy.opt_out_reason_required
    )


def _curator_override_for_field(
    domain_object: CuratableObjectEnvelope,
    field_path: str,
) -> Mapping[str, Any] | None:
    raw_overrides = domain_object.metadata.get("curator_overrides")
    if isinstance(raw_overrides, Mapping):
        raw_override = raw_overrides.get(field_path)
        if isinstance(raw_override, Mapping):
            return raw_override
    if isinstance(raw_overrides, Sequence) and not isinstance(
        raw_overrides, (str, bytes, bytearray)
    ):
        for raw_override in raw_overrides:
            if (
                isinstance(raw_override, Mapping)
                and raw_override.get("field_path") == field_path
            ):
                return raw_override

    raw_override = domain_object.metadata.get("curator_override")
    if isinstance(raw_override, Mapping) and raw_override.get("field_path") == field_path:
        return raw_override
    return None


def _curator_override_satisfies_policy(
    *,
    domain_object: CuratableObjectEnvelope,
    field_definition: DomainPackFieldDefinition | None,
    field_policy: FieldValidationPolicy | None = None,
    field_path: str,
) -> bool:
    override = _curator_override_for_field(domain_object, field_path)
    if override is None or not _field_allows_curator_override(
        field_definition,
        field_policy,
    ):
        return False
    if not _field_override_requires_reason(field_definition, field_policy):
        return True
    reason = override.get("reason") or override.get("opt_out_reason")
    return isinstance(reason, str) and bool(reason.strip())


def _definition_state_blockers(
    *,
    envelope: DomainEnvelope,
    object_id: str,
    domain_object: CuratableObjectEnvelope,
    object_definition: DomainPackObjectDefinition | None,
    projection_ref: Mapping[str, Any],
) -> list[CurationSubmissionReadinessBlocker]:
    blockers: list[CurationSubmissionReadinessBlocker] = []
    targets: list[tuple[str, DefinitionState, str, Mapping[str, Any]]] = []
    if envelope.schema_ref is not None:
        targets.append(
            (
                "domain_envelope.schema_ref",
                envelope.schema_ref.definition_state,
                "Envelope schema definition is not stable for export.",
                envelope.schema_ref.metadata,
            )
        )
    targets.append(
        (
            "domain_envelope.object",
            domain_object.definition_state,
            "Domain envelope object definition is not stable for export.",
            domain_object.metadata,
        )
    )
    if object_definition is not None:
        targets.append(
            (
                "domain_pack.object_definition",
                object_definition.definition_state,
                "Domain-pack object definition is not stable for export.",
                object_definition.metadata,
            )
        )

    for source, definition_state, message, metadata in targets:
        if definition_state is DefinitionState.STABLE:
            continue
        blockers.append(
            _readiness_blocker(
                envelope_id=envelope.envelope_id,
                object_id=object_id,
                field_path=None,
                severity=ValidationFindingSeverity.BLOCKER.value,
                status_value="definition_state",
                code="domain_envelope.definition_state_blocked",
                message=message,
                provider_refs=_metadata_provider_refs(metadata),
                projection_ref=projection_ref,
                details={
                    "definition_state": definition_state.value,
                    "source": source,
                },
            )
        )
    return blockers


def _export_behavior_blockers(
    *,
    envelope: DomainEnvelope,
    object_id: str,
    domain_object: CuratableObjectEnvelope,
    object_definition: DomainPackObjectDefinition | None,
    projection_ref: Mapping[str, Any],
) -> list[CurationSubmissionReadinessBlocker]:
    behavior = _export_behavior_for(domain_object, object_definition)
    if not behavior:
        return []

    blockers: list[CurationSubmissionReadinessBlocker] = []
    status_value = str(behavior.get("status") or behavior.get("mode") or "").strip().lower()
    flags_block_export = any(behavior.get(flag) is False for flag in _NON_EXPORTABLE_FLAGS)
    if status_value in _BLOCKING_EXPORT_STATUSES or flags_block_export:
        reason = str(behavior.get("reason") or "Domain-pack export policy blocks this object.")
        blockers.append(
            _readiness_blocker(
                envelope_id=envelope.envelope_id,
                object_id=object_id,
                field_path=None,
                severity=ValidationFindingSeverity.BLOCKER.value,
                status_value="blocked",
                code="domain_envelope.export_behavior_blocked",
                message=reason,
                provider_refs=_metadata_provider_refs(domain_object.metadata),
                projection_ref=projection_ref,
                details={"export_behavior": behavior},
            )
        )

    raw_required_context = behavior.get("required_export_context_fields") or ()
    if isinstance(raw_required_context, Sequence) and not isinstance(
        raw_required_context, (str, bytes, bytearray)
    ):
        for field_path in raw_required_context:
            if not isinstance(field_path, str) or not field_path.strip():
                continue
            normalized_field_path = field_path.strip()
            value = (
                _payload_value(domain_object.payload, normalized_field_path)
                if field_path_exists(domain_object.payload, normalized_field_path)
                else _MISSING
            )
            if _value_missing_or_blank(value):
                blockers.append(
                    _readiness_blocker(
                        envelope_id=envelope.envelope_id,
                        object_id=object_id,
                        field_path=normalized_field_path,
                        severity=ValidationFindingSeverity.BLOCKER.value,
                        status_value="missing_host_context",
                        code="domain_envelope.missing_export_context",
                        message=(
                            "Required export host context is missing: "
                            f"{normalized_field_path}."
                        ),
                        provider_refs=_metadata_provider_refs(domain_object.metadata),
                        projection_ref=projection_ref,
                        details={"export_behavior": behavior},
                    )
                )
    return blockers


def _pack_export_policy_blockers(
    *,
    domain_pack: LoadedDomainPack | None,
    envelope: DomainEnvelope,
    object_id: str,
    projection_ref: Mapping[str, Any],
) -> list[CurationSubmissionReadinessBlocker]:
    if domain_pack is None:
        return [
            _readiness_blocker(
                envelope_id=envelope.envelope_id,
                object_id=object_id,
                field_path=None,
                severity=ValidationFindingSeverity.BLOCKER.value,
                status_value="missing_domain_pack",
                code="domain_envelope.domain_pack_unavailable",
                message="Domain-pack metadata is not available for export readiness checks.",
                projection_ref=projection_ref,
                details={"domain_pack_id": envelope.domain_pack_id},
            )
        ]

    raw_policy = domain_pack.metadata.metadata.get("export_blocker_policy")
    if not isinstance(raw_policy, Mapping):
        return []
    if str(raw_policy.get("status") or "").strip().lower() not in _BLOCKING_EXPORT_STATUSES:
        return []
    return [
        _readiness_blocker(
            envelope_id=envelope.envelope_id,
            object_id=object_id,
            field_path=None,
            severity=ValidationFindingSeverity.BLOCKER.value,
            status_value="blocked",
            code="domain_envelope.pack_export_policy_blocked",
            message=str(
                raw_policy.get("reason")
                or "Domain-pack export policy blocks this envelope."
            ),
            projection_ref=projection_ref,
            details={"export_blocker_policy": dict(raw_policy)},
        )
    ]


def _field_policy_blockers(
    *,
    envelope: DomainEnvelope,
    object_id: str,
    domain_object: CuratableObjectEnvelope,
    field_definitions: Mapping[str, DomainPackFieldDefinition],
    field_policies: Mapping[str, FieldValidationPolicy],
    projection_ref: Mapping[str, Any],
) -> tuple[list[CurationSubmissionReadinessBlocker], list[str]]:
    blockers: list[CurationSubmissionReadinessBlocker] = []
    warnings: list[str] = []

    for field_path, policy in sorted(field_policies.items()):
        field_definition = field_definitions.get(field_path)
        field_is_gate = policy.required or policy.export_blocking
        if not field_is_gate and policy.definition_state is DefinitionState.STABLE:
            continue

        provider_refs = dict(policy.provider_refs or {})
        if field_definition is not None:
            provider_refs.update(_metadata_provider_refs(field_definition.metadata))

        if field_is_gate and policy.definition_state is not DefinitionState.STABLE:
            blockers.append(
                _readiness_blocker(
                    envelope_id=envelope.envelope_id,
                    object_id=object_id,
                    field_path=field_path,
                    severity=ValidationFindingSeverity.BLOCKER.value,
                    status_value="definition_state",
                    code="domain_envelope.field_definition_state_blocked",
                    message=(
                        "Domain-pack field definition is not stable for export: "
                        f"{field_path}."
                    ),
                    provider_refs=provider_refs,
                    projection_ref=projection_ref,
                    details=policy.identity_details(),
                )
            )

        if not field_is_gate:
            continue

        field_value = (
            _payload_value(domain_object.payload, field_path)
            if field_path_exists(domain_object.payload, field_path)
            else _MISSING
        )
        if not _value_missing_or_blank(field_value):
            continue

        if _curator_override_satisfies_policy(
            domain_object=domain_object,
            field_definition=field_definition,
            field_policy=policy,
            field_path=field_path,
        ):
            warnings.append(
                f"Curator override accepted for export-blocking field {field_path}."
            )
            continue

        override_exists = _curator_override_for_field(domain_object, field_path) is not None
        code = (
            "domain_envelope.curator_override_not_allowed"
            if override_exists
            else "domain_envelope.required_field_missing"
        )
        message = (
            f"Curator override is not allowed for export-blocking field {field_path}."
            if override_exists
            else f"Required export field is missing: {field_path}."
        )
        blockers.append(
            _readiness_blocker(
                envelope_id=envelope.envelope_id,
                object_id=object_id,
                field_path=field_path,
                severity=ValidationFindingSeverity.BLOCKER.value,
                status_value="open",
                code=code,
                message=message,
                provider_refs=provider_refs,
                projection_ref=projection_ref,
                details=policy.identity_details(),
            )
        )

    return blockers, warnings


def _validation_finding_blockers(
    *,
    envelope: DomainEnvelope,
    object_id: str,
    domain_object: CuratableObjectEnvelope,
    object_id_by_ref: Mapping[tuple[str, str], str],
    field_definitions: Mapping[str, DomainPackFieldDefinition],
    field_policies: Mapping[str, FieldValidationPolicy],
    projection_ref: Mapping[str, Any],
) -> list[CurationSubmissionReadinessBlocker]:
    blockers: list[CurationSubmissionReadinessBlocker] = []
    for finding_index, finding in enumerate(envelope.validation_findings):
        target_object_id, field_path = _finding_target(finding, object_id_by_ref)
        if target_object_id != object_id:
            continue
        if finding.severity not in _DOMAIN_BLOCKER_SEVERITIES:
            continue
        if finding.status is ValidationFindingStatus.RESOLVED:
            continue
        if finding.status is ValidationFindingStatus.WAIVED and _finding_waiver_allowed(
            finding=finding,
            domain_object=domain_object,
            field_definition=(
                field_definitions.get(field_path)
                if field_path is not None
                else None
            ),
            field_policy=(
                field_policies.get(field_path)
                if field_path is not None
                else None
            ),
            field_path=field_path,
        ):
            continue
        code = finding.code or (
            "domain_envelope.validation_finding_waiver_not_allowed"
            if finding.status is ValidationFindingStatus.WAIVED
            else "domain_envelope.validation_finding_open"
        )
        blockers.append(
            _readiness_blocker(
                envelope_id=envelope.envelope_id,
                object_id=target_object_id,
                field_path=field_path,
                severity=finding.severity.value,
                status_value=finding.status.value,
                code=code,
                message=finding.message,
                provider_refs=_metadata_provider_refs(finding.details),
                projection_ref=projection_ref,
                details={
                    **dict(finding.details or {}),
                    "finding_index": finding_index,
                    "finding_id": finding.finding_id,
                },
            )
        )
    return blockers


def _finding_waiver_allowed(
    *,
    finding: ValidationFinding,
    domain_object: CuratableObjectEnvelope,
    field_definition: DomainPackFieldDefinition | None,
    field_policy: FieldValidationPolicy | None,
    field_path: str | None,
) -> bool:
    details = finding.details or {}
    for metadata in _finding_policy_metadata_sources(details):
        if _metadata_allows_curator_override(metadata):
            return True
    if field_path is not None and _curator_override_satisfies_policy(
        domain_object=domain_object,
        field_definition=field_definition,
        field_policy=field_policy,
        field_path=field_path,
    ):
        return True
    return False


def _finding_policy_metadata_sources(
    details: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    metadata_sources: list[Mapping[str, Any]] = [details]
    raw_validation_metadata = details.get("validation_metadata")
    if isinstance(raw_validation_metadata, Mapping):
        metadata_sources.append(raw_validation_metadata)
        raw_field_policy = raw_validation_metadata.get("field_policy")
        if isinstance(raw_field_policy, Mapping):
            metadata_sources.append(raw_field_policy)
    return tuple(metadata_sources)


def _domain_object_for_id(
    envelope: DomainEnvelope,
    object_id: str,
) -> CuratableObjectEnvelope | None:
    for domain_object in envelope.objects:
        if object_id in {
            value
            for value in (domain_object.object_id, domain_object.pending_ref_id)
            if value is not None
        }:
            return domain_object
    return None


def _build_domain_envelope_object_context(
    *,
    candidate: CurationCandidate,
    envelope_row: DomainEnvelopeModel,
    envelope: DomainEnvelope,
    expected_revision: int,
    projection_refs: tuple[dict[str, Any], ...],
) -> _DomainEnvelopeObjectContext:
    object_id = str(candidate.object_id)
    projection_ref = projection_refs[0] if projection_refs else {
        "envelope_id": envelope.envelope_id,
        "object_id": object_id,
        "envelope_revision": envelope_row.revision,
    }
    blockers: list[CurationSubmissionReadinessBlocker] = []
    warnings: list[str] = []

    if envelope_row.revision != expected_revision:
        return _DomainEnvelopeObjectContext(
            candidate_id=str(candidate.id),
            envelope_row=envelope_row,
            envelope=None,
            domain_object=None,
            projection_refs=projection_refs,
            blockers=(
                _readiness_blocker(
                    envelope_id=envelope_row.envelope_id,
                    object_id=object_id,
                    field_path=None,
                    severity=ValidationFindingSeverity.BLOCKER.value,
                    status_value="stale_revision",
                    code="domain_envelope.stale_revision",
                    message=(
                        f"Domain envelope {envelope_row.envelope_id} is at revision "
                        f"{envelope_row.revision}, not expected revision {expected_revision}."
                    ),
                    projection_ref=projection_ref,
                    details={
                        "expected_revision": expected_revision,
                        "actual_revision": envelope_row.revision,
                    },
                ),
            ),
        )

    domain_object = _domain_object_for_id(envelope, object_id)
    if domain_object is None:
        return _DomainEnvelopeObjectContext(
            candidate_id=str(candidate.id),
            envelope_row=envelope_row,
            envelope=envelope,
            domain_object=None,
            projection_refs=projection_refs,
            blockers=(
                _readiness_blocker(
                    envelope_id=envelope.envelope_id,
                    object_id=object_id,
                    field_path=None,
                    severity=ValidationFindingSeverity.BLOCKER.value,
                    status_value="missing_object",
                    code="domain_envelope.object_not_found",
                    message="Domain envelope object was not found at the expected revision.",
                    projection_ref=projection_ref,
                    details={"expected_revision": expected_revision},
                ),
            ),
        )

    domain_pack = _loaded_domain_pack_for_envelope(envelope)
    registry = (
        DomainPackValidationRegistry.from_domain_pack(domain_pack)
        if domain_pack is not None
        else None
    )
    object_definition = (
        registry.object_definitions_by_type.get(domain_object.object_type)
        if registry is not None
        else None
    )
    field_definitions = _field_definitions_for(object_definition)
    field_policies = {
        policy.field_path: policy
        for policy in (registry.field_policies if registry is not None else ())
        if policy.object_type == domain_object.object_type
    }

    blockers.extend(
        _pack_export_policy_blockers(
            domain_pack=domain_pack,
            envelope=envelope,
            object_id=object_id,
            projection_ref=projection_ref,
        )
    )
    blockers.extend(
        _definition_state_blockers(
            envelope=envelope,
            object_id=object_id,
            domain_object=domain_object,
            object_definition=object_definition,
            projection_ref=projection_ref,
        )
    )
    blockers.extend(
        _export_behavior_blockers(
            envelope=envelope,
            object_id=object_id,
            domain_object=domain_object,
            object_definition=object_definition,
            projection_ref=projection_ref,
        )
    )
    field_blockers, field_warnings = _field_policy_blockers(
        envelope=envelope,
        object_id=object_id,
        domain_object=domain_object,
        field_definitions=field_definitions,
        field_policies=field_policies,
        projection_ref=projection_ref,
    )
    blockers.extend(field_blockers)
    warnings.extend(field_warnings)
    blockers.extend(
        _validation_finding_blockers(
            envelope=envelope,
            object_id=object_id,
            domain_object=domain_object,
            object_id_by_ref=_object_id_by_ref(envelope),
            field_definitions=field_definitions,
            field_policies=field_policies,
            projection_ref=projection_ref,
        )
    )

    return _DomainEnvelopeObjectContext(
        candidate_id=str(candidate.id),
        envelope_row=envelope_row,
        envelope=envelope,
        domain_object=domain_object,
        object_definition=object_definition,
        field_definitions=field_definitions,
        field_policies=field_policies,
        projection_refs=projection_refs,
        blockers=tuple(blockers),
        warnings=tuple(dedupe(warnings)),
    )


def _build_domain_envelope_submission_context(
    *,
    db: Session,
    candidates: Mapping[str, CurationCandidate],
    target_candidate_ids: Sequence[str],
    expected_envelope_revisions: Mapping[str, int] | None = None,
) -> _DomainEnvelopeSubmissionContext:
    expected_revisions = dict(expected_envelope_revisions or {})
    for envelope_id, revision in expected_revisions.items():
        if not envelope_id.strip() or revision < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Expected envelope revisions must be positive integers keyed by envelope_id",
            )

    domain_candidates = [
        candidates[candidate_id]
        for candidate_id in target_candidate_ids
        if candidate_id in candidates and _domain_envelope_candidate(candidates[candidate_id])
    ]
    domain_envelope_ids = [
        str(candidate.envelope_id)
        for candidate in domain_candidates
        if candidate.envelope_id is not None
    ]
    unknown_expected_envelopes = sorted(set(expected_revisions) - set(domain_envelope_ids))
    if unknown_expected_envelopes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Expected envelope revision supplied for envelope(s) outside the "
                f"submission request: {', '.join(unknown_expected_envelopes)}"
            ),
        )
    if not domain_candidates:
        return _DomainEnvelopeSubmissionContext()

    envelope_rows = {
        row.envelope_id: row
        for row in db.scalars(
            select(DomainEnvelopeModel).where(
                DomainEnvelopeModel.envelope_id.in_(list(dict.fromkeys(domain_envelope_ids)))
            )
        ).all()
    }
    projection_refs = _projection_refs_by_object(db, envelope_ids=domain_envelope_ids)

    object_contexts: dict[str, _DomainEnvelopeObjectContext] = {}
    envelope_snapshots: dict[str, dict[str, Any]] = {}

    for candidate in domain_candidates:
        candidate_id = str(candidate.id)
        envelope_id = str(candidate.envelope_id)
        object_id = str(candidate.object_id)
        expected_revision = expected_revisions.get(
            envelope_id,
            int(candidate.envelope_revision or 0),
        )
        row = envelope_rows.get(envelope_id)
        object_projection_refs = projection_refs.get((envelope_id, object_id), ())
        projection_ref = object_projection_refs[0] if object_projection_refs else {
            "envelope_id": envelope_id,
            "object_id": object_id,
            "envelope_revision": expected_revision,
        }
        if row is None:
            object_contexts[candidate_id] = _DomainEnvelopeObjectContext(
                candidate_id=candidate_id,
                envelope_row=None,
                envelope=None,
                domain_object=None,
                projection_refs=object_projection_refs,
                blockers=(
                    _readiness_blocker(
                        envelope_id=envelope_id,
                        object_id=object_id,
                        field_path=None,
                        severity=ValidationFindingSeverity.BLOCKER.value,
                        status_value="missing_envelope",
                        code="domain_envelope.not_found",
                        message="Domain envelope was not found for export/submission.",
                        projection_ref=projection_ref,
                    ),
                ),
            )
            continue

        envelope = DomainEnvelope.model_validate(row.envelope_json)
        context = _build_domain_envelope_object_context(
            candidate=candidate,
            envelope_row=row,
            envelope=envelope,
            expected_revision=expected_revision,
            projection_refs=object_projection_refs,
        )
        object_contexts[candidate_id] = context
        if row.revision == expected_revision and envelope_id not in envelope_snapshots:
            envelope_snapshots[envelope_id] = _domain_envelope_snapshot(
                envelope_row=row,
                envelope=envelope,
                selected_object_ids=[
                    str(item.object_id)
                    for item in domain_candidates
                    if item.envelope_id == envelope_id and item.object_id is not None
                ],
            )

    return _DomainEnvelopeSubmissionContext(
        object_contexts=object_contexts,
        envelope_snapshots=envelope_snapshots,
    )


def _domain_envelope_snapshot(
    *,
    envelope_row: DomainEnvelopeModel,
    envelope: DomainEnvelope,
    selected_object_ids: Sequence[str],
) -> dict[str, Any]:
    selected = set(selected_object_ids)
    object_id_by_ref = _object_id_by_ref(envelope)
    selected_objects = [
        domain_object.model_dump(mode="json")
        for domain_object in envelope.objects
        if _stable_object_id(domain_object) in selected
    ]
    return {
        "envelope_id": envelope.envelope_id,
        "envelope_revision": envelope_row.revision,
        "domain_pack_id": envelope.domain_pack_id,
        "domain_pack_version": envelope.domain_pack_version,
        "status": envelope.status.value,
        "schema_provider": envelope_row.schema_provider,
        "schema_ref": dict(envelope_row.schema_ref_json or {}),
        "object_model_ref": dict(envelope_row.object_model_ref_json or {}),
        "model_field_ref": dict(envelope_row.model_field_ref_json or {}),
        "selected_object_ids": sorted(selected),
        "objects": selected_objects,
        "validation_findings": [
            finding.model_dump(mode="json")
            for finding in envelope.validation_findings
            if _finding_target(finding, object_id_by_ref)[0] in selected
        ],
        "metadata": dict(envelope.metadata or {}),
    }


def _candidate_submission_readiness(
    candidate: CurationCandidate,
    validation_snapshot: CurationValidationSnapshotSchema | None,
    domain_context: _DomainEnvelopeSubmissionContext | None = None,
) -> CurationCandidateSubmissionReadiness:
    draft = _draft_detail(candidate.draft)
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Curation candidate {candidate.id} is missing its draft payload",
        )

    blocking_reasons: list[str] = []
    warnings: list[str] = []

    if candidate.status == CurationCandidateStatus.PENDING:
        blocking_reasons.append("Candidate is still pending curator review.")
    elif candidate.status == CurationCandidateStatus.REJECTED:
        blocking_reasons.append("Candidate was rejected and is excluded from submission.")
    elif candidate.status != CurationCandidateStatus.ACCEPTED:
        blocking_reasons.append(
            f"Candidate status {candidate.status.value} is not eligible for submission."
        )

    field_map = {
        field.field_key: field
        for field in draft.fields
    }
    field_results = (
        validation_snapshot.field_results
        if validation_snapshot is not None
        else {}
    )
    for field_key, validation_result in field_results.items():
        blocking_reason = _submission_validation_blocking_reason(
            field_map.get(field_key),
            validation_result,
        )
        if blocking_reason is not None:
            blocking_reasons.append(blocking_reason)
        warnings.extend(validation_result.warnings)

    blockers = list(
        domain_context.blockers_for(str(candidate.id))
        if domain_context is not None
        else ()
    )
    blocking_reasons.extend(blocker.message for blocker in blockers)
    if domain_context is not None:
        warnings.extend(domain_context.warnings_for(str(candidate.id)))

    return CurationCandidateSubmissionReadiness(
        candidate_id=str(candidate.id),
        ready=candidate.status == CurationCandidateStatus.ACCEPTED and not blocking_reasons,
        blocking_reasons=dedupe(blocking_reasons),
        warnings=dedupe(warnings),
        blockers=blockers,
    )


def _submission_candidate_bundle(
    candidate: CurationCandidate,
) -> dict[str, Any]:
    draft = _draft_detail(candidate.draft)
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Curation candidate {candidate.id} is missing its draft payload",
        )

    return {
        "candidate_id": str(candidate.id),
        "adapter_key": candidate.adapter_key,
        "display_label": candidate.display_label,
        "secondary_label": candidate.secondary_label,
        "fields": {
            field.field_key: field.value
            for field in draft.fields
        },
        "draft_fields": [
            field.model_dump(mode="json")
            for field in draft.fields
        ],
        "metadata": dict(candidate.candidate_metadata or {}),
        "normalized_payload": dict(candidate.normalized_payload or {}),
    }


def _domain_envelope_candidate_bundle(
    candidate: CurationCandidate,
    domain_context: _DomainEnvelopeSubmissionContext,
) -> dict[str, Any] | None:
    context = domain_context.object_contexts.get(str(candidate.id))
    if (
        context is None
        or context.envelope_row is None
        or context.envelope is None
        or context.domain_object is None
        or context.blockers
    ):
        return None

    domain_object = context.domain_object
    object_id = _stable_object_id(domain_object)
    object_definition = context.object_definition
    return {
        "candidate_id": str(candidate.id),
        "adapter_key": candidate.adapter_key,
        "display_label": candidate.display_label,
        "secondary_label": candidate.secondary_label,
        "semantic_source": "domain_envelope.objects",
        "projection_ref": {
            "envelope_id": context.envelope.envelope_id,
            "object_id": object_id,
            "envelope_revision": context.envelope_row.revision,
        },
        "envelope_id": context.envelope.envelope_id,
        "envelope_revision": context.envelope_row.revision,
        "domain_pack_id": context.envelope.domain_pack_id,
        "domain_pack_version": context.envelope.domain_pack_version,
        "object_id": object_id,
        "object_type": domain_object.object_type,
        "object_role": domain_object.object_role,
        "object_status": domain_object.status.value,
        "definition_state": domain_object.definition_state.value,
        "payload": dict(domain_object.payload),
        "object": domain_object.model_dump(mode="json"),
        "schema_ref": (
            domain_object.schema_ref.model_dump(mode="json")
            if domain_object.schema_ref is not None
            else {}
        ),
        "object_model_ref": {
            "envelope": dict(context.envelope_row.object_model_ref_json or {}),
            "definition": (
                object_definition.model_dump(mode="json")
                if object_definition is not None
                else None
            ),
        },
        "model_field_ref": dict(context.envelope_row.model_field_ref_json or {}),
        "projection_refs": list(context.projection_refs),
        "provider_refs": _metadata_provider_refs(domain_object.metadata),
        "metadata": {
            "semantic_source": "domain_envelope.objects",
            "candidate_metadata": dict(candidate.candidate_metadata or {}),
        },
    }


def _non_envelope_ready_candidates(
    ready_candidates: Sequence[CurationCandidate],
    domain_context: _DomainEnvelopeSubmissionContext | None,
) -> list[CurationCandidate]:
    if domain_context is None:
        return list(ready_candidates)
    return [
        candidate
        for candidate in ready_candidates
        if not domain_context.has_domain_candidate(str(candidate.id))
    ]


def _domain_envelope_candidate_bundles(
    ready_candidates: Sequence[CurationCandidate],
    domain_context: _DomainEnvelopeSubmissionContext | None,
) -> list[dict[str, Any]]:
    if domain_context is None:
        return []
    bundles: list[dict[str, Any]] = []
    for candidate in ready_candidates:
        bundle = _domain_envelope_candidate_bundle(candidate, domain_context)
        if bundle is not None:
            bundles.append(bundle)
    return bundles


def _readiness_blocker_payloads(
    readiness: Sequence[CurationCandidateSubmissionReadiness],
) -> list[dict[str, Any]]:
    return [
        blocker.model_dump(mode="json")
        for item in readiness
        for blocker in item.blockers
    ]


class _SharedSubmissionPreviewAdapter:
    """Default adapter-owned payload builder used when no custom builder is registered yet."""

    def __init__(self, adapter_key: str) -> None:
        self.adapter_key = adapter_key
        self.supported_submission_modes = tuple(SubmissionMode)
        self.supported_target_keys: tuple[str, ...] = ()

    def build_submission_payload(
        self,
        *,
        mode: SubmissionMode,
        target_key: str,
        payload_context: Mapping[str, Any],
    ) -> SubmissionPayloadContract:
        payload_json: dict[str, Any] = {
            "session_id": payload_context["session_id"],
            "adapter_key": self.adapter_key,
            "mode": mode.value,
            "target_key": target_key,
            "candidate_count": payload_context["candidate_count"],
            "candidates": payload_context["candidates"],
            "domain_envelope_candidates": payload_context["domain_envelope_candidates"],
            "domain_envelopes": payload_context["domain_envelopes"],
            "readiness_blockers": payload_context["readiness_blockers"],
        }
        document = payload_context.get("document")
        if document is not None:
            payload_json["document"] = document
        session_validation = payload_context.get("session_validation")
        if session_validation is not None:
            payload_json["session_validation"] = session_validation

        payload_kwargs: dict[str, Any] = {
            "mode": mode,
            "target_key": target_key,
            "adapter_key": self.adapter_key,
            "candidate_ids": payload_context["candidate_ids"],
            "payload_json": payload_json,
            "warnings": payload_context["warnings"],
        }

        return SubmissionPayloadContract(**payload_kwargs)


def _resolve_submission_domain_adapter(adapter_key: str) -> SubmissionDomainAdapter:
    return _SharedSubmissionPreviewAdapter(adapter_key)


def _default_submission_target_key(adapter_key: str) -> str:
    return f"{adapter_key}.default"


def _resolve_submission_preview_target_key(
    *,
    adapter_key: str,
    requested_target_key: str | None,
) -> tuple[SubmissionDomainAdapter, str]:
    submission_adapter = _resolve_submission_domain_adapter(adapter_key)
    supported_target_keys = tuple(submission_adapter.supported_target_keys or ())

    if requested_target_key:
        if supported_target_keys and requested_target_key not in supported_target_keys:
            supported_targets = ", ".join(supported_target_keys)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Unsupported submission target '{requested_target_key}' for "
                    f"adapter '{adapter_key}'. Supported targets: {supported_targets}"
                ),
            )

        return submission_adapter, requested_target_key

    if supported_target_keys:
        return submission_adapter, supported_target_keys[0]

    # Keep the shared substrate target-agnostic even before adapters publish
    # explicit target identifiers for preview/export flows.
    return submission_adapter, _default_submission_target_key(adapter_key)


def _resolve_export_preview_target_key(
    *,
    adapter_key: str,
    requested_target_key: str | None,
):
    export_adapter = _resolve_export_adapter(adapter_key)
    supported_target_keys = tuple(export_adapter.supported_target_keys or ())

    if requested_target_key:
        if supported_target_keys and requested_target_key not in supported_target_keys:
            supported_targets = ", ".join(supported_target_keys)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Unsupported export target '{requested_target_key}' for "
                    f"adapter '{adapter_key}'. Supported targets: {supported_targets}"
                ),
            )

        return export_adapter, requested_target_key

    if supported_target_keys:
        return export_adapter, supported_target_keys[0]

    return export_adapter, _default_submission_target_key(adapter_key)


def _resolve_export_adapter(adapter_key: str):
    export_adapter = _export_adapter_registry().get(adapter_key)
    if export_adapter is not None:
        return export_adapter

    # Keep direct-submit payload building aligned with the shared submission
    # contract while domain-specific export adapters continue to roll out.
    return _resolve_submission_domain_adapter(adapter_key)


def _resolve_submission_transport_adapter(target_key: str) -> SubmissionTransportAdapter:
    try:
        return _submission_adapter_registry().require(target_key)
    except KeyError as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Submission target is not configured",
            log_message=f"Unknown submission target requested: {target_key}",
            exc=exc,
            level=logging.WARNING,
        )


def _base_submission_payload_context(
    *,
    db: Session,
    session_row: ReviewSessionModel,
    ready_candidates: Sequence[CurationCandidate],
    session_validation: CurationValidationSnapshotSchema | None,
) -> dict[str, Any]:
    document = db.get(PDFDocument, session_row.document_id)
    warnings: list[str] = []
    if not ready_candidates:
        warnings.append("No accepted candidates are ready for submission.")

    return {
        "session_id": str(session_row.id),
        "document": (
            _document_ref(document).model_dump(mode="json")
            if document is not None
            else None
        ),
        "session_validation": (
            session_validation.model_dump(mode="json")
            if session_validation is not None
            else None
        ),
        "warnings": dedupe(warnings),
    }


def _submission_payload_context(
    *,
    db: Session,
    session_row: ReviewSessionModel,
    ready_candidates: Sequence[CurationCandidate],
    session_validation: CurationValidationSnapshotSchema | None,
    domain_context: _DomainEnvelopeSubmissionContext | None = None,
    readiness: Sequence[CurationCandidateSubmissionReadiness] = (),
) -> dict[str, Any]:
    payload_context = _base_submission_payload_context(
        db=db,
        session_row=session_row,
        ready_candidates=ready_candidates,
        session_validation=session_validation,
    )
    non_envelope_candidates = _non_envelope_ready_candidates(
        ready_candidates,
        domain_context,
    )
    domain_candidates = _domain_envelope_candidate_bundles(
        ready_candidates,
        domain_context,
    )
    candidate_ids = [
        str(candidate.id) for candidate in non_envelope_candidates
    ] + [
        str(item["candidate_id"]) for item in domain_candidates
    ]

    return {
        **payload_context,
        "candidate_ids": candidate_ids,
        "candidate_count": len(candidate_ids),
        "candidates": [
            _submission_candidate_bundle(candidate)
            for candidate in non_envelope_candidates
        ],
        "domain_envelope_candidates": domain_candidates,
        "domain_envelopes": (
            list(domain_context.envelope_snapshots.values())
            if domain_context is not None
            else []
        ),
        "readiness_blockers": _readiness_blocker_payloads(readiness),
    }


def _export_submission_payload_context(
    *,
    db: Session,
    session_row: ReviewSessionModel,
    ready_candidates: Sequence[CurationCandidate],
    session_validation: CurationValidationSnapshotSchema | None,
    domain_context: _DomainEnvelopeSubmissionContext | None = None,
    readiness: Sequence[CurationCandidateSubmissionReadiness] = (),
) -> dict[str, Any]:
    payload_context = _base_submission_payload_context(
        db=db,
        session_row=session_row,
        ready_candidates=ready_candidates,
        session_validation=session_validation,
    )
    non_envelope_candidates = _non_envelope_ready_candidates(
        ready_candidates,
        domain_context,
    )
    export_candidates = [
        _candidate_payload(candidate).model_dump(mode="json")
        for candidate in non_envelope_candidates
    ]
    domain_candidates = _domain_envelope_candidate_bundles(
        ready_candidates,
        domain_context,
    )
    candidate_ids = [
        candidate["candidate_id"] for candidate in export_candidates
    ] + [
        str(item["candidate_id"]) for item in domain_candidates
    ]

    return {
        **payload_context,
        "candidate_ids": candidate_ids,
        "candidate_count": len(candidate_ids),
        "candidates": export_candidates,
        "domain_envelope_candidates": domain_candidates,
        "domain_envelopes": (
            list(domain_context.envelope_snapshots.values())
            if domain_context is not None
            else []
        ),
        "readiness_blockers": _readiness_blocker_payloads(readiness),
    }


def _build_submission_preview_payload(
    *,
    db: Session,
    session_row: ReviewSessionModel,
    submission_adapter: SubmissionDomainAdapter,
    mode: SubmissionMode,
    target_key: str,
    ready_candidates: Sequence[CurationCandidate],
    session_validation: CurationValidationSnapshotSchema | None,
    domain_context: _DomainEnvelopeSubmissionContext | None = None,
    readiness: Sequence[CurationCandidateSubmissionReadiness] = (),
) -> SubmissionPayloadContract:
    payload_context = _submission_payload_context(
        db=db,
        session_row=session_row,
        ready_candidates=ready_candidates,
        session_validation=session_validation,
        domain_context=domain_context,
        readiness=readiness,
    )
    return submission_adapter.build_submission_payload(
        mode=mode,
        target_key=target_key,
        payload_context=payload_context,
    )


def _build_submission_execute_payload(
    *,
    db: Session,
    session_row: ReviewSessionModel,
    mode: SubmissionMode,
    target_key: str,
    ready_candidates: Sequence[CurationCandidate],
    session_validation: CurationValidationSnapshotSchema | None,
    adapter_key: str | None = None,
    domain_context: _DomainEnvelopeSubmissionContext | None = None,
    readiness: Sequence[CurationCandidateSubmissionReadiness] = (),
) -> SubmissionPayloadContract:
    export_adapter = _resolve_export_adapter(adapter_key or session_row.adapter_key)
    payload_context = _export_submission_payload_context(
        db=db,
        session_row=session_row,
        ready_candidates=ready_candidates,
        session_validation=session_validation,
        domain_context=domain_context,
        readiness=readiness,
    )

    try:
        return export_adapter.build_submission_payload(
            mode=mode,
            target_key=target_key,
            payload_context=payload_context,
        )
    except ValueError as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Submission payload could not be built",
            log_message=f"Submission payload build failed for target {target_key}",
            exc=exc,
            level=logging.WARNING,
        )


def _coerce_failed_submission_result(
    *,
    adapter: SubmissionTransportAdapter,
    error: Exception,
) -> SubmissionTransportResult:
    if isinstance(error, SubmissionTransportError):
        return error.to_result()

    return normalize_submission_transport_result(
        status=CurationSubmissionStatus.FAILED,
        response_message=SUBMISSION_TRANSPORT_FAILURE_MESSAGE,
    )


def _submission_attempt_marks_session_submitted(status_value: CurationSubmissionStatus) -> bool:
    return status_value in {
        CurationSubmissionStatus.ACCEPTED,
        CurationSubmissionStatus.QUEUED,
        CurationSubmissionStatus.MANUAL_REVIEW_REQUIRED,
    }


def _submission_action_message(
    *,
    result_status: CurationSubmissionStatus,
    target_key: str,
) -> str:
    return (
        f"Submission to target '{target_key}' completed with status "
        f"'{result_status.value}'"
    )


def _submission_candidate_ids(record: SubmissionModel) -> list[str]:
    payload = _submission_payload(record)
    if payload is not None and payload.candidate_ids:
        return dedupe(payload.candidate_ids)

    return dedupe(
        [
            candidate_id
            for readiness_item in (record.readiness or [])
            if isinstance(readiness_item, dict)
            and readiness_item.get("ready") is True
            and isinstance(candidate_id := readiness_item.get("candidate_id"), str)
            and candidate_id
        ]
    )


def _reject_direct_submit_with_domain_blockers(
    readiness: Sequence[CurationCandidateSubmissionReadiness],
) -> None:
    blockers = [
        blocker
        for readiness_item in readiness
        for blocker in readiness_item.blockers
    ]
    if not blockers:
        return
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "message": "Domain-envelope readiness blockers prevent direct submission",
            "blockers": [blocker.model_dump(mode="json") for blocker in blockers],
        },
    )


def _execute_direct_submission_attempt(
    *,
    db: Session,
    session_row: ReviewSessionModel,
    adapter_key: str,
    mode: SubmissionMode,
    target_key: str,
    payload: SubmissionPayloadContract,
    readiness: Sequence[CurationCandidateSubmissionReadiness],
    actor_claims: dict[str, Any],
    action_type: CurationActionType,
    action_metadata: Mapping[str, Any] | None = None,
) -> tuple[CurationSubmissionRecord, CurationActionLogEntry]:
    transport_adapter = _resolve_submission_transport_adapter(target_key)
    requested_at = datetime.now(timezone.utc)
    try:
        result = coerce_submission_transport_result(
            transport_adapter.submit(payload=payload)
        )
    except Exception as exc:
        logger.exception(
            "Submission transport adapter '%s' failed for session '%s' and target '%s'",
            transport_adapter.transport_key,
            str(session_row.id),
            target_key,
        )
        result = _coerce_failed_submission_result(
            adapter=transport_adapter,
            error=exc,
        )

    if result.status not in DIRECT_SUBMISSION_RESULT_STATUSES:
        result = normalize_submission_transport_result(
            status=CurationSubmissionStatus.FAILED,
            response_message=(
                f"Submission adapter '{transport_adapter.transport_key}' returned "
                f"unsupported direct-submit status '{result.status.value}'"
            ),
            warnings=result.warnings,
            submission_state=result.submission_state,
            target_result_history=result.target_result_history,
        )

    completed_at = result.completed_at or requested_at
    combined_warnings = dedupe([*payload.warnings, *result.warnings])
    submission_row = SubmissionModel(
        session_id=session_row.id,
        adapter_key=adapter_key,
        mode=mode,
        target_key=target_key,
        status=result.status,
        readiness=[item.model_dump(mode="json") for item in readiness],
        payload=_serialize_submission_payload_contract(payload),
        external_reference=result.external_reference,
        response_message=result.response_message,
        validation_errors=list(result.validation_errors),
        warnings=combined_warnings,
        submission_state=dict(result.submission_state or {}),
        target_result_history=[
            dict(item)
            for item in result.target_result_history
        ],
        requested_at=requested_at,
        completed_at=completed_at,
    )

    previous_session_status = session_row.status
    if _submission_attempt_marks_session_submitted(result.status):
        session_row.status = CurationSessionStatus.SUBMITTED
        if session_row.submitted_at is None:
            session_row.submitted_at = completed_at
    session_row.updated_at = completed_at
    session_row.last_worked_at = completed_at
    session_row.session_version += 1

    action_log_payload = {
        "target_key": target_key,
        "mode": mode.value,
        "submission_status": result.status.value,
        "submitted_candidate_ids": list(payload.candidate_ids),
        "submitted_candidate_count": len(payload.candidate_ids),
        "external_reference": result.external_reference,
        "validation_error_count": len(result.validation_errors),
        "submission_state": dict(result.submission_state or {}),
        "target_result_history_count": len(result.target_result_history),
    }
    if action_metadata:
        action_log_payload.update(dict(action_metadata))

    action_log_row = SessionActionLogModel(
        session_id=session_row.id,
        action_type=action_type,
        actor_type=CurationActorType.USER,
        actor=_actor_claims_payload(actor_claims),
        occurred_at=completed_at,
        previous_session_status=(
            previous_session_status if previous_session_status != session_row.status else None
        ),
        new_session_status=(
            session_row.status if previous_session_status != session_row.status else None
        ),
        message=_submission_action_message(
            result_status=result.status,
            target_key=target_key,
        ),
        action_metadata=action_log_payload,
    )

    db.add(session_row)
    db.add(submission_row)
    db.add(action_log_row)
    db.flush()

    response_submission = _submission_record(submission_row).model_copy(
        update={
            "payload": payload,
            "warnings": combined_warnings,
        }
    )

    db.commit()
    action_log_entry = _action_log_entry(action_log_row)
    if action_log_entry is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Submission action log entry could not be serialized",
        )

    return response_submission, action_log_entry


def submission_preview(
    db: Session,
    session_id: str | UUID,
    request: CurationSubmissionPreviewRequest,
) -> CurationSubmissionPreviewResponse:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    request_session_id = _normalize_uuid(request.session_id, field_name="session_id")
    if normalized_session_id != request_session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path session_id does not match request body session_id",
        )

    validation_response = validate_session(
        db,
        normalized_session_id,
        CurationSessionValidationRequest(
            session_id=request.session_id,
            candidate_ids=request.candidate_ids,
            force=False,
        ),
    )

    session_row = _load_session_for_validation(db, session_id=normalized_session_id)
    submission_adapter = None
    if request.mode == SubmissionMode.EXPORT:
        _, target_key = _resolve_export_preview_target_key(
            adapter_key=session_row.adapter_key,
            requested_target_key=request.target_key,
        )
    else:
        submission_adapter, target_key = _resolve_submission_preview_target_key(
            adapter_key=session_row.adapter_key,
            requested_target_key=request.target_key,
        )
    candidate_map = {str(candidate.id): candidate for candidate in session_row.candidates}
    target_candidate_ids = request.candidate_ids or list(candidate_map.keys())
    domain_context = _build_domain_envelope_submission_context(
        db=db,
        candidates=candidate_map,
        target_candidate_ids=target_candidate_ids,
        expected_envelope_revisions=request.expected_envelope_revisions,
    )
    readiness = [
        _candidate_submission_readiness(
            candidate_map[candidate_id],
            next(
                (
                    candidate_validation
                    for candidate_validation in validation_response.candidate_validations
                    if candidate_validation.candidate_id == candidate_id
                ),
                None,
            ),
            domain_context=domain_context,
        )
        for candidate_id in target_candidate_ids
    ]
    ready_candidates = [
        candidate_map[readiness_item.candidate_id]
        for readiness_item in readiness
        if readiness_item.ready
    ]

    payload = (
        (
            _build_submission_execute_payload(
                db=db,
                session_row=session_row,
                mode=request.mode,
                target_key=target_key,
                ready_candidates=ready_candidates,
                session_validation=validation_response.session_validation,
                domain_context=domain_context,
                readiness=readiness,
            )
            if request.mode == SubmissionMode.EXPORT
            else _build_submission_preview_payload(
                db=db,
                session_row=session_row,
                submission_adapter=submission_adapter,
                mode=request.mode,
                target_key=target_key,
                ready_candidates=ready_candidates,
                session_validation=validation_response.session_validation,
                domain_context=domain_context,
                readiness=readiness,
            )
        )
        if request.include_payload
        else None
    )
    submission_warnings = list(payload.warnings) if payload is not None else []

    return CurationSubmissionPreviewResponse(
        submission=CurationSubmissionRecord(
            submission_id=str(uuid4()),
            session_id=str(session_row.id),
            adapter_key=session_row.adapter_key,
            mode=request.mode,
            target_key=target_key,
            status=(
                CurationSubmissionStatus.EXPORT_READY
                if request.mode == SubmissionMode.EXPORT
                else CurationSubmissionStatus.PREVIEW_READY
            ),
            readiness=readiness,
            payload=_submission_payload_model_input(payload),
            requested_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            validation_errors=[],
            warnings=submission_warnings,
        ),
        session_validation=validation_response.session_validation,
    )


def execute_submission(
    db: Session,
    session_id: str | UUID,
    request: CurationSubmissionExecuteRequest,
    actor_claims: dict[str, Any],
) -> CurationSubmissionExecuteResponse:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    request_session_id = _normalize_uuid(request.session_id, field_name="session_id")
    if normalized_session_id != request_session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path session_id does not match request body session_id",
        )
    if request.mode != SubmissionMode.DIRECT_SUBMIT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Submit endpoint only supports mode 'direct_submit'",
        )

    validation_response = validate_session(
        db,
        normalized_session_id,
        CurationSessionValidationRequest(
            session_id=request.session_id,
            candidate_ids=request.candidate_ids,
            force=False,
        ),
    )

    session_row = _load_session_for_validation(db, session_id=normalized_session_id)
    candidate_map = {str(candidate.id): candidate for candidate in session_row.candidates}
    target_candidate_ids = request.candidate_ids or list(candidate_map.keys())
    domain_context = _build_domain_envelope_submission_context(
        db=db,
        candidates=candidate_map,
        target_candidate_ids=target_candidate_ids,
        expected_envelope_revisions=request.expected_envelope_revisions,
    )
    readiness = [
        _candidate_submission_readiness(
            candidate_map[candidate_id],
            next(
                (
                    candidate_validation
                    for candidate_validation in validation_response.candidate_validations
                    if candidate_validation.candidate_id == candidate_id
                ),
                None,
            ),
            domain_context=domain_context,
        )
        for candidate_id in target_candidate_ids
    ]
    _reject_direct_submit_with_domain_blockers(readiness)
    ready_candidates = [
        candidate_map[readiness_item.candidate_id]
        for readiness_item in readiness
        if readiness_item.ready
    ]
    if not ready_candidates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No eligible candidates are ready for direct submission",
        )

    payload = _build_submission_execute_payload(
        db=db,
        session_row=session_row,
        mode=request.mode,
        target_key=request.target_key,
        ready_candidates=ready_candidates,
        session_validation=validation_response.session_validation,
        domain_context=domain_context,
        readiness=readiness,
    )
    response_submission, action_log_entry = _execute_direct_submission_attempt(
        db=db,
        session_row=session_row,
        adapter_key=payload.adapter_key,
        mode=request.mode,
        target_key=request.target_key,
        payload=payload,
        readiness=readiness,
        actor_claims=actor_claims,
        action_type=CurationActionType.SUBMISSION_EXECUTED,
    )
    db.expire_all()

    response_session = get_session_detail(db, normalized_session_id)
    if (
        response_session.latest_submission is not None
        and response_session.latest_submission.submission_id == response_submission.submission_id
    ):
        response_session = response_session.model_copy(
            update={"latest_submission": response_submission}
        )

    return CurationSubmissionExecuteResponse(
        submission=response_submission,
        session=response_session,
        action_log_entry=action_log_entry,
    )


def retry_submission(
    db: Session,
    session_id: str | UUID,
    submission_id: str | UUID,
    request: CurationSubmissionRetryRequest,
    actor_claims: dict[str, Any],
) -> CurationSubmissionRetryResponse:
    normalized_session_id = _normalize_uuid(session_id, field_name="session_id")
    normalized_submission_id = _normalize_uuid(submission_id, field_name="submission_id")
    request_submission_id = _normalize_uuid(request.submission_id, field_name="submission_id")
    if normalized_submission_id != request_submission_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path submission_id does not match request body submission_id",
        )

    original_submission = _load_submission_record(
        db,
        session_id=normalized_session_id,
        submission_id=normalized_submission_id,
    )
    if original_submission.mode != SubmissionMode.DIRECT_SUBMIT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only direct-submit submissions may be retried",
        )
    if original_submission.status != CurationSubmissionStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only failed submissions may be retried",
        )

    target_candidate_ids = _submission_candidate_ids(original_submission)
    if not target_candidate_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Original submission does not include retriable candidate identifiers",
        )

    validation_response = validate_session(
        db,
        normalized_session_id,
        CurationSessionValidationRequest(
            session_id=str(normalized_session_id),
            candidate_ids=target_candidate_ids,
            force=False,
        ),
    )

    session_row = _load_session_for_validation(db, session_id=normalized_session_id)
    candidate_map = {str(candidate.id): candidate for candidate in session_row.candidates}
    domain_context = _build_domain_envelope_submission_context(
        db=db,
        candidates=candidate_map,
        target_candidate_ids=target_candidate_ids,
        expected_envelope_revisions=request.expected_envelope_revisions,
    )
    readiness = [
        _candidate_submission_readiness(
            candidate_map[candidate_id],
            next(
                (
                    candidate_validation
                    for candidate_validation in validation_response.candidate_validations
                    if candidate_validation.candidate_id == candidate_id
                ),
                None,
            ),
            domain_context=domain_context,
        )
        for candidate_id in target_candidate_ids
    ]
    _reject_direct_submit_with_domain_blockers(readiness)
    ready_candidates = [
        candidate_map[readiness_item.candidate_id]
        for readiness_item in readiness
        if readiness_item.ready
    ]
    if not ready_candidates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No eligible candidates are ready for direct submission",
        )

    payload = _build_submission_execute_payload(
        db=db,
        session_row=session_row,
        adapter_key=original_submission.adapter_key,
        mode=original_submission.mode,
        target_key=original_submission.target_key,
        ready_candidates=ready_candidates,
        session_validation=validation_response.session_validation,
        domain_context=domain_context,
        readiness=readiness,
    )
    retry_reason = _normalized_optional_string(request.reason, field_name="reason")
    response_submission, action_log_entry = _execute_direct_submission_attempt(
        db=db,
        session_row=session_row,
        adapter_key=original_submission.adapter_key,
        mode=original_submission.mode,
        target_key=original_submission.target_key,
        payload=payload,
        readiness=readiness,
        actor_claims=actor_claims,
        action_type=CurationActionType.SUBMISSION_RETRIED,
        action_metadata={
            "original_submission_id": str(original_submission.id),
            "retry_reason": retry_reason,
        },
    )
    db.expire_all()

    return CurationSubmissionRetryResponse(
        submission=response_submission,
        action_log_entry=action_log_entry,
    )


def get_submission(
    db: Session,
    session_id: str | UUID,
    submission_id: str | UUID,
) -> CurationSubmissionHistoryResponse:
    submission_row = _load_submission_record(
        db,
        session_id=session_id,
        submission_id=submission_id,
    )
    return CurationSubmissionHistoryResponse(
        submission=_submission_record(submission_row),
    )


__all__ = [
    "execute_submission",
    "get_submission",
    "retry_submission",
    "submission_preview",
]
