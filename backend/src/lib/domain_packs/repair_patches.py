"""Bounded repair-patch helpers for domain-envelope validation failures.

This module is intentionally provider-neutral. Domain packs declare which object
payload fields are editable/repairable through field metadata, and the repair
patch engine rejects everything else before updating envelope JSON.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from typing import Any, Iterable, Literal, Mapping
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    FieldRef,
    HistoryActorType,
    HistoryEvent,
    HistoryEventKind,
    ObjectRef,
    ValidationFinding,
    parse_field_path,
    validate_field_path_syntax,
)
from src.schemas.domain_pack_metadata import DomainPackFieldDefinition

from .registry import LoadedDomainPack
from .validation_registry import DomainPackValidationRegistry


REPAIR_CONTEXT_METADATA_KEY = "repair_context"
DEFAULT_REPAIR_RETRY_BUDGET = 2
_MISSING = object()


class RepairPatchStatus(str, Enum):
    """Outcome for one extractor repair patch."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    STALE_REVISION = "stale_revision"
    RETRY_EXHAUSTED = "retry_exhausted"


class RepairFinalStatus(str, Enum):
    """Final curator-visible repair classifications."""

    REPAIRED = "repaired"
    VALIDATOR_RERUN_REQUESTED = "validator_rerun_requested"
    NO_REPAIR_POSSIBLE = "no_repair_possible"
    UNDER_DEVELOPMENT = "under_development"
    TRUE_NOT_FOUND = "not_found"
    TRANSIENT_SERVICE_FAILURE = "transient_service_failure"
    BLOCKED_VALIDATOR = "blocked_validator"
    RETRY_EXHAUSTED = "retry_exhausted"


class RepairContractModel(BaseModel):
    """Strict base for repair prompt/tool contracts."""

    model_config = ConfigDict(extra="forbid")


class RepairRetryBudget(RepairContractModel):
    """Retry accounting for one validation-driven repair target."""

    max_attempts: int = Field(ge=0)
    used_attempts: int = Field(ge=0)
    remaining_attempts: int = Field(ge=0)
    exhausted: bool


class RepairRequestTarget(RepairContractModel):
    """One field-level target the supervisor may ask an extractor to repair."""

    finding_id: StrictStr | None = None
    object_ref: ObjectRef
    field_path: StrictStr
    current_value: Any = None
    validator_code: StrictStr | None = None
    message: StrictStr
    retry_budget: RepairRetryBudget
    repairable: bool
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("field_path")
    @classmethod
    def _validate_field_path(cls, value: str) -> str:
        return validate_field_path_syntax(value)


class DomainEnvelopeRepairRequest(RepairContractModel):
    """Supervisor-to-extractor repair request contract."""

    repair_action: Literal["repair_request"] = "repair_request"
    request_id: StrictStr = Field(default_factory=lambda: f"repair-request:{uuid4().hex}")
    envelope_id: StrictStr
    expected_revision: int = Field(ge=0)
    targets: list[RepairRequestTarget] = Field(min_length=1)
    instructions: StrictStr = (
        "Return extractor_patch operations only for the requested field paths, "
        "or return no_repair_possible/mark_under_development when repair is not supported."
    )


class RepairPatchOperation(RepairContractModel):
    """One bounded replace operation against an object payload field path."""

    op: Literal["replace"] = "replace"
    object_ref: ObjectRef
    field_path: StrictStr
    expected_before: Any
    after: Any
    reason: StrictStr
    finding_id: StrictStr | None = None
    evidence_record_ids: list[StrictStr] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("field_path")
    @classmethod
    def _validate_field_path(cls, value: str) -> str:
        return validate_field_path_syntax(value)

    @field_validator("reason")
    @classmethod
    def _validate_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must not be empty")
        return value


class DomainEnvelopeRepairPatch(RepairContractModel):
    """Extractor-to-supervisor patch contract."""

    repair_action: Literal["extractor_patch"] = "extractor_patch"
    patch_id: StrictStr = Field(default_factory=lambda: f"repair-patch:{uuid4().hex}")
    envelope_id: StrictStr
    expected_revision: int = Field(ge=0)
    operations: list[RepairPatchOperation] = Field(min_length=1)
    source_finding_ids: list[StrictStr] = Field(default_factory=list)
    rationale: StrictStr
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("rationale")
    @classmethod
    def _validate_rationale(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("rationale must not be empty")
        return value

    @model_validator(mode="after")
    def _merge_operation_finding_ids(self) -> "DomainEnvelopeRepairPatch":
        if self.source_finding_ids:
            return self
        operation_finding_ids = [
            operation.finding_id
            for operation in self.operations
            if operation.finding_id is not None
        ]
        if operation_finding_ids:
            self.source_finding_ids.extend(dict.fromkeys(operation_finding_ids))
        return self


class RepairFinalClassification(RepairContractModel):
    """Supervisor final classification contract for repair outcomes."""

    repair_action: Literal[
        "validator_rerun",
        "no_repair_possible",
        "mark_under_development",
        "final_classification",
    ] = "final_classification"
    classification_id: StrictStr = Field(
        default_factory=lambda: f"repair-classification:{uuid4().hex}"
    )
    envelope_id: StrictStr
    expected_revision: int = Field(ge=0)
    status: RepairFinalStatus
    reason: StrictStr
    finding_ids: list[StrictStr] = Field(default_factory=list)
    validator_binding_ids: list[StrictStr] = Field(default_factory=list)
    object_ref: ObjectRef | None = None
    field_path: StrictStr | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("field_path")
    @classmethod
    def _validate_optional_field_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_field_path_syntax(value)

    @field_validator("reason")
    @classmethod
    def _validate_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must not be empty")
        return value


class DomainEnvelopeExtractorFinalClassification(RepairContractModel):
    """Extractor final response when a requested repair cannot produce a patch."""

    repair_action: Literal["no_repair_possible", "mark_under_development"]
    classification_id: StrictStr = Field(
        default_factory=lambda: f"repair-classification:{uuid4().hex}"
    )
    envelope_id: StrictStr
    expected_revision: int = Field(ge=0)
    status: RepairFinalStatus
    reason: StrictStr
    finding_ids: list[StrictStr] = Field(default_factory=list)
    object_ref: ObjectRef | None = None
    field_path: StrictStr | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("field_path")
    @classmethod
    def _validate_optional_field_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_field_path_syntax(value)

    @field_validator("reason")
    @classmethod
    def _validate_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must not be empty")
        return value

    @model_validator(mode="after")
    def _validate_action_status_pair(self) -> "DomainEnvelopeExtractorFinalClassification":
        expected_status = {
            "no_repair_possible": RepairFinalStatus.NO_REPAIR_POSSIBLE,
            "mark_under_development": RepairFinalStatus.UNDER_DEVELOPMENT,
        }[self.repair_action]
        if self.status != expected_status:
            raise ValueError(
                f"{self.repair_action} must use status '{expected_status.value}'"
            )
        return self


@dataclass(frozen=True)
class RepairPatchResult:
    """Result returned after validating and applying a repair patch."""

    envelope: DomainEnvelope
    status: RepairPatchStatus
    errors: tuple[str, ...]
    accepted_operations: tuple[RepairPatchOperation, ...]
    rejected_operations: tuple[RepairPatchOperation, ...]
    retry_budget: RepairRetryBudget

    @property
    def accepted(self) -> bool:
        return self.status is RepairPatchStatus.ACCEPTED


def build_repair_request(
    envelope: DomainEnvelope,
    domain_pack: LoadedDomainPack,
    *,
    findings: Iterable[ValidationFinding] | None = None,
    expected_revision: int,
    max_attempts: int = DEFAULT_REPAIR_RETRY_BUDGET,
    registry: DomainPackValidationRegistry | None = None,
) -> DomainEnvelopeRepairRequest:
    """Build a bounded supervisor repair request from validation findings."""

    validation_registry = registry or DomainPackValidationRegistry.from_domain_pack(
        domain_pack
    )
    selected_findings = tuple(findings if findings is not None else envelope.validation_findings)
    targets: list[RepairRequestTarget] = []
    for finding in selected_findings:
        if finding.field_ref is None:
            continue
        target_object = _object_for_ref(envelope, finding.field_ref.object_ref)
        if target_object is None:
            continue
        field_definition = _field_definition_for(
            validation_registry,
            target_object.object_type,
            finding.field_ref.field_path,
        )
        if field_definition is None:
            continue
        repairable, policy_details = _field_repairability(field_definition)
        current_value = _payload_value(target_object.payload, finding.field_ref.field_path)
        if current_value is _MISSING:
            current_value = None
        repair_key = _target_repair_key(
            finding_ids=[finding.finding_id] if finding.finding_id else [],
            object_ref=finding.field_ref.object_ref,
            field_path=finding.field_ref.field_path,
        )
        used_attempts = _used_attempts(envelope, repair_key)
        targets.append(
            RepairRequestTarget(
                finding_id=finding.finding_id,
                object_ref=finding.field_ref.object_ref,
                field_path=finding.field_ref.field_path,
                current_value=current_value,
                validator_code=finding.code,
                message=finding.message,
                retry_budget=_retry_budget(max_attempts, used_attempts),
                repairable=repairable,
                metadata={
                    "field_policy": policy_details,
                    "finding_status": finding.status.value,
                    "finding_severity": finding.severity.value,
                },
            )
        )
    if not targets:
        raise ValueError("No field-level validation findings can be repaired")
    return DomainEnvelopeRepairRequest(
        envelope_id=envelope.envelope_id,
        expected_revision=expected_revision,
        targets=targets,
    )


def record_repair_request(
    envelope: DomainEnvelope,
    repair_request: DomainEnvelopeRepairRequest | Mapping[str, Any],
    *,
    actor_id: str = "domain_repair_supervisor",
) -> DomainEnvelope:
    """Append a repair-request history event and chat/workspace context."""

    request = _coerce_repair_request(repair_request)
    if request.envelope_id != envelope.envelope_id:
        raise ValueError("repair request envelope_id does not match envelope")

    details = request.model_dump(mode="json")
    event = _repair_event(
        envelope=envelope,
        event_type=HistoryEventKind.REPAIR_REQUESTED,
        actor_id=actor_id,
        message="Validation-driven repair requested.",
        details=details,
    )
    metadata = _append_repair_context(
        envelope.metadata,
        {
            "kind": "repair_request",
            "request_id": request.request_id,
            "status": "requested",
            "target_count": len(request.targets),
            "expected_revision": request.expected_revision,
            "chat_summary": (
                f"Repair requested for {len(request.targets)} validation target(s)."
            ),
        },
    )
    return envelope.model_copy(
        update={
            "history": [*envelope.history, event],
            "metadata": metadata,
        }
    )


def apply_repair_patch(
    envelope: DomainEnvelope,
    domain_pack: LoadedDomainPack,
    patch: DomainEnvelopeRepairPatch | Mapping[str, Any],
    *,
    current_revision: int,
    actor_id: str = "domain_repair_supervisor",
    max_attempts: int = DEFAULT_REPAIR_RETRY_BUDGET,
    registry: DomainPackValidationRegistry | None = None,
) -> RepairPatchResult:
    """Validate and apply one extractor repair patch.

    The patch is accepted only when the expected revision matches, every target
    object exists, every field is declared and explicitly editable/repairable,
    no field is protected, and each ``expected_before`` value matches the
    current envelope payload value.
    """

    repair_patch = _coerce_repair_patch(patch)
    validation_registry = registry or DomainPackValidationRegistry.from_domain_pack(
        domain_pack
    )
    patch_retry_key = _repair_key(
        finding_ids=repair_patch.source_finding_ids,
        operations=[
            {
                "object_ref": operation.object_ref.model_dump(mode="json"),
                "field_path": operation.field_path,
            }
            for operation in repair_patch.operations
        ],
    )
    target_retry_keys = _target_retry_keys_for_patch(envelope, repair_patch)
    consumed_retry_keys = _retry_keys_for_attempt(patch_retry_key, target_retry_keys)
    budget_retry_keys = consumed_retry_keys
    used_before = _max_used_attempts(envelope, budget_retry_keys)
    retry_budgets_current = _retry_budgets_by_key(
        envelope,
        budget_retry_keys,
        max_attempts=max_attempts,
        consumed=False,
    )
    retry_budgets_after_consumption = _retry_budgets_by_key(
        envelope,
        budget_retry_keys,
        max_attempts=max_attempts,
        consumed=True,
    )

    if repair_patch.envelope_id != envelope.envelope_id:
        return _rejected_patch_result(
            envelope=envelope,
            patch=repair_patch,
            status=RepairPatchStatus.REJECTED,
            errors=("patch envelope_id does not match envelope",),
            retry_budget=_retry_budget(max_attempts, used_before + 1),
            actor_id=actor_id,
            repair_key=patch_retry_key,
            target_retry_keys=target_retry_keys,
            consumed_retry_keys=consumed_retry_keys,
            retry_budgets_by_key=retry_budgets_after_consumption,
            consumes_retry=True,
        )

    if repair_patch.expected_revision != current_revision:
        return _rejected_patch_result(
            envelope=envelope,
            patch=repair_patch,
            status=RepairPatchStatus.STALE_REVISION,
            errors=(
                "patch expected_revision "
                f"{repair_patch.expected_revision} does not match current revision "
                f"{current_revision}",
            ),
            retry_budget=_retry_budget(max_attempts, used_before),
            actor_id=actor_id,
            repair_key=patch_retry_key,
            target_retry_keys=target_retry_keys,
            consumed_retry_keys=(),
            retry_budgets_by_key=retry_budgets_current,
            consumes_retry=False,
            extra_details={"current_revision": current_revision},
        )

    if used_before >= max_attempts:
        result = _rejected_patch_result(
            envelope=envelope,
            patch=repair_patch,
            status=RepairPatchStatus.RETRY_EXHAUSTED,
            errors=("repair retry budget is exhausted",),
            retry_budget=_retry_budget(max_attempts, used_before),
            actor_id=actor_id,
            repair_key=patch_retry_key,
            target_retry_keys=target_retry_keys,
            consumed_retry_keys=(),
            retry_budgets_by_key=retry_budgets_current,
            consumes_retry=False,
        )
        classified = record_repair_final_classification(
            result.envelope,
            RepairFinalClassification(
                repair_action="final_classification",
                envelope_id=envelope.envelope_id,
                expected_revision=current_revision,
                status=RepairFinalStatus.RETRY_EXHAUSTED,
                reason="Repair retry budget is exhausted.",
                finding_ids=list(repair_patch.source_finding_ids),
            ),
            actor_id=actor_id,
        )
        return RepairPatchResult(
            envelope=classified,
            status=result.status,
            errors=result.errors,
            accepted_operations=result.accepted_operations,
            rejected_operations=result.rejected_operations,
            retry_budget=result.retry_budget,
        )

    validated_operations: list[
        tuple[RepairPatchOperation, int, CuratableObjectEnvelope, Any]
    ] = []
    errors: list[str] = []
    for index, operation in enumerate(repair_patch.operations):
        object_index, target_object = _object_index_for_ref(envelope, operation.object_ref)
        if target_object is None or object_index is None:
            errors.append(f"operations[{index}] references an unknown object")
            continue

        field_definition = _field_definition_for(
            validation_registry,
            target_object.object_type,
            operation.field_path,
        )
        if field_definition is None:
            errors.append(
                f"operations[{index}].field_path '{operation.field_path}' is not "
                f"declared for object_type '{target_object.object_type}'"
            )
            continue

        repairable, repair_policy = _field_repairability(field_definition)
        if repair_policy["protected"]:
            errors.append(
                f"operations[{index}].field_path '{operation.field_path}' is protected"
            )
            continue
        if not repairable:
            errors.append(
                f"operations[{index}].field_path '{operation.field_path}' is not "
                "declared editable or repairable"
            )
            continue

        before_value = _payload_value(target_object.payload, operation.field_path)
        comparable_before = None if before_value is _MISSING else before_value
        if comparable_before != operation.expected_before:
            errors.append(
                f"operations[{index}].expected_before does not match current value "
                f"for '{operation.field_path}'"
            )
            continue

        validated_operations.append(
            (operation, object_index, target_object, comparable_before)
        )

    if errors:
        return _rejected_patch_result(
            envelope=envelope,
            patch=repair_patch,
            status=RepairPatchStatus.REJECTED,
            errors=tuple(errors),
            retry_budget=_retry_budget(max_attempts, used_before + 1),
            actor_id=actor_id,
            repair_key=patch_retry_key,
            target_retry_keys=target_retry_keys,
            consumed_retry_keys=consumed_retry_keys,
            retry_budgets_by_key=retry_budgets_after_consumption,
            consumes_retry=True,
        )

    updated_objects = list(envelope.objects)
    history_events = list(envelope.history)
    field_update_details: list[dict[str, Any]] = []
    for operation, object_index, target_object, before_value in validated_operations:
        updated_payload = copy.deepcopy(updated_objects[object_index].payload)
        _set_payload_value(updated_payload, operation.field_path, operation.after)
        updated_object = updated_objects[object_index].model_copy(
            update={"payload": updated_payload}
        )
        updated_objects[object_index] = updated_object
        field_ref = FieldRef(
            object_ref=_object_ref_for(updated_object),
            field_path=operation.field_path,
        )
        update_details = {
            "patch_id": repair_patch.patch_id,
            "repair_action": repair_patch.repair_action,
            "source_finding_ids": list(repair_patch.source_finding_ids),
            "field_path": operation.field_path,
            "expected_before": operation.expected_before,
            "before": before_value,
            "after": operation.after,
            "reason": operation.reason,
            "evidence_record_ids": list(operation.evidence_record_ids),
        }
        field_update_details.append(update_details)
        history_events.append(
            _repair_event(
                envelope=envelope,
                event_type=HistoryEventKind.FIELD_UPDATED,
                actor_id=actor_id,
                message=f"Repair patch updated {operation.field_path}.",
                field_ref=field_ref,
                details=update_details,
            )
        )

    used_after = used_before + 1
    retry_budget = _retry_budget(max_attempts, used_after)
    accepted_details = {
        "patch": repair_patch.model_dump(mode="json"),
        "status": RepairPatchStatus.ACCEPTED.value,
        "current_revision": current_revision,
        "retry_key": patch_retry_key,
        "target_retry_keys": list(target_retry_keys),
        "retry_keys": list(consumed_retry_keys),
        "retry_budget": retry_budget.model_dump(mode="json"),
        "retry_budgets_by_key": retry_budgets_after_consumption,
        "field_updates": field_update_details,
    }
    history_events.append(
        _repair_event(
            envelope=envelope,
            event_type=HistoryEventKind.REPAIR_PATCH_ACCEPTED,
            actor_id=actor_id,
            message="Repair patch accepted.",
            details=accepted_details,
        )
    )
    metadata = _append_repair_context(
        envelope.metadata,
        {
            "kind": "extractor_patch",
            "patch_id": repair_patch.patch_id,
            "retry_key": patch_retry_key,
            "target_retry_keys": list(target_retry_keys),
            "retry_keys": list(consumed_retry_keys),
            "retry_consumed": True,
            "status": RepairPatchStatus.ACCEPTED.value,
            "source_finding_ids": list(repair_patch.source_finding_ids),
            "operation_count": len(validated_operations),
            "expected_revision": repair_patch.expected_revision,
            "current_revision": current_revision,
            "retry_budget": retry_budget.model_dump(mode="json"),
            "retry_budgets_by_key": retry_budgets_after_consumption,
            "chat_summary": (
                f"Accepted repair patch {repair_patch.patch_id} for "
                f"{len(validated_operations)} field(s)."
            ),
        },
    )

    return RepairPatchResult(
        envelope=envelope.model_copy(
            update={
                "objects": updated_objects,
                "history": history_events,
                "metadata": metadata,
            }
        ),
        status=RepairPatchStatus.ACCEPTED,
        errors=(),
        accepted_operations=tuple(repair_patch.operations),
        rejected_operations=(),
        retry_budget=retry_budget,
    )


def record_validator_rerun_request(
    envelope: DomainEnvelope,
    classification: RepairFinalClassification | Mapping[str, Any],
    *,
    actor_id: str = "domain_repair_supervisor",
) -> DomainEnvelope:
    """Append a validator-rerun request event after an accepted repair patch."""

    payload = _coerce_classification(classification).model_copy(
        update={
            "repair_action": "validator_rerun",
            "status": RepairFinalStatus.VALIDATOR_RERUN_REQUESTED,
        }
    )
    if payload.envelope_id != envelope.envelope_id:
        raise ValueError("validator rerun envelope_id does not match envelope")

    object_ref, field_ref = _history_target_refs(
        envelope,
        object_ref=payload.object_ref,
        field_path=payload.field_path,
    )
    event = _repair_event(
        envelope=envelope,
        event_type=HistoryEventKind.VALIDATION_RERUN_REQUESTED,
        actor_id=actor_id,
        message="Validator rerun requested after repair.",
        object_ref=None if field_ref else object_ref,
        field_ref=field_ref,
        details=payload.model_dump(mode="json"),
    )
    metadata = _append_repair_context(
        envelope.metadata,
        {
            "kind": "validator_rerun",
            "classification_id": payload.classification_id,
            "status": RepairFinalStatus.VALIDATOR_RERUN_REQUESTED.value,
            "finding_ids": list(payload.finding_ids),
            "validator_binding_ids": list(payload.validator_binding_ids),
            "expected_revision": payload.expected_revision,
            **_repair_target_context(payload.object_ref, payload.field_path),
            "chat_summary": "Validator rerun requested after repair.",
        },
    )
    return envelope.model_copy(
        update={
            "history": [*envelope.history, event],
            "metadata": metadata,
        }
    )


def record_repair_final_classification(
    envelope: DomainEnvelope,
    classification: RepairFinalClassification | Mapping[str, Any],
    *,
    actor_id: str = "domain_repair_supervisor",
) -> DomainEnvelope:
    """Append a final repair classification event and chat/workspace context."""

    payload = _coerce_classification(classification)
    if payload.envelope_id != envelope.envelope_id:
        raise ValueError("repair classification envelope_id does not match envelope")

    object_ref, field_ref = _history_target_refs(
        envelope,
        object_ref=payload.object_ref,
        field_path=payload.field_path,
    )
    event = _repair_event(
        envelope=envelope,
        event_type=HistoryEventKind.REPAIR_FINAL_CLASSIFIED,
        actor_id=actor_id,
        message=f"Repair classified as {payload.status.value}.",
        object_ref=None if field_ref else object_ref,
        field_ref=field_ref,
        details=payload.model_dump(mode="json"),
    )
    metadata = _append_repair_context(
        envelope.metadata,
        {
            "kind": "final_classification",
            "classification_id": payload.classification_id,
            "status": payload.status.value,
            "repair_action": payload.repair_action,
            "finding_ids": list(payload.finding_ids),
            "validator_binding_ids": list(payload.validator_binding_ids),
            "expected_revision": payload.expected_revision,
            "reason": payload.reason,
            **_repair_target_context(payload.object_ref, payload.field_path),
            "chat_summary": (
                f"Repair classified as {payload.status.value}: {payload.reason}"
            ),
        },
    )
    return envelope.model_copy(
        update={
            "history": [*envelope.history, event],
            "metadata": metadata,
        }
    )


def _coerce_repair_request(
    repair_request: DomainEnvelopeRepairRequest | Mapping[str, Any],
) -> DomainEnvelopeRepairRequest:
    if isinstance(repair_request, DomainEnvelopeRepairRequest):
        return repair_request
    return DomainEnvelopeRepairRequest.model_validate(repair_request)


def _coerce_repair_patch(
    patch: DomainEnvelopeRepairPatch | Mapping[str, Any],
) -> DomainEnvelopeRepairPatch:
    if isinstance(patch, DomainEnvelopeRepairPatch):
        return patch
    return DomainEnvelopeRepairPatch.model_validate(patch)


def _coerce_classification(
    classification: RepairFinalClassification | Mapping[str, Any],
) -> RepairFinalClassification:
    if isinstance(classification, RepairFinalClassification):
        return classification
    return RepairFinalClassification.model_validate(classification)


def _rejected_patch_result(
    *,
    envelope: DomainEnvelope,
    patch: DomainEnvelopeRepairPatch,
    status: RepairPatchStatus,
    errors: tuple[str, ...],
    retry_budget: RepairRetryBudget,
    actor_id: str,
    repair_key: str,
    target_retry_keys: Iterable[str],
    consumed_retry_keys: Iterable[str],
    retry_budgets_by_key: Mapping[str, Mapping[str, Any]],
    consumes_retry: bool,
    extra_details: Mapping[str, Any] | None = None,
) -> RepairPatchResult:
    target_retry_key_list = list(target_retry_keys)
    consumed_retry_key_list = list(consumed_retry_keys)
    retry_budget_payloads = {
        key: dict(budget) for key, budget in retry_budgets_by_key.items()
    }
    details = {
        "patch": patch.model_dump(mode="json"),
        "status": status.value,
        "errors": list(errors),
        "retry_key": repair_key,
        "target_retry_keys": target_retry_key_list,
        "retry_keys": consumed_retry_key_list,
        "retry_consumed": consumes_retry,
        "retry_budget": retry_budget.model_dump(mode="json"),
        "retry_budgets_by_key": retry_budget_payloads,
    }
    if extra_details:
        details.update(dict(extra_details))
    event = _repair_event(
        envelope=envelope,
        event_type=HistoryEventKind.REPAIR_PATCH_REJECTED,
        actor_id=actor_id,
        message=f"Repair patch rejected: {status.value}.",
        details=details,
    )
    metadata = _append_repair_context(
        envelope.metadata,
        {
            "kind": "extractor_patch",
            "patch_id": patch.patch_id,
            "retry_key": repair_key,
            "target_retry_keys": target_retry_key_list,
            "retry_keys": consumed_retry_key_list,
            "status": status.value,
            "source_finding_ids": list(patch.source_finding_ids),
            "operation_count": len(patch.operations),
            "expected_revision": patch.expected_revision,
            "retry_budget": retry_budget.model_dump(mode="json"),
            "retry_consumed": consumes_retry,
            "retry_budgets_by_key": retry_budget_payloads,
            "errors": list(errors),
            "chat_summary": (
                f"Rejected repair patch {patch.patch_id}: {status.value}."
            ),
        },
    )
    return RepairPatchResult(
        envelope=envelope.model_copy(
            update={
                "history": [*envelope.history, event],
                "metadata": metadata,
            }
        ),
        status=status,
        errors=errors,
        accepted_operations=(),
        rejected_operations=tuple(patch.operations),
        retry_budget=retry_budget,
    )


def _retry_budget(max_attempts: int, used_attempts: int) -> RepairRetryBudget:
    remaining = max(0, max_attempts - used_attempts)
    return RepairRetryBudget(
        max_attempts=max_attempts,
        used_attempts=used_attempts,
        remaining_attempts=remaining,
        exhausted=remaining == 0,
    )


def _used_attempts(envelope: DomainEnvelope, repair_key: str) -> int:
    context = envelope.metadata.get(REPAIR_CONTEXT_METADATA_KEY)
    if not isinstance(context, Mapping):
        return 0
    attempts = context.get("attempts")
    if not isinstance(attempts, list):
        return 0
    return sum(
        1
        for item in attempts
        if isinstance(item, Mapping)
        and _attempt_consumed_retry_key(item, repair_key)
        and item.get("kind") == "extractor_patch"
        and item.get("status") in {
            RepairPatchStatus.ACCEPTED.value,
            RepairPatchStatus.REJECTED.value,
        }
    )


def _attempt_consumed_retry_key(attempt: Mapping[str, Any], repair_key: str) -> bool:
    retry_keys = attempt.get("retry_keys")
    if isinstance(retry_keys, list):
        return repair_key in {str(item) for item in retry_keys if str(item)}
    return attempt.get("retry_key") == repair_key


def _max_used_attempts(envelope: DomainEnvelope, repair_keys: Iterable[str]) -> int:
    return max((_used_attempts(envelope, key) for key in repair_keys), default=0)


def _retry_budgets_by_key(
    envelope: DomainEnvelope,
    repair_keys: Iterable[str],
    *,
    max_attempts: int,
    consumed: bool,
) -> dict[str, dict[str, Any]]:
    increment = 1 if consumed else 0
    return {
        key: _retry_budget(
            max_attempts,
            _used_attempts(envelope, key) + increment,
        ).model_dump(mode="json")
        for key in repair_keys
    }


def _append_repair_context(
    metadata: Mapping[str, Any],
    entry: Mapping[str, Any],
) -> dict[str, Any]:
    updated_metadata = copy.deepcopy(dict(metadata))
    raw_context = updated_metadata.get(REPAIR_CONTEXT_METADATA_KEY)
    context = copy.deepcopy(dict(raw_context)) if isinstance(raw_context, Mapping) else {}
    attempts = list(context.get("attempts") if isinstance(context.get("attempts"), list) else [])
    classifications = list(
        context.get("classifications")
        if isinstance(context.get("classifications"), list)
        else []
    )

    entry_payload = dict(entry)
    if entry_payload.get("kind") == "final_classification":
        classifications.append(entry_payload)
    else:
        attempts.append(entry_payload)

    context["attempts"] = attempts
    context["classifications"] = classifications
    context["latest_status"] = entry_payload.get("status")
    context["latest_chat_summary"] = entry_payload.get("chat_summary")
    updated_metadata[REPAIR_CONTEXT_METADATA_KEY] = context
    return updated_metadata


def _field_definition_for(
    registry: DomainPackValidationRegistry,
    object_type: str,
    field_path: str,
) -> DomainPackFieldDefinition | None:
    object_definition = registry.object_definitions_by_type.get(object_type)
    if object_definition is None:
        return None
    for field_definition in object_definition.fields:
        if field_definition.field_path == field_path:
            return field_definition
    return None


def _field_repairability(
    field_definition: DomainPackFieldDefinition,
) -> tuple[bool, dict[str, Any]]:
    metadata = field_definition.metadata
    protected = _metadata_bool(metadata, "protected") or _nested_metadata_bool(
        metadata,
        "repair",
        "protected",
    )
    repairable = (
        _metadata_bool(metadata, "repairable")
        or _metadata_bool(metadata, "editable")
        or _nested_metadata_bool(metadata, "repair", "repairable")
        or _nested_metadata_bool(metadata, "repair", "editable")
        or _nested_metadata_bool(metadata, "edit", "editable")
    )
    details = {
        "field_path": field_definition.field_path,
        "definition_state": field_definition.definition_state.value,
        "repairable": repairable,
        "editable": (
            _metadata_bool(metadata, "editable")
            or _nested_metadata_bool(metadata, "repair", "editable")
            or _nested_metadata_bool(metadata, "edit", "editable")
        ),
        "protected": protected,
    }
    if field_definition.definition_notes:
        details["definition_notes"] = list(field_definition.definition_notes)
    return (repairable and not protected), details


def _metadata_bool(metadata: Mapping[str, Any], key: str) -> bool:
    return metadata.get(key) is True


def _nested_metadata_bool(metadata: Mapping[str, Any], outer_key: str, inner_key: str) -> bool:
    nested = metadata.get(outer_key)
    return isinstance(nested, Mapping) and nested.get(inner_key) is True


def _object_index_for_ref(
    envelope: DomainEnvelope,
    object_ref: ObjectRef,
) -> tuple[int | None, CuratableObjectEnvelope | None]:
    ref_key = object_ref.ref_key()
    for index, domain_object in enumerate(envelope.objects):
        if ref_key in domain_object.ref_keys():
            return index, domain_object
    return None, None


def _object_for_ref(
    envelope: DomainEnvelope,
    object_ref: ObjectRef,
) -> CuratableObjectEnvelope | None:
    _, domain_object = _object_index_for_ref(envelope, object_ref)
    return domain_object


def _history_target_refs(
    envelope: DomainEnvelope,
    *,
    object_ref: ObjectRef | None,
    field_path: str | None,
) -> tuple[ObjectRef | None, FieldRef | None]:
    if object_ref is None:
        return None, None
    if field_path is None:
        return object_ref, None

    domain_object = _object_for_ref(envelope, object_ref)
    if (
        domain_object is not None
        and _payload_value(domain_object.payload, field_path) is not _MISSING
    ):
        return None, FieldRef(object_ref=object_ref, field_path=field_path)

    return object_ref, None


def _repair_target_context(
    object_ref: ObjectRef | None,
    field_path: str | None,
) -> dict[str, Any]:
    context: dict[str, Any] = {}
    if object_ref is not None:
        context["object_ref"] = object_ref.model_dump(mode="json")
    if field_path is not None:
        context["field_path"] = field_path
    return context


def _payload_value(payload: Mapping[str, Any], field_path: str) -> Any:
    current: Any = payload
    for part in parse_field_path(field_path):
        if isinstance(part, str):
            if not isinstance(current, Mapping) or part not in current:
                return _MISSING
            current = current[part]
            continue
        if (
            not isinstance(current, list)
            or isinstance(current, (str, bytes, bytearray))
            or part >= len(current)
        ):
            return _MISSING
        current = current[part]
    return current


def _set_payload_value(payload: dict[str, Any], field_path: str, value: Any) -> None:
    parts = parse_field_path(field_path)
    current: Any = payload
    for index, part in enumerate(parts[:-1]):
        next_part = parts[index + 1]
        if isinstance(part, str):
            if not isinstance(current, dict):
                raise ValueError(f"Cannot set '{field_path}' through non-object parent")
            if part not in current or current[part] is None:
                current[part] = [] if isinstance(next_part, int) else {}
            current = current[part]
            continue
        if not isinstance(current, list) or isinstance(current, (str, bytes, bytearray)):
            raise ValueError(f"Cannot set '{field_path}' through non-array parent")
        if part == len(current):
            current.append([] if isinstance(next_part, int) else {})
        if part >= len(current):
            raise ValueError(f"Cannot set '{field_path}' because a list index is missing")
        current = current[part]

    final_part = parts[-1]
    if isinstance(final_part, str):
        if not isinstance(current, dict):
            raise ValueError(f"Cannot set '{field_path}' on non-object parent")
        current[final_part] = value
        return

    if not isinstance(current, list) or isinstance(current, (str, bytes, bytearray)):
        raise ValueError(f"Cannot set '{field_path}' on non-array parent")
    if final_part == len(current):
        current.append(value)
        return
    if final_part >= len(current):
        raise ValueError(f"Cannot set '{field_path}' because a list index is missing")
    current[final_part] = value


def _object_ref_for(domain_object: CuratableObjectEnvelope) -> ObjectRef:
    if domain_object.object_id is not None:
        return ObjectRef(
            object_id=domain_object.object_id,
            object_type=domain_object.object_type,
        )
    if domain_object.pending_ref_id is not None:
        return ObjectRef(
            pending_ref_id=domain_object.pending_ref_id,
            object_type=domain_object.object_type,
        )
    raise ValueError("CuratableObjectEnvelope must provide object_id or pending_ref_id")


def _repair_event(
    *,
    envelope: DomainEnvelope,
    event_type: HistoryEventKind,
    actor_id: str,
    message: str,
    details: Mapping[str, Any],
    object_ref: ObjectRef | None = None,
    field_ref: FieldRef | None = None,
) -> HistoryEvent:
    seed = {
        "envelope_id": envelope.envelope_id,
        "event_type": event_type.value,
        "actor_id": actor_id,
        "message": message,
        "details": _jsonable(details),
        "object_ref": object_ref.model_dump(mode="json") if object_ref else None,
        "field_ref": field_ref.model_dump(mode="json") if field_ref else None,
    }
    digest = sha256(json.dumps(seed, sort_keys=True).encode("utf-8")).hexdigest()
    return HistoryEvent(
        event_type=event_type,
        event_id=f"repair-event:{digest}",
        actor_type=HistoryActorType.SYSTEM,
        actor_id=actor_id,
        message=message,
        object_ref=object_ref,
        field_ref=field_ref,
        details=dict(details),
    )


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def _target_retry_keys_for_patch(
    envelope: DomainEnvelope,
    patch: DomainEnvelopeRepairPatch,
) -> tuple[str, ...]:
    target_keys = [
        _target_repair_key(
            finding_ids=_operation_finding_ids(envelope, patch, operation),
            object_ref=operation.object_ref,
            field_path=operation.field_path,
        )
        for operation in patch.operations
    ]
    return tuple(dict.fromkeys(target_keys))


def _operation_finding_ids(
    envelope: DomainEnvelope,
    patch: DomainEnvelopeRepairPatch,
    operation: RepairPatchOperation,
) -> tuple[str, ...]:
    if operation.finding_id:
        return (operation.finding_id,)

    source_finding_ids = tuple(dict.fromkeys(patch.source_finding_ids))
    matched_finding_ids = tuple(
        finding.finding_id
        for finding in envelope.validation_findings
        if finding.finding_id is not None
        and finding.field_ref is not None
        and (
            not source_finding_ids
            or finding.finding_id in source_finding_ids
        )
        and finding.field_ref.object_ref.ref_key() == operation.object_ref.ref_key()
        and finding.field_ref.field_path == operation.field_path
    )
    if matched_finding_ids:
        return matched_finding_ids

    if len(source_finding_ids) == 1:
        return source_finding_ids

    return ()


def _target_repair_key(
    *,
    finding_ids: Iterable[str],
    object_ref: ObjectRef,
    field_path: str,
) -> str:
    return _repair_key(
        finding_ids=finding_ids,
        operations=[
            {
                "object_ref": object_ref.model_dump(mode="json"),
                "field_path": field_path,
            }
        ],
    )


def _retry_keys_for_attempt(
    patch_retry_key: str,
    target_retry_keys: Iterable[str],
) -> tuple[str, ...]:
    return tuple(dict.fromkeys((patch_retry_key, *target_retry_keys)))


def _repair_key(
    *,
    finding_ids: Iterable[str],
    operations: Iterable[Mapping[str, Any]],
) -> str:
    normalized_finding_ids = sorted({str(item) for item in finding_ids if str(item)})
    seed = {
        "finding_ids": normalized_finding_ids,
        "operations": list(operations),
    }
    digest = sha256(json.dumps(seed, sort_keys=True).encode("utf-8")).hexdigest()
    return f"repair:{digest}"


__all__ = [
    "DEFAULT_REPAIR_RETRY_BUDGET",
    "REPAIR_CONTEXT_METADATA_KEY",
    "DomainEnvelopeExtractorFinalClassification",
    "DomainEnvelopeRepairPatch",
    "DomainEnvelopeRepairRequest",
    "RepairFinalClassification",
    "RepairFinalStatus",
    "RepairPatchOperation",
    "RepairPatchResult",
    "RepairPatchStatus",
    "RepairRequestTarget",
    "RepairRetryBudget",
    "apply_repair_patch",
    "build_repair_request",
    "record_repair_final_classification",
    "record_repair_request",
    "record_validator_rerun_request",
]
