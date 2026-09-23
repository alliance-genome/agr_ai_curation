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
from src.lib.domain_packs.resolvable_values import (
    CONTRACT_KEYS,
    CURATOR_OVERRIDE_KEY,
    CURATOR_OVERRIDE_METADATA_KEY,
    LOOKUP_OUTCOME_KEY,
    OVERRULED_KEY_PREFIX,
    RESOLUTION_STATE_KEY,
    ResolvableSpec,
    ResolvableValueError,
    apply_curator_identity,
    declared_resolvable_fields,
    declared_spec_for,
)
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
from src.lib.agent_studio.profile_conformance import ResolvedGenericProfile

_MISSING = object()


def is_generic_attribute_path(path: str) -> bool:
    return path == "attributes" or path.startswith("attributes.") or path.startswith("attributes[")


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
    profile: ResolvedGenericProfile | None = None,
) -> EnvelopeFieldPatchResult:
    """Validate and apply one curator field edit to a domain envelope.

    The patch is accepted only when the expected revision matches, the object
    exists, the field path is editable under the domain pack or bound profile,
    and ``before`` matches the current object payload value. Profile attribute
    edits require the exact saved pin and closed profile conformance; other
    fields retain the domain pack's protected/editable policy.
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
    object_ref = domain_object.to_object_ref() if domain_object is not None else None
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
        if profile is not None and is_generic_attribute_path(patch.field_path):
            # The saved closed profile, not the global generic pack, owns these
            # paths. Validate the replacement below before any history mutation.
            profile.require_receipt(domain_object.metadata.get("generic_profile_ref", {}))
        elif field_definition is None:
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
                    f"field_path '{patch.field_path}' is not declared editable"
                )
            else:
                errors.extend(
                    _resolvable_edit_errors(
                        domain_object,
                        patch,
                        declared_resolvable_fields(domain_pack.metadata, domain_object.object_type),
                    )
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
    if profile is not None and is_generic_attribute_path(patch.field_path):
        staged_payload["attributes"] = profile.patch_attributes(
            staged_payload.get("attributes", {}),
            [{"field_path": patch.field_path, "value": patch.value}],
            candidate_id=patch.object_id,
        )
    override_audit: dict[str, Any] | None = None
    try:
        if profile is None or not is_generic_attribute_path(patch.field_path):
            handled, override_audit = _apply_resolvable_edit(
                staged_payload,
                patch,
                domain_pack=domain_pack,
                object_type=domain_object.object_type,
                actor_id=actor_id,
            )
            if not handled:
                set_payload_value(staged_payload, patch.field_path, patch.value)
    except (ValueError, ResolvableValueError) as exc:
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

    updated_objects = list(envelope.extracted_objects)
    object_metadata = dict(domain_object.metadata)
    if override_audit is not None:
        # Audit trail of curator validation overrides: who, when, what was there before.
        object_metadata[CURATOR_OVERRIDE_METADATA_KEY] = [
            *object_metadata.get(CURATOR_OVERRIDE_METADATA_KEY, []),
            {**override_audit, "field_path": patch.field_path, "patch_id": patch.patch_id},
        ]
    updated_object = domain_object.model_copy(
        update={"payload": staged_payload, "metadata": object_metadata}
    )
    updated_objects[object_index] = updated_object

    field_ref = FieldRef(
        object_ref=updated_object.to_object_ref(),
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
                "extracted_objects": updated_objects,
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


def _resolvable_target(
    field_path: str, resolvable_fields: Mapping[str, ResolvableSpec],
) -> tuple[str, ResolvableSpec, str | None] | None:
    """(value path, spec, edited key or None for the whole value) when a patch edits a declared resolvable value."""

    tokens = parse_field_path(field_path)
    spec = declared_spec_for(resolvable_fields, tokens)
    if spec is not None:
        return field_path, spec, None
    if tokens and isinstance(tokens[-1], str):
        spec = declared_spec_for(resolvable_fields, tokens[:-1])
        if spec is not None:
            return field_path.rpartition(".")[0] if "." in field_path else "", spec, tokens[-1]
    return None


def _set_by_validation(key: str) -> bool:
    return key in CONTRACT_KEYS or key.startswith(OVERRULED_KEY_PREFIX)


def _resolvable_edit_errors(
    domain_object: CuratableObjectEnvelope,
    patch: EnvelopeFieldPatch,
    resolvable_fields: Mapping[str, ResolvableSpec],
) -> list[str]:
    """Curators edit a resolvable value's identity, never its paper wording or validation state."""

    target = _resolvable_target(patch.field_path, resolvable_fields)
    if target is None:
        return []
    value_path, _spec, key = target
    if key is not None:
        if _set_by_validation(key):
            return [
                f"field_path '{patch.field_path}' is set by validation; edit the value's identity instead"
            ]
        return []
    if not isinstance(patch.value, Mapping):
        return [f"field_path '{patch.field_path}' is a resolvable value; edit its identity keys"]
    current = _payload_value(domain_object.payload, value_path)
    current = current if isinstance(current, Mapping) else {}
    changed = sorted(
        str(item_key) for item_key, item in patch.value.items()
        if _set_by_validation(str(item_key)) and item != current.get(item_key)
    )
    if changed:
        return [
            f"field_path '{patch.field_path}' cannot change {', '.join(changed)}; "
            "those are set by validation"
        ]
    return []


def _apply_resolvable_edit(
    payload: dict[str, Any],
    patch: EnvelopeFieldPatch,
    *,
    domain_pack: LoadedDomainPack,
    object_type: str,
    actor_id: str,
) -> tuple[bool, dict[str, Any] | None]:
    """Apply a curator's edit of a declared resolvable value's identity as a validation override.

    Returns (handled, override audit record). Not handled means an ordinary
    field edit the caller sets; handled without a record means only a
    value's other keys changed. Declared mirror copies follow an override.
    """

    resolvable_fields = declared_resolvable_fields(domain_pack.metadata, object_type)
    target = _resolvable_target(patch.field_path, resolvable_fields)
    if target is None:
        return False, None
    value_path, spec, key = target
    if key is not None and key not in spec.identity_keys:
        return False, None
    container = _payload_value(payload, value_path) if value_path else payload
    if not isinstance(container, dict):
        set_payload_value(payload, value_path, {})
        container = _payload_value(payload, value_path)
    if key is not None:
        edits = {key: copy.deepcopy(patch.value)}
    else:
        new_value = dict(patch.value)
        for item_key, item in new_value.items():
            if item_key not in spec.identity_keys and not _set_by_validation(str(item_key)):
                container[item_key] = copy.deepcopy(item)
        edits = {
            identity_key: copy.deepcopy(new_value[identity_key])
            for identity_key in spec.identity_keys
            if identity_key in new_value and new_value[identity_key] != container.get(identity_key)
        }
        if not edits:
            return True, None
    audit = apply_curator_identity(
        container,
        edits,
        identity_keys=spec.identity_keys,
        actor_id=actor_id,
        at=datetime.now(timezone.utc).isoformat(),
    )
    _follow_declared_mirrors(payload, value_path, container, domain_pack, object_type, resolvable_fields)
    return True, {**audit, "value_path": value_path}


def _follow_declared_mirrors(
    payload: dict[str, Any],
    value_path: str,
    container: Mapping[str, Any],
    domain_pack: LoadedDomainPack,
    object_type: str,
    resolvable_fields: Mapping[str, ResolvableSpec],
) -> None:
    """A declared mirror copy (``materializes_to_field_paths``) takes the curator's identity and state."""

    object_definition = next(
        (obj for obj in domain_pack.metadata.object_definitions if obj.object_type == object_type), None,
    )
    if object_definition is None:
        return
    prefix = f"{value_path}." if value_path else ""
    mirror_values: set[str] = set()
    for field_definition in object_definition.fields:
        key = field_definition.field_path[len(prefix):] if field_definition.field_path.startswith(prefix) else ""
        if not key or "." in key or key not in container:
            continue
        for mirror in field_definition.metadata.get("materializes_to_field_paths") or []:
            mirror_path = str(mirror).strip()
            mirror_value_path = mirror_path.rpartition(".")[0]
            if not mirror_value_path or declared_spec_for(
                resolvable_fields, parse_field_path(mirror_value_path)
            ) is None:
                continue
            set_payload_value(payload, mirror_path, copy.deepcopy(container[key]))
            mirror_values.add(mirror_value_path)
    for mirror_value_path in mirror_values:
        mirror = _payload_value(payload, mirror_value_path)
        for contract_key in (RESOLUTION_STATE_KEY, LOOKUP_OUTCOME_KEY, CURATOR_OVERRIDE_KEY):
            if contract_key in container:
                mirror[contract_key] = copy.deepcopy(container[contract_key])
            else:
                mirror.pop(contract_key, None)


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
    by_path = {field.field_path: field for field in object_definition.fields}
    if field_path in by_path:
        return by_path[field_path]
    # One element of a multivalued list (e.g. ``codes[1].curie``) takes the declaration of
    # its bare path, but only when every indexed segment is a declared multivalued field.
    bare: list[str] = []
    for part in parse_field_path(field_path):
        if isinstance(part, int):
            prefix = by_path.get(".".join(bare))
            if prefix is None or not prefix.multivalued:
                return None
            continue
        bare.append(part)
    return by_path.get(".".join(bare))


def _field_editability(
    field_definition: DomainPackFieldDefinition,
) -> tuple[bool, dict[str, Any]]:
    metadata = field_definition.metadata
    protected = (
        _metadata_bool(metadata, "protected")
        or _nested_metadata_bool(metadata, "edit", "protected")
    )
    declared_editable = (
        _metadata_bool(metadata, "editable")
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


def _nested_metadata_bool(
    metadata: Mapping[str, Any], outer_key: str, inner_key: str
) -> bool:
    nested = metadata.get(outer_key)
    return isinstance(nested, Mapping) and nested.get(inner_key) is True


def _object_index_for_stable_id(
    envelope: DomainEnvelope,
    object_id: str,
) -> tuple[int | None, CuratableObjectEnvelope | None]:
    for index, domain_object in enumerate(envelope.extracted_objects):
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


def set_payload_value(payload: dict[str, Any], field_path: str, value: Any) -> None:
    """Set a canonical object/index path without coercion or sparse arrays."""
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
        if not isinstance(current, list) or isinstance(
            current, (str, bytes, bytearray)
        ):
            raise ValueError(f"Cannot set '{field_path}' through non-array parent")
        if part == len(current):
            current.append([] if isinstance(next_part, int) else {})
        if part >= len(current):
            raise ValueError(
                f"Cannot set '{field_path}' because a list index is missing"
            )
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
        raise ValueError(
            f"{field_name} must contain only JSON-compatible values"
        ) from exc


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
