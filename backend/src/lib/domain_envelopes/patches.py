"""Provider-neutral field-path patches for persisted domain envelopes."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from typing import Any, Mapping, Sequence
from uuid import uuid4

from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.resolvable_values import (
    CONTRACT_KEYS,
    CURATOR_OVERRIDE_KEY,
    CURATOR_OVERRIDE_METADATA_KEY,
    EXTRACTOR_PROPOSAL_PREFIX,
    LOOKUP_OUTCOME_KEY,
    OVERRULED_KEY_PREFIX,
    RESOLUTION_STATE_KEY,
    ResolvableSpec,
    ResolvableValueError,
    apply_curator_identity,
    declared_resolvable_fields,
    declared_spec_for,
    has_resolution_state,
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
    ValidationFinding,
    ValidationFindingStatus,
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
    # One atomic curator override of a declared resolvable value's identity:
    # ``field_path`` names one identity field of the value (at the object root
    # too), ``value`` maps identity keys to their new values and ``before``
    # maps the same keys to their current values.
    REPLACE_IDENTITY = "replace_identity"
    # Remove one element of a list of resolvable values: ``field_path`` names
    # the element (``list[i]``), ``before`` is the stored element, ``value`` null.
    REMOVE = "remove"


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

    if domain_object is None or object_index is None:
        errors.append(f"object_id '{patch.object_id}' was not found in the envelope")
    elif not errors:
        field_definition = _field_definition_for(
            validation_registry,
            domain_object.object_type,
            patch.field_path,
        )
        resolvable_fields = declared_resolvable_fields(domain_pack.metadata, domain_object.object_type)
        override = _override_target(patch, resolvable_fields)
        identity_leaf = _identity_leaf_target(patch, resolvable_fields)
        if profile is not None and is_generic_attribute_path(patch.field_path):
            # The saved closed profile, not the global generic pack, owns these
            # paths. Validate the replacement below before any history mutation.
            if patch.operation is EnvelopeFieldPatchOperation.REPLACE_IDENTITY:
                errors.append(f"operation '{patch.operation.value}' is not supported for profile fields")
            profile.require_receipt(domain_object.metadata.get("generic_profile_ref", {}))
        elif patch.operation is EnvelopeFieldPatchOperation.REPLACE_IDENTITY and override is None:
            errors.append(
                f"field_path '{patch.field_path}' is not an identity field of a declared resolvable value"
            )
        elif patch.operation is EnvelopeFieldPatchOperation.REMOVE:
            errors.extend(
                _removal_errors(domain_object, patch, resolvable_fields, registry=validation_registry)
            )
        elif override is not None:
            errors.extend(
                _override_errors(domain_object, patch, *override, registry=validation_registry)
            )
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
                errors.extend(_resolvable_edit_errors(patch, resolvable_fields))
                if identity_leaf is not None and not errors:
                    # One identity field of a declared value: the override rules of the whole value apply.
                    errors.extend(
                        _override_scope_errors(domain_object, patch, *identity_leaf, registry=validation_registry)
                    )

        current_before = _current_before(domain_object.payload, patch, override)
        identity_without_value = patch.operation is EnvelopeFieldPatchOperation.REPLACE_IDENTITY and override is None
        if not identity_without_value and current_before != patch.before:
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
    override_audit: dict[str, Any] | None = None
    if profile is not None and is_generic_attribute_path(patch.field_path):
        if patch.operation is EnvelopeFieldPatchOperation.REMOVE:
            staged_payload["attributes"], override_audit = profile.remove_curator_element(
                staged_payload.get("attributes", {}),
                patch.field_path,
                actor_id=actor_id,
                at=datetime.now(timezone.utc).isoformat(),
            )
        else:
            # A profile value's identity edit is a curator override, like a pack value's.
            staged_payload["attributes"], override_audit = profile.apply_curator_edit(
                staged_payload.get("attributes", {}),
                patch.field_path,
                patch.value,
                actor_id=actor_id,
                at=datetime.now(timezone.utc).isoformat(),
            )
    try:
        if patch.operation is EnvelopeFieldPatchOperation.REMOVE and (
            profile is None or not is_generic_attribute_path(patch.field_path)
        ):
            override_audit = _remove_list_element(staged_payload, patch.field_path, actor_id=actor_id)
        elif profile is None or not is_generic_attribute_path(patch.field_path):
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
    settled_envelope = envelope.model_copy(update={"extracted_objects": updated_objects})
    if override_audit is not None:
        # The value's open findings follow the curator's decision (see _settle_findings).
        settled_envelope = _settle_findings(
            settled_envelope,
            updated_object,
            override_audit,
            domain_pack=domain_pack,
            registry=validation_registry,
            actor_id=actor_id,
        )

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
        settled_envelope.model_copy(
            update={"history": [*settled_envelope.history, field_event, accepted_event]}
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
    """Keys extraction or validation writes: the contract keys, overruled and proposed identities."""

    return key in CONTRACT_KEYS or key.startswith((OVERRULED_KEY_PREFIX, EXTRACTOR_PROPOSAL_PREFIX))


# Which identity a whole-value curator override may change, by the keys the value declares.
_OVERRIDE_SCOPE_MESSAGE = {
    (True, True): "only the identifier and name can be changed in a curator override",
    (True, False): "only the identifier can be changed in a curator override",
    (False, True): "only the name can be changed in a curator override",
}


def resolvable_identity_field(
    domain_pack: LoadedDomainPack, object_type: str, field_path: str,
) -> tuple[str, str] | None:
    """(value path, identity key) when ``field_path`` is an identity field of a declared resolvable value."""

    target = _resolvable_target(field_path, declared_resolvable_fields(domain_pack.metadata, object_type))
    if target is None:
        return None
    value_path, spec, key = target
    return (value_path, key) if key in spec.identity_keys else None


def curator_override_allowed(
    field_definitions: Mapping[str, DomainPackFieldDefinition], value_path: str, spec: ResolvableSpec,
) -> dict[str, bool]:
    """The review screen's view of the override rules for one declared value.

    ``container_protected``: the value's own field is protected.
    ``overridable``: a curator may override it (not protected, every identity
    field declared editable), as the patch rules require. A saved profile's
    values are always overridable.
    """

    if value_path and is_generic_attribute_path(value_path):
        return {"container_protected": False, "overridable": True}
    container = field_definitions.get(_bare_path(value_path)) if value_path else None
    protected = container is not None and _field_editability(container)[1]["protected"]
    leaves = [field_definitions.get(_bare_path(f"{value_path}.{key}" if value_path else key)) for key in spec.identity_keys]
    editable = all(leaf is not None and _field_editability(leaf)[0] for leaf in leaves)
    return {"container_protected": protected, "overridable": not protected and editable}


def resolvable_identity_keys(domain_pack: LoadedDomainPack, object_type: str, value_path: str) -> tuple[str, ...]:
    """The identity keys of the declared resolvable value at ``value_path`` ("" for the root)."""

    spec = declared_spec_for(
        declared_resolvable_fields(domain_pack.metadata, object_type),
        parse_field_path(value_path) if value_path else (),
    )
    return spec.identity_keys if spec is not None else ()


def _override_target(
    patch: EnvelopeFieldPatch, resolvable_fields: Mapping[str, ResolvableSpec],
) -> tuple[str, ResolvableSpec] | None:
    """(value path, spec) when a patch overrides a declared resolvable value as a whole.

    That is a whole-value replace of the value, or a ``replace_identity``
    patch naming one of its identity fields.
    """

    target = _resolvable_target(patch.field_path, resolvable_fields)
    if target is None:
        return None
    value_path, spec, key = target
    if patch.operation is EnvelopeFieldPatchOperation.REPLACE_IDENTITY:
        return (value_path, spec) if key in spec.identity_keys else None
    return (value_path, spec) if key is None else None


def _override_errors(
    domain_object: CuratableObjectEnvelope,
    patch: EnvelopeFieldPatch,
    value_path: str,
    spec: ResolvableSpec,
    *,
    registry: DomainPackValidationRegistry,
) -> list[str]:
    """A whole-value override needs every identity field editable and changes only the identity."""

    scope = _override_scope_errors(domain_object, patch, value_path, spec, registry=registry)
    if scope:
        return scope
    if not isinstance(patch.value, Mapping) or not patch.value:
        return [f"field_path '{patch.field_path}' is a resolvable value; send its identity keys"]
    if patch.operation is EnvelopeFieldPatchOperation.REPLACE_IDENTITY:
        changed = sorted(str(key) for key in patch.value if key not in spec.identity_keys)
    else:
        current = _payload_value(domain_object.payload, value_path)
        current = current if isinstance(current, Mapping) else {}
        changed = sorted(
            str(item_key) for item_key, item in patch.value.items()
            if item_key not in spec.identity_keys and item != current.get(item_key)
        )
    if changed:
        return [
            f"field_path '{patch.field_path}' cannot change {', '.join(changed)}; "
            f"{_OVERRIDE_SCOPE_MESSAGE[(bool(spec.id_key), bool(spec.label_key))]}"
        ]
    return []


def _override_scope_errors(
    domain_object: CuratableObjectEnvelope,
    patch: EnvelopeFieldPatch,
    value_path: str,
    spec: ResolvableSpec,
    *,
    registry: DomainPackValidationRegistry,
) -> list[str]:
    """Whether a curator may override this value at all, whatever the edit's shape.

    The value's own field needs no editable flag, but a protected one blocks
    it; every identity field must be declared editable; and the value must
    exist (an index past the end of a list is no value).
    """

    container = (
        _field_definition_for(registry, domain_object.object_type, value_path) if value_path else None
    )
    if container is not None and _field_editability(container)[1]["protected"]:
        return [f"field_path '{value_path}' is protected"]
    closed = []
    for key in spec.identity_keys:
        leaf = f"{value_path}.{key}" if value_path else key
        definition = _field_definition_for(registry, domain_object.object_type, leaf)
        if definition is None or not _field_editability(definition)[0]:
            closed.append(leaf)
    if closed:
        return [
            f"field_path '{patch.field_path}' takes no curator override: "
            f"{', '.join(closed)} not declared editable"
        ]
    if value_path and any(isinstance(part, int) for part in parse_field_path(value_path)) and (
        _payload_value(domain_object.payload, value_path) is _MISSING
    ):
        return ["Edit an existing value."]
    return []


def _identity_leaf_target(
    patch: EnvelopeFieldPatch, resolvable_fields: Mapping[str, ResolvableSpec],
) -> tuple[str, ResolvableSpec] | None:
    """(value path, spec) when a plain replace edits one identity field of a declared value."""

    if patch.operation is not EnvelopeFieldPatchOperation.REPLACE:
        return None
    target = _resolvable_target(patch.field_path, resolvable_fields)
    if target is None or target[2] not in target[1].identity_keys:
        return None
    return target[0], target[1]


def _removal_errors(
    domain_object: CuratableObjectEnvelope,
    patch: EnvelopeFieldPatch,
    resolvable_fields: Mapping[str, ResolvableSpec],
    *,
    registry: DomainPackValidationRegistry,
) -> list[str]:
    """A curator removes one element of a list of resolvable values it may override."""

    tokens = parse_field_path(patch.field_path)
    target = _resolvable_target(patch.field_path, resolvable_fields)
    if not isinstance(tokens[-1], int) or target is None or target[2] is not None:
        return [f"field_path '{patch.field_path}' is not an element of a list of resolvable values"]
    if patch.value is not None:
        return [f"field_path '{patch.field_path}': a removal carries no value"]
    if _payload_value(domain_object.payload, patch.field_path) is _MISSING:
        return ["Edit an existing value."]
    return _override_scope_errors(domain_object, patch, patch.field_path, target[1], registry=registry)


def _resolvable_edit_errors(
    patch: EnvelopeFieldPatch,
    resolvable_fields: Mapping[str, ResolvableSpec],
) -> list[str]:
    """Curators edit a resolvable value's identity, never its paper wording or validation state."""

    target = _resolvable_target(patch.field_path, resolvable_fields)
    if target is None:
        return []
    _value_path, _spec, key = target
    if key is not None and _set_by_validation(key):
        return [
            f"field_path '{patch.field_path}' is set by extraction or validation; "
            "edit the value's identity instead"
        ]
    return []


def _current_before(
    payload: Mapping[str, Any], patch: EnvelopeFieldPatch, override: tuple[str, ResolvableSpec] | None,
) -> Any:
    """The current value a patch's ``before`` must match.

    For ``replace_identity`` that is the current value of each identity key
    the patch names; otherwise the value at ``field_path`` (None when absent).
    """

    if patch.operation is EnvelopeFieldPatchOperation.REPLACE_IDENTITY:
        if override is None or not isinstance(patch.value, Mapping):
            return None
        container = _payload_value(payload, override[0]) if override[0] else payload
        container = container if isinstance(container, Mapping) else {}
        return {key: container.get(key) for key in patch.value}
    before_value = _payload_value(payload, patch.field_path)
    return None if before_value is _MISSING else before_value


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
    field edit the caller sets; handled without a record means a whole-value
    edit that changed nothing. Declared mirror copies follow an override.
    """

    resolvable_fields = declared_resolvable_fields(domain_pack.metadata, object_type)
    target = _resolvable_target(patch.field_path, resolvable_fields)
    if target is None:
        return False, None
    value_path, spec, key = target
    whole = patch.operation is EnvelopeFieldPatchOperation.REPLACE_IDENTITY or key is None
    if not whole and key not in spec.identity_keys:
        return False, None
    container = _payload_value(payload, value_path) if value_path else payload
    if not isinstance(container, dict):
        # A value stored before the contract as plain text keeps that text as its paper wording.
        text = str(container).strip() if isinstance(container, (str, int, float)) else ""
        set_payload_value(payload, value_path, {spec.mention_key: text} if text else {})
        container = _payload_value(payload, value_path)
    if not whole:
        edits = {key: copy.deepcopy(patch.value)}
    else:
        # Other keys cannot change (_override_errors); only the identity is applied.
        new_value = dict(patch.value)
        edits = {
            identity_key: copy.deepcopy(new_value[identity_key])
            for identity_key in spec.identity_keys
            if identity_key in new_value
        }
        if has_resolution_state(container) and all(
            item == container.get(identity_key) for identity_key, item in edits.items()
        ):
            # Nothing changes. A value stored before the contract is different: entering
            # its stored identity is the curator vouching for it, an override.
            return True, None
    audit = apply_curator_identity(
        container,
        edits,
        identity_keys=spec.identity_keys,
        id_key=spec.id_key,
        label_key=spec.label_key,
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
    """Declared mirror copies (``materializes_to_field_paths``) take the curator's identity.

    A mirror that is itself a declared resolvable value also takes the
    state; a plain mirror (e.g. a name kept beside its term) takes the value.
    A list element's mirrors are matched through its bare declared path.
    """

    object_definition = next(
        (obj for obj in domain_pack.metadata.object_definitions if obj.object_type == object_type), None,
    )
    if object_definition is None:
        return
    bare_value_path = _bare_path(value_path)
    prefix = f"{bare_value_path}." if bare_value_path else ""
    mirror_values: set[str] = set()
    for field_definition in object_definition.fields:
        key = field_definition.field_path[len(prefix):] if field_definition.field_path.startswith(prefix) else ""
        if not key or "." in key or key not in container:
            continue
        for mirror in field_definition.metadata.get("materializes_to_field_paths") or []:
            mirror_path = _concrete_mirror_path(str(mirror).strip(), value_path)
            mirror_value_path = mirror_path.rpartition(".")[0]
            if mirror_value_path and declared_spec_for(
                resolvable_fields, parse_field_path(mirror_value_path)
            ) is not None:
                set_payload_value(payload, mirror_path, copy.deepcopy(container[key]))
                mirror_values.add(mirror_value_path)
            elif not mirror_value_path or isinstance(_payload_value(payload, mirror_value_path), dict):
                set_payload_value(payload, mirror_path, copy.deepcopy(container[key]))
    for mirror_value_path in mirror_values:
        mirror = _payload_value(payload, mirror_value_path)
        for contract_key in (RESOLUTION_STATE_KEY, LOOKUP_OUTCOME_KEY, CURATOR_OVERRIDE_KEY):
            if contract_key in container:
                mirror[contract_key] = copy.deepcopy(container[contract_key])
            else:
                mirror.pop(contract_key, None)


def _bare_path(field_path: str) -> str:
    """A concrete path with its list indexes dropped (``terms[1].curie`` -> ``terms.curie``)."""

    return ".".join(str(part) for part in parse_field_path(field_path) if isinstance(part, str)) if field_path else ""


def _concrete_mirror_path(mirror_path: str, value_path: str) -> str:
    """A declared mirror path with the list indexes of ``value_path`` where their lists recur."""

    indexes: dict[str, int] = {}
    bare: list[str] = []
    for part in parse_field_path(value_path) if value_path else ():
        if isinstance(part, int):
            indexes[".".join(bare)] = part
        else:
            bare.append(part)
    tokens = parse_field_path(mirror_path)
    concrete: list[str | int] = []
    bare = []
    for position, part in enumerate(tokens):
        concrete.append(part)
        if isinstance(part, int):
            continue
        bare.append(part)
        index = indexes.get(".".join(bare))
        following = tokens[position + 1] if position + 1 < len(tokens) else None
        if index is not None and not isinstance(following, int):
            concrete.append(index)
    return _format_path(concrete)


def _format_path(parts: Sequence[str | int]) -> str:
    text = ""
    for part in parts:
        text = f"{text}[{part}]" if isinstance(part, int) else (f"{text}.{part}" if text else part)
    return text


def _remove_list_element(payload: dict[str, Any], field_path: str, *, actor_id: str) -> dict[str, Any]:
    """Remove one list element (validated by ``_removal_errors``); returns its audit record."""

    tokens = parse_field_path(field_path)
    list_path = _format_path(tokens[:-1])
    items = _payload_value(payload, list_path)
    removed = items.pop(tokens[-1])
    return {
        "action": "removed",
        "actor_id": actor_id,
        "at": datetime.now(timezone.utc).isoformat(),
        "previous": copy.deepcopy(removed),
        "identity": None,
        "value_path": field_path,
    }


def _settle_findings(
    envelope: DomainEnvelope,
    domain_object: CuratableObjectEnvelope,
    audit: Mapping[str, Any],
    *,
    domain_pack: LoadedDomainPack,
    registry: DomainPackValidationRegistry,
    actor_id: str,
) -> DomainEnvelope:
    """Open findings follow a curator's decision on one value.

    An override resolves the value's open findings (validator results on it
    no longer block) and the binding-level validator findings whose every
    present written value is now overridden. Changing, clearing or restoring
    an override resolves an earlier disagreement warning on the value. A
    removed list element's findings are resolved and later elements'
    findings follow their new index. Only a later validator run re-raises.
    """

    from src.lib.domain_packs.materialization import (
        CURATOR_OVERRIDE_DISAGREEMENT_CODE,
        expected_writes_settled_by_overrides,
    )
    from src.lib.domain_packs.validation_findings import _history_event_for_resolved_finding

    value_path = str(audit["value_path"])
    action = audit["action"]
    ref_keys = set(domain_object.ref_keys())
    resolvable_fields = declared_resolvable_fields(domain_pack.metadata, domain_object.object_type)
    spec = declared_spec_for(resolvable_fields, parse_field_path(value_path) if value_path else ())
    root_keys = set(CONTRACT_KEYS) | set(spec.identity_keys if spec is not None else ())
    object_definition = registry.object_definitions_by_type.get(domain_object.object_type)

    def on_value(finding: ValidationFinding) -> bool:
        if finding.field_ref is None:
            return value_path == "" and finding.code == CURATOR_OVERRIDE_DISAGREEMENT_CODE
        field_path = finding.field_ref.field_path
        if value_path:
            return field_path == value_path or field_path.startswith((f"{value_path}.", f"{value_path}["))
        return str(parse_field_path(field_path)[0]) in root_keys

    def settled_binding(finding: ValidationFinding) -> bool:
        request = finding.details.get("validation_request")
        expected = request.get("expected_result_fields") if isinstance(request, Mapping) else None
        return (
            isinstance(expected, Mapping)
            and object_definition is not None
            and expected_writes_settled_by_overrides(
                domain_object.payload, expected,
                object_definition=object_definition, resolvable_fields=resolvable_fields,
            )
        )

    findings: list[ValidationFinding] = []
    events: list[HistoryEvent] = []
    for finding in envelope.validation_findings:
        target = finding.field_ref.object_ref if finding.field_ref is not None else finding.object_ref
        if (
            finding.status is not ValidationFindingStatus.OPEN
            or target is None
            or target.ref_key() not in ref_keys
        ):
            findings.append(finding)
            continue
        on = on_value(finding)
        if action == "removed":
            resolve = on
            if not on and finding.field_ref is not None:
                finding = _shifted_after_removal(finding, value_path)
        elif action == "override":
            resolve = on or settled_binding(finding)
        else:
            resolve = on and finding.code == CURATOR_OVERRIDE_DISAGREEMENT_CODE
        if resolve:
            finding = finding.model_copy(update={"status": ValidationFindingStatus.RESOLVED})
            events.append(_history_event_for_resolved_finding(envelope=envelope, finding=finding, actor_id=actor_id))
        findings.append(finding)
    return envelope.model_copy(update={"validation_findings": findings, "history": [*envelope.history, *events]})


def _shifted_after_removal(finding: ValidationFinding, removed_path: str) -> ValidationFinding:
    """A finding on a later element of the list follows that element to its new index."""

    removed = parse_field_path(removed_path)
    list_parts, removed_index = removed[:-1], removed[-1]
    assert finding.field_ref is not None
    parts = list(parse_field_path(finding.field_ref.field_path))
    if (
        len(parts) <= len(list_parts)
        or tuple(parts[: len(list_parts)]) != tuple(list_parts)
        or not isinstance(parts[len(list_parts)], int)
        or parts[len(list_parts)] <= removed_index
    ):
        return finding
    parts[len(list_parts)] -= 1
    return finding.model_copy(update={
        "field_ref": finding.field_ref.model_copy(update={"field_path": _format_path(parts)}),
    })


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
