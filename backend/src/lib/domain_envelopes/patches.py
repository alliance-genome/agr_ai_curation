"""Provider-neutral field-path patches for persisted domain envelopes."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from typing import Any, Mapping
from uuid import uuid4

from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    FieldRef,
    HistoryActorType,
    HistoryEvent,
    HistoryEventKind,
    ObjectRef,
    parse_field_path,
    validate_field_path_syntax,
)
from src.schemas.domain_pack_metadata import DomainPackFieldDefinition


_MISSING = object()


class EnvelopeFieldPatchOperation(str, Enum):
    """Supported curator edit operations against one object payload field."""

    REPLACE = "replace"


class EnvelopeFieldPatchStatus(str, Enum):
    """Outcome for a curator field-path patch."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    STALE_REVISION = "stale_revision"


@dataclass(frozen=True)
class EnvelopeFieldPatch:
    """One optimistic-concurrency field patch against an envelope object."""

    envelope_id: str
    expected_revision: int
    object_id: str
    field_path: str
    before: Any
    value: Any
    operation: EnvelopeFieldPatchOperation = EnvelopeFieldPatchOperation.REPLACE
    reason: str | None = None
    patch_id: str = field(default_factory=lambda: f"curator-field-patch:{uuid4().hex}")


@dataclass(frozen=True)
class EnvelopeFieldPatchResult:
    """Validated patch outcome and envelope snapshot after applying history."""

    envelope: DomainEnvelope
    status: EnvelopeFieldPatchStatus
    errors: tuple[str, ...]
    before: Any
    after: Any
    object_type: str | None
    history_event_ids: tuple[str, ...]

    @property
    def accepted(self) -> bool:
        return self.status is EnvelopeFieldPatchStatus.ACCEPTED


def apply_curator_field_patch(
    envelope: DomainEnvelope,
    domain_pack: LoadedDomainPack,
    patch: EnvelopeFieldPatch,
    *,
    current_revision: int,
    actor_id: str,
    registry: DomainPackValidationRegistry | None = None,
) -> EnvelopeFieldPatchResult:
    """Validate and apply one curator field edit to a domain envelope.

    The patch is accepted only when the expected revision matches, the object
    exists, the field path is declared editable or repairable by the domain
    pack, the target is not protected, and ``before`` matches the current
    object payload value.
    """

    validation_registry = registry or DomainPackValidationRegistry.from_domain_pack(
        domain_pack
    )
    errors: list[str] = []

    if patch.envelope_id != envelope.envelope_id:
        return _rejected_without_history(
            envelope=envelope,
            status=EnvelopeFieldPatchStatus.REJECTED,
            errors=("patch envelope_id does not match envelope",),
            before=None,
            after=patch.value,
        )

    if patch.expected_revision != current_revision:
        return _rejected_without_history(
            envelope=envelope,
            status=EnvelopeFieldPatchStatus.STALE_REVISION,
            errors=(
                "patch expected_revision "
                f"{patch.expected_revision} does not match current revision "
                f"{current_revision}",
            ),
            before=None,
            after=patch.value,
        )

    object_index, domain_object = _object_index_for_stable_id(envelope, patch.object_id)
    object_ref = _object_ref_for(domain_object) if domain_object is not None else None
    current_before: Any = None
    object_type = domain_object.object_type if domain_object is not None else None

    try:
        validate_field_path_syntax(patch.field_path)
    except ValueError as exc:
        errors.append(f"field_path is invalid: {exc}")

    if patch.operation is not EnvelopeFieldPatchOperation.REPLACE:
        errors.append(f"operation '{patch.operation.value}' is not supported")

    if domain_object is None or object_index is None:
        errors.append(f"object_id '{patch.object_id}' was not found in the envelope")
    elif not errors:
        field_definition = _field_definition_for(
            validation_registry,
            domain_object.object_type,
            patch.field_path,
        )
        if field_definition is None:
            errors.append(
                f"field_path '{patch.field_path}' is not declared for object_type "
                f"'{domain_object.object_type}'"
            )
        else:
            editable, policy = _field_editability(field_definition)
            if policy["protected"]:
                errors.append(f"field_path '{patch.field_path}' is protected")
            elif not editable:
                errors.append(
                    f"field_path '{patch.field_path}' is not declared editable or repairable"
                )

        before_value = _payload_value(domain_object.payload, patch.field_path)
        current_before = None if before_value is _MISSING else before_value
        if current_before != patch.before:
            errors.append(
                f"before does not match current value for field_path '{patch.field_path}'"
            )

    if errors:
        rejected = _with_rejection_history(
            envelope=envelope,
            patch=patch,
            errors=tuple(errors),
            current_revision=current_revision,
            actor_id=actor_id,
            object_ref=object_ref,
            before=current_before,
            object_type=object_type,
        )
        return EnvelopeFieldPatchResult(
            envelope=rejected,
            status=EnvelopeFieldPatchStatus.REJECTED,
            errors=tuple(errors),
            before=current_before,
            after=patch.value,
            object_type=object_type,
            history_event_ids=(rejected.history[-1].event_id or "",),
        )

    assert domain_object is not None
    assert object_index is not None
    assert object_ref is not None

    staged_payload = copy.deepcopy(domain_object.payload)
    try:
        _set_payload_value(staged_payload, patch.field_path, patch.value)
    except ValueError as exc:
        rejected = _with_rejection_history(
            envelope=envelope,
            patch=patch,
            errors=(str(exc),),
            current_revision=current_revision,
            actor_id=actor_id,
            object_ref=object_ref,
            before=current_before,
            object_type=object_type,
        )
        return EnvelopeFieldPatchResult(
            envelope=rejected,
            status=EnvelopeFieldPatchStatus.REJECTED,
            errors=(str(exc),),
            before=current_before,
            after=patch.value,
            object_type=object_type,
            history_event_ids=(rejected.history[-1].event_id or "",),
        )

    updated_objects = list(envelope.objects)
    updated_object = domain_object.model_copy(update={"payload": staged_payload})
    updated_objects[object_index] = updated_object

    field_ref = FieldRef(
        object_ref=_object_ref_for(updated_object),
        field_path=patch.field_path,
    )
    details = {
        "patch_id": patch.patch_id,
        "status": EnvelopeFieldPatchStatus.ACCEPTED.value,
        "operation": patch.operation.value,
        "expected_revision": patch.expected_revision,
        "current_revision": current_revision,
        "object_id": patch.object_id,
        "object_type": updated_object.object_type,
        "field_path": patch.field_path,
        "before": current_before,
        "after": patch.value,
        "reason": patch.reason,
    }
    field_event = _curator_event(
        envelope=envelope,
        event_type=HistoryEventKind.FIELD_UPDATED,
        actor_id=actor_id,
        message=f"Curator updated {patch.field_path}.",
        field_ref=field_ref,
        details=details,
    )
    accepted_event = _curator_event(
        envelope=envelope,
        event_type=HistoryEventKind.CURATOR_FIELD_PATCH_ACCEPTED,
        actor_id=actor_id,
        message="Curator field patch accepted.",
        field_ref=field_ref,
        details=details,
    )
    updated_envelope = _validated_envelope(
        envelope.model_copy(
            update={
                "objects": updated_objects,
                "history": [*envelope.history, field_event, accepted_event],
            }
        )
    )
    return EnvelopeFieldPatchResult(
        envelope=updated_envelope,
        status=EnvelopeFieldPatchStatus.ACCEPTED,
        errors=(),
        before=current_before,
        after=patch.value,
        object_type=updated_object.object_type,
        history_event_ids=(
            field_event.event_id or "",
            accepted_event.event_id or "",
        ),
    )


def _rejected_without_history(
    *,
    envelope: DomainEnvelope,
    status: EnvelopeFieldPatchStatus,
    errors: tuple[str, ...],
    before: Any,
    after: Any,
) -> EnvelopeFieldPatchResult:
    return EnvelopeFieldPatchResult(
        envelope=envelope,
        status=status,
        errors=errors,
        before=before,
        after=after,
        object_type=None,
        history_event_ids=(),
    )


def _with_rejection_history(
    *,
    envelope: DomainEnvelope,
    patch: EnvelopeFieldPatch,
    errors: tuple[str, ...],
    current_revision: int,
    actor_id: str,
    object_ref: ObjectRef | None,
    before: Any,
    object_type: str | None,
) -> DomainEnvelope:
    event = _curator_event(
        envelope=envelope,
        event_type=HistoryEventKind.CURATOR_FIELD_PATCH_REJECTED,
        actor_id=actor_id,
        message="Curator field patch rejected.",
        object_ref=object_ref,
        details={
            "patch_id": patch.patch_id,
            "status": EnvelopeFieldPatchStatus.REJECTED.value,
            "operation": patch.operation.value,
            "expected_revision": patch.expected_revision,
            "current_revision": current_revision,
            "object_id": patch.object_id,
            "object_type": object_type,
            "field_path": patch.field_path,
            "before": before,
            "after": patch.value,
            "reason": patch.reason,
            "errors": list(errors),
        },
    )
    return _validated_envelope(
        envelope.model_copy(update={"history": [*envelope.history, event]})
    )


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


def _field_editability(
    field_definition: DomainPackFieldDefinition,
) -> tuple[bool, dict[str, Any]]:
    metadata = field_definition.metadata
    protected = (
        _metadata_bool(metadata, "protected")
        or _nested_metadata_bool(metadata, "repair", "protected")
        or _nested_metadata_bool(metadata, "edit", "protected")
    )
    declared_editable = (
        _metadata_bool(metadata, "editable")
        or _metadata_bool(metadata, "repairable")
        or _nested_metadata_bool(metadata, "repair", "editable")
        or _nested_metadata_bool(metadata, "repair", "repairable")
        or _nested_metadata_bool(metadata, "edit", "editable")
    )
    return (
        declared_editable and not protected,
        {
            "field_path": field_definition.field_path,
            "editable": declared_editable and not protected,
            "declared_editable": declared_editable,
            "protected": protected,
            "definition_state": field_definition.definition_state.value,
        },
    )


def _metadata_bool(metadata: Mapping[str, Any], key: str) -> bool:
    return metadata.get(key) is True


def _nested_metadata_bool(metadata: Mapping[str, Any], outer_key: str, inner_key: str) -> bool:
    nested = metadata.get(outer_key)
    return isinstance(nested, Mapping) and nested.get(inner_key) is True


def _object_index_for_stable_id(
    envelope: DomainEnvelope,
    object_id: str,
) -> tuple[int | None, CuratableObjectEnvelope | None]:
    for index, domain_object in enumerate(envelope.objects):
        if object_id in {
            value
            for value in (domain_object.object_id, domain_object.pending_ref_id)
            if value is not None
        }:
            return index, domain_object
    return None, None


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
    _ensure_json_compatible(value, field_name="value")
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


def _curator_event(
    *,
    envelope: DomainEnvelope,
    event_type: HistoryEventKind,
    actor_id: str,
    message: str,
    details: Mapping[str, Any],
    object_ref: ObjectRef | None = None,
    field_ref: FieldRef | None = None,
) -> HistoryEvent:
    event_details = _jsonable(details)
    seed = {
        "envelope_id": envelope.envelope_id,
        "event_type": event_type.value,
        "actor_id": actor_id,
        "message": message,
        "details": event_details,
        "object_ref": object_ref.model_dump(mode="json") if object_ref else None,
        "field_ref": field_ref.model_dump(mode="json") if field_ref else None,
    }
    digest = sha256(json.dumps(seed, sort_keys=True).encode("utf-8")).hexdigest()
    return HistoryEvent(
        event_type=event_type,
        event_id=f"curator-field-patch:{digest}",
        timestamp=datetime.now(timezone.utc),
        actor_type=HistoryActorType.HUMAN,
        actor_id=actor_id,
        message=message,
        object_ref=object_ref,
        field_ref=field_ref,
        details=event_details,
    )


def _ensure_json_compatible(value: Any, *, field_name: str) -> None:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must contain only JSON-compatible values") from exc


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def _validated_envelope(envelope: DomainEnvelope) -> DomainEnvelope:
    return DomainEnvelope.model_validate(envelope.model_dump(mode="json"))


__all__ = [
    "EnvelopeFieldPatch",
    "EnvelopeFieldPatchOperation",
    "EnvelopeFieldPatchResult",
    "EnvelopeFieldPatchStatus",
    "apply_curator_field_patch",
]
