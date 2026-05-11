"""Metadata-driven domain-pack validation registry.

Domain packs own their validator declarations.  This module keeps the core
runtime provider-agnostic by normalizing those declarations into deterministic
bindings, validator metadata entries, match results, and field-level policies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DefinitionState,
    DomainEnvelope,
    validate_field_path_syntax,
)
from src.schemas.domain_pack_metadata import (
    DomainPackFieldDefinition,
    DomainPackFieldType,
    DomainPackObjectDefinition,
)

from .registry import LoadedDomainPack


class ValidationBindingState(str, Enum):
    """Lifecycle state for a domain-pack validator binding."""

    ACTIVE = "active"
    PLANNED = "planned"
    BLOCKED = "blocked"


class ValidationRegistryError(ValueError):
    """Raised when domain-pack validation metadata is malformed."""


@dataclass(frozen=True)
class ValidatorMetadataEntry:
    """One declarative entry from ``metadata.validators``."""

    validator_id: str
    state: ValidationBindingState
    description: str = ""
    definition_state: DefinitionState = DefinitionState.STABLE
    blocked_by: str | None = None
    reason: str | None = None
    tool_name: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def identity_details(self) -> dict[str, Any]:
        """Return stable structured details suitable for findings."""

        details: dict[str, Any] = {
            "validator_id": self.validator_id,
            "binding_state": self.state.value,
            "definition_state": self.definition_state.value,
            "metadata_source": "validators",
        }
        if self.description:
            details["description"] = self.description
        if self.blocked_by:
            details["blocked_by"] = self.blocked_by
        if self.reason:
            details["reason"] = self.reason
        if self.tool_name:
            details["tool_name"] = self.tool_name
        return details


@dataclass(frozen=True)
class ValidatorBinding:
    """One normalized executable or declarative validator binding."""

    binding_id: str
    state: ValidationBindingState
    source_scope: str
    source_object_type: str | None = None
    source_field_path: str | None = None
    validator: str | None = None
    validation_kind: str | None = None
    tool_name: str | None = None
    tool_method: str | None = None
    definition_state: DefinitionState = DefinitionState.STABLE
    blocked_by: str | None = None
    reason: str | None = None
    blocking: bool = False
    required: bool = False
    allow_opt_out: bool = False
    opt_out_reason_required: bool = False
    required_only: bool = False
    applies_to_domain_pack_id: str | None = None
    object_types: tuple[str, ...] = ()
    object_roles: tuple[str, ...] = ()
    field_paths: tuple[str, ...] = ()
    field_types: tuple[DomainPackFieldType, ...] = ()
    input_fields: dict[str, Any] = field(default_factory=dict)
    expected_result_fields: dict[str, Any] = field(default_factory=dict)
    provider_projection: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_executable(self) -> bool:
        """Return whether the binding names a local callable validator."""

        return self.validator is not None

    def identity_details(self) -> dict[str, Any]:
        """Return stable structured details suitable for findings."""

        details: dict[str, Any] = {
            "validator_binding_id": self.binding_id,
            "binding_state": self.state.value,
            "definition_state": self.definition_state.value,
            "metadata_source": "validator_bindings",
            "source_scope": self.source_scope,
            "blocking": self.blocking,
            "required": self.required,
            "allow_opt_out": self.allow_opt_out,
            "opt_out_reason_required": self.opt_out_reason_required,
        }
        optional_values = {
            "validator": self.validator,
            "validation_kind": self.validation_kind,
            "tool_name": self.tool_name,
            "tool_method": self.tool_method,
            "blocked_by": self.blocked_by,
            "reason": self.reason,
            "source_object_type": self.source_object_type,
            "source_field_path": self.source_field_path,
            "applies_to_domain_pack_id": self.applies_to_domain_pack_id,
        }
        details.update(
            {key: value for key, value in optional_values.items() if value is not None}
        )
        if self.object_types:
            details["object_types"] = list(self.object_types)
        if self.object_roles:
            details["object_roles"] = list(self.object_roles)
        if self.field_paths:
            details["field_paths"] = list(self.field_paths)
        if self.field_types:
            details["field_types"] = [field_type.value for field_type in self.field_types]
        if self.input_fields:
            details["input_fields"] = dict(self.input_fields)
        if self.expected_result_fields:
            details["expected_result_fields"] = dict(self.expected_result_fields)
        if self.provider_projection:
            details["provider_projection"] = dict(self.provider_projection)
        return details


@dataclass(frozen=True)
class ValidationAttachmentOption:
    """One flow-builder attachment option derived from domain-pack metadata."""

    attachment_id: str
    domain_pack_id: str
    domain_pack_version: str | None
    validator_id: str
    state: ValidationBindingState
    scope: str
    validator_binding_id: str | None = None
    validation_kind: str | None = None
    tool_name: str | None = None
    tool_method: str | None = None
    object_type: str | None = None
    object_role: str | None = None
    field_path: str | None = None
    field_type: DomainPackFieldType | None = None
    label: str = ""
    description: str = ""
    definition_state: DefinitionState = DefinitionState.STABLE
    blocked_by: str | None = None
    reason: str | None = None
    required: bool = False
    export_blocking: bool = False
    default_enabled: bool = False
    allow_opt_out: bool = False
    opt_out_reason_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-serializable option metadata for APIs and flow payloads."""

        payload: dict[str, Any] = {
            "attachment_id": self.attachment_id,
            "domain_pack_id": self.domain_pack_id,
            "domain_pack_version": self.domain_pack_version,
            "validator_id": self.validator_id,
            "validator_binding_id": self.validator_binding_id,
            "validation_kind": self.validation_kind,
            "tool_name": self.tool_name,
            "tool_method": self.tool_method,
            "state": self.state.value,
            "scope": self.scope,
            "object_type": self.object_type,
            "object_role": self.object_role,
            "field_path": self.field_path,
            "field_type": self.field_type.value if self.field_type is not None else None,
            "label": self.label,
            "description": self.description,
            "definition_state": self.definition_state.value,
            "blocked_by": self.blocked_by,
            "reason": self.reason,
            "required": self.required,
            "export_blocking": self.export_blocking,
            "default_enabled": self.default_enabled,
            "allow_opt_out": self.allow_opt_out,
            "opt_out_reason_required": self.opt_out_reason_required,
        }
        return {key: value for key, value in payload.items() if value is not None}


@dataclass(frozen=True)
class FieldValidationPolicy:
    """Required/export-blocking validation policy for one object field."""

    domain_pack_id: str
    object_type: str
    field_path: str
    field_type: DomainPackFieldType
    required: bool
    export_blocking: bool
    definition_state: DefinitionState
    object_role: str | None = None
    model_ref: str | None = None
    provider_refs: dict[str, Any] = field(default_factory=dict)
    validator_binding_ids: tuple[str, ...] = ()
    blocking_validator_binding_ids: tuple[str, ...] = ()
    allow_opt_out: bool = False
    opt_out_reason_required: bool = False

    def identity_details(self) -> dict[str, Any]:
        """Return stable structured details suitable for findings."""

        details: dict[str, Any] = {
            "domain_pack_id": self.domain_pack_id,
            "object_type": self.object_type,
            "field_path": self.field_path,
            "field_type": self.field_type.value,
            "policy_source": "field_policy",
            "required": self.required,
            "export_blocking": self.export_blocking,
            "definition_state": self.definition_state.value,
            "allow_opt_out": self.allow_opt_out,
            "opt_out_reason_required": self.opt_out_reason_required,
        }
        if self.object_role:
            details["object_role"] = self.object_role
        if self.model_ref:
            details["model_ref"] = self.model_ref
        if self.provider_refs:
            details["provider_refs"] = dict(self.provider_refs)
        if self.validator_binding_ids:
            details["validator_binding_ids"] = list(self.validator_binding_ids)
        if self.blocking_validator_binding_ids:
            details["blocking_validator_binding_ids"] = list(
                self.blocking_validator_binding_ids
            )
        return details


@dataclass(frozen=True)
class ValidatorBindingMatch:
    """One deterministic match between a binding and an envelope target."""

    binding: ValidatorBinding
    envelope: DomainEnvelope
    object_envelope: CuratableObjectEnvelope | None = None
    object_definition: DomainPackObjectDefinition | None = None
    field_definition: DomainPackFieldDefinition | None = None
    policy: FieldValidationPolicy | None = None

    @property
    def object_type(self) -> str | None:
        if self.object_envelope is not None:
            return self.object_envelope.object_type
        if self.object_definition is not None:
            return self.object_definition.object_type
        return None

    @property
    def field_path(self) -> str | None:
        if self.field_definition is not None:
            return self.field_definition.field_path
        return None

    def target_details(self) -> dict[str, Any]:
        """Return stable structured target metadata."""

        details: dict[str, Any] = {}
        if self.object_type is not None:
            details["object_type"] = self.object_type
        if self.object_envelope is not None:
            if self.object_envelope.object_id is not None:
                details["object_id"] = self.object_envelope.object_id
            if self.object_envelope.pending_ref_id is not None:
                details["pending_ref_id"] = self.object_envelope.pending_ref_id
            if self.object_envelope.object_role is not None:
                details["object_role"] = self.object_envelope.object_role
            if self.object_envelope.model_ref is not None:
                details["object_model_ref"] = self.object_envelope.model_ref
            if self.object_envelope.schema_ref is not None:
                details["object_schema_ref"] = self.object_envelope.schema_ref.model_dump(
                    mode="json"
                )
        if self.object_definition is not None:
            if self.object_definition.model_ref is not None:
                details["definition_model_ref"] = self.object_definition.model_ref
            if self.object_definition.schema_ref is not None:
                details["definition_schema_ref"] = (
                    self.object_definition.schema_ref.model_dump(mode="json")
                )
            provider_refs = _metadata_provider_refs(self.object_definition.metadata)
            if provider_refs:
                details["object_provider_refs"] = provider_refs
        if self.field_definition is not None:
            details["field_path"] = self.field_definition.field_path
            details["field_type"] = self.field_definition.field_type.value
            details["field_required"] = self.field_definition.required
            if self.field_definition.model_ref is not None:
                details["field_model_ref"] = self.field_definition.model_ref
            provider_refs = _metadata_provider_refs(self.field_definition.metadata)
            if provider_refs:
                details["field_provider_refs"] = provider_refs
        if self.policy is not None:
            details["field_policy"] = self.policy.identity_details()
        return details


@dataclass(frozen=True)
class DomainPackValidationRegistry:
    """Validation metadata normalized for one loaded domain pack."""

    domain_pack: LoadedDomainPack
    validator_metadata: tuple[ValidatorMetadataEntry, ...]
    bindings: tuple[ValidatorBinding, ...]
    field_policies: tuple[FieldValidationPolicy, ...]

    @classmethod
    def from_domain_pack(cls, domain_pack: LoadedDomainPack) -> "DomainPackValidationRegistry":
        """Build a validation registry from one loaded domain pack."""

        metadata = domain_pack.metadata
        object_definitions = {
            object_definition.object_type: object_definition
            for object_definition in metadata.object_definitions
        }
        validator_metadata = _collect_validator_metadata(metadata.metadata)
        bindings = _collect_validator_bindings(metadata.metadata, object_definitions)

        for object_definition in metadata.object_definitions:
            bindings.extend(
                _collect_validator_bindings(
                    object_definition.metadata,
                    object_definitions,
                    source_scope="object",
                    source_object_type=object_definition.object_type,
                )
            )
            for field_definition in object_definition.fields:
                bindings.extend(
                    _collect_validator_bindings(
                        field_definition.metadata,
                        object_definitions,
                        source_scope="field",
                        source_object_type=object_definition.object_type,
                        source_field_path=field_definition.field_path,
                        source_field_type=field_definition.field_type,
                    )
                )

        normalized_bindings = tuple(
            sorted(bindings, key=lambda binding: (binding.state.value, binding.binding_id))
        )
        return cls(
            domain_pack=domain_pack,
            validator_metadata=tuple(
                sorted(
                    validator_metadata,
                    key=lambda item: (item.state.value, item.validator_id),
                )
            ),
            bindings=normalized_bindings,
            field_policies=_build_field_policies(domain_pack, normalized_bindings),
        )

    @property
    def object_definitions_by_type(self) -> dict[str, DomainPackObjectDefinition]:
        """Return object definitions keyed by object type."""

        return {
            object_definition.object_type: object_definition
            for object_definition in self.domain_pack.metadata.object_definitions
        }

    @property
    def field_policies_by_key(self) -> dict[tuple[str, str], FieldValidationPolicy]:
        """Return field policies keyed by ``(object_type, field_path)``."""

        return {
            (policy.object_type, policy.field_path): policy
            for policy in self.field_policies
        }

    def policy_for(
        self,
        object_type: str,
        field_path: str,
    ) -> FieldValidationPolicy | None:
        """Return one field policy by object type and field path."""

        return self.field_policies_by_key.get((object_type, field_path))

    def match_bindings(
        self,
        envelope: DomainEnvelope,
        *,
        states: Iterable[ValidationBindingState | str] | None = None,
    ) -> tuple[ValidatorBindingMatch, ...]:
        """Return deterministic binding matches for one envelope."""

        selected_states = (
            {ValidationBindingState(state) for state in states}
            if states is not None
            else set(ValidationBindingState)
        )
        object_definitions = self.object_definitions_by_type
        policies_by_key = self.field_policies_by_key

        matches: list[ValidatorBindingMatch] = []
        for binding in self.bindings:
            if binding.state not in selected_states:
                continue
            if (
                binding.applies_to_domain_pack_id is not None
                and binding.applies_to_domain_pack_id != envelope.domain_pack_id
            ):
                continue

            matched_objects = _matching_objects(
                binding=binding,
                envelope=envelope,
                object_definitions=object_definitions,
            )
            if not matched_objects:
                if _binding_has_target_constraints(binding):
                    continue
                matches.append(ValidatorBindingMatch(binding=binding, envelope=envelope))
                continue

            for object_envelope, object_definition in matched_objects:
                matched_fields = _matching_fields(binding, object_definition)
                if matched_fields:
                    for field_definition in matched_fields:
                        matches.append(
                            ValidatorBindingMatch(
                                binding=binding,
                                envelope=envelope,
                                object_envelope=object_envelope,
                                object_definition=object_definition,
                                field_definition=field_definition,
                                policy=policies_by_key.get(
                                    (
                                        object_envelope.object_type,
                                        field_definition.field_path,
                                    )
                                ),
                            )
                        )
                    continue

                if _binding_has_field_constraints(binding):
                    continue

                matches.append(
                    ValidatorBindingMatch(
                        binding=binding,
                        envelope=envelope,
                        object_envelope=object_envelope,
                        object_definition=object_definition,
                    )
                )

        return tuple(matches)

    def validation_attachment_options(self) -> tuple[ValidationAttachmentOption, ...]:
        """Return deterministic flow-builder validation attachment options."""

        options: list[ValidationAttachmentOption] = [
            _metadata_attachment_option(
                domain_pack=self.domain_pack,
                entry=entry,
            )
            for entry in self.validator_metadata
        ]

        policies_by_binding_id: dict[str, list[FieldValidationPolicy]] = {}
        for policy in self.field_policies:
            for binding_id in policy.validator_binding_ids:
                policies_by_binding_id.setdefault(binding_id, []).append(policy)

        object_definitions = self.object_definitions_by_type
        for binding in self.bindings:
            if (
                binding.applies_to_domain_pack_id is not None
                and binding.applies_to_domain_pack_id != self.domain_pack.pack_id
            ):
                continue

            matched_policies = tuple(policies_by_binding_id.get(binding.binding_id, ()))
            if _binding_has_field_constraints(binding):
                for policy in matched_policies:
                    options.append(
                        _binding_attachment_option(
                            domain_pack=self.domain_pack,
                            binding=binding,
                            scope="field",
                            object_type=policy.object_type,
                            object_role=policy.object_role,
                            field_path=policy.field_path,
                            field_type=policy.field_type,
                            export_blocking=policy.export_blocking or binding.blocking,
                        )
                    )
                continue

            matched_objects = _binding_target_object_definitions(
                binding,
                object_definitions,
            )
            if matched_objects:
                for object_definition in matched_objects:
                    options.append(
                        _binding_attachment_option(
                            domain_pack=self.domain_pack,
                            binding=binding,
                            scope="object",
                            object_type=object_definition.object_type,
                            object_role=_metadata_object_role(object_definition.metadata),
                            export_blocking=binding.blocking,
                        )
                    )
                continue

            options.append(
                _binding_attachment_option(
                    domain_pack=self.domain_pack,
                    binding=binding,
                    scope="pack",
                    export_blocking=binding.blocking,
                )
            )

        return tuple(sorted(options, key=lambda option: option.attachment_id))


def _collect_validator_metadata(
    owner_metadata: Mapping[str, Any],
) -> list[ValidatorMetadataEntry]:
    raw_validators = owner_metadata.get("validators")
    entries: list[ValidatorMetadataEntry] = []
    for state, raw_item in _iter_stateful_metadata_items(raw_validators, "validators"):
        validator_id = _required_string(raw_item, "validator_id", "validators")
        entries.append(
            ValidatorMetadataEntry(
                validator_id=validator_id,
                state=state,
                description=_optional_string(raw_item.get("description")),
                definition_state=_definition_state(raw_item),
                blocked_by=_optional_string(raw_item.get("blocked_by")),
                reason=_optional_string(raw_item.get("reason")),
                tool_name=_optional_string(raw_item.get("tool_name")),
                raw=dict(raw_item),
            )
        )
    return entries


def _collect_validator_bindings(
    owner_metadata: Mapping[str, Any],
    object_definitions: Mapping[str, DomainPackObjectDefinition],
    *,
    source_scope: str = "pack",
    source_object_type: str | None = None,
    source_field_path: str | None = None,
    source_field_type: DomainPackFieldType | None = None,
) -> list[ValidatorBinding]:
    raw_bindings = owner_metadata.get("validator_bindings")
    bindings: list[ValidatorBinding] = []
    for state, raw_item in _iter_stateful_metadata_items(
        raw_bindings,
        "validator_bindings",
    ):
        applies_to = _optional_mapping(raw_item.get("applies_to"), "applies_to")
        object_types = _coerce_string_tuple(
            applies_to.get("object_types", raw_item.get("object_types"))
        )
        field_paths = _coerce_string_tuple(
            applies_to.get("field_paths", raw_item.get("field_paths"))
        )
        field_types = _coerce_field_type_tuple(
            applies_to.get("field_types", raw_item.get("field_types"))
        )

        inferred_object_types, inferred_field_paths = _infer_targets_from_binding(
            raw_item,
            object_definitions,
            source_object_type=source_object_type,
        )
        if not object_types:
            object_types = inferred_object_types
        if not field_paths:
            field_paths = inferred_field_paths

        if source_object_type is not None and not object_types:
            object_types = (source_object_type,)
        if source_field_path is not None and not field_paths:
            field_paths = (source_field_path,)
        if source_field_type is not None and not field_types:
            field_types = (source_field_type,)

        blocking = _optional_bool(raw_item.get("blocking"))
        required = _optional_bool_with_default(
            raw_item.get("required"),
            False,
        )
        allow_opt_out = _optional_bool_with_default(
            raw_item.get("allow_opt_out"),
            state is ValidationBindingState.ACTIVE,
        )

        bindings.append(
            ValidatorBinding(
                binding_id=_required_string(raw_item, "binding_id", "validator_bindings"),
                state=state,
                source_scope=source_scope,
                source_object_type=source_object_type,
                source_field_path=source_field_path,
                validator=_optional_string(raw_item.get("validator")),
                validation_kind=_optional_string(raw_item.get("validation_kind")),
                tool_name=_optional_string(raw_item.get("tool_name")),
                tool_method=_optional_string(raw_item.get("tool_method")),
                definition_state=_definition_state(raw_item),
                blocked_by=_optional_string(raw_item.get("blocked_by")),
                reason=_optional_string(raw_item.get("reason")),
                blocking=blocking,
                required=required,
                allow_opt_out=allow_opt_out,
                opt_out_reason_required=_optional_bool_with_default(
                    raw_item.get("opt_out_reason_required"),
                    False,
                ),
                required_only=_optional_bool(raw_item.get("required_only")),
                applies_to_domain_pack_id=_optional_string(
                    applies_to.get("domain_pack_id", raw_item.get("domain_pack_id"))
                ),
                object_types=object_types,
                object_roles=_coerce_string_tuple(
                    applies_to.get("object_roles", raw_item.get("object_roles"))
                ),
                field_paths=tuple(validate_field_path_syntax(path) for path in field_paths),
                field_types=field_types,
                input_fields=dict(_optional_mapping(raw_item.get("input_fields"), "input_fields")),
                expected_result_fields=dict(
                    _optional_mapping(
                        raw_item.get("expected_result_fields"),
                        "expected_result_fields",
                    )
                ),
                provider_projection=_provider_projection(raw_item),
                raw=dict(raw_item),
            )
        )
    return bindings


def _provider_projection(raw_item: Mapping[str, Any]) -> dict[str, Any]:
    validation_kind = _optional_string(raw_item.get("validation_kind"))
    provider = _projection_provider(raw_item)
    provider_fields = _provider_projection_fields(raw_item)
    target: dict[str, Any] = {}
    input_fields = _optional_mapping(raw_item.get("input_fields"), "input_fields")
    expected_result_fields = _optional_mapping(
        raw_item.get("expected_result_fields"),
        "expected_result_fields",
    )
    if input_fields:
        target["input_fields"] = dict(input_fields)
    if expected_result_fields:
        target["expected_result_fields"] = dict(expected_result_fields)

    projection: dict[str, Any] = {}
    if provider:
        projection["provider"] = provider
    if validation_kind:
        projection["projection_type"] = validation_kind
    if target:
        projection["target"] = target
    if provider_fields:
        projection["provider_fields"] = provider_fields
    return projection


def _projection_provider(raw_item: Mapping[str, Any]) -> str | None:
    return _optional_string(raw_item.get("provider"))


def _provider_projection_fields(raw_item: Mapping[str, Any]) -> dict[str, Any]:
    provider_fields: dict[str, Any] = {}
    for key in (
        "table",
        "tables",
        "expected_db_target",
        "expected_db_targets",
        "target_terms",
        "tool_name",
        "tool_method",
    ):
        value = raw_item.get(key)
        if value is not None:
            provider_fields[key] = value
    return provider_fields


def _iter_stateful_metadata_items(
    raw_items: Any,
    field_name: str,
) -> Iterable[tuple[ValidationBindingState, Mapping[str, Any]]]:
    if raw_items is None:
        return ()
    if isinstance(raw_items, list):
        return tuple(
            (
                _state_from_item(raw_item, None, field_name),
                _required_mapping_item(raw_item, field_name),
            )
            for raw_item in raw_items
        )
    if isinstance(raw_items, Mapping):
        state_keys = {state.value for state in ValidationBindingState}
        present_state_keys = state_keys.intersection(raw_items)
        if present_state_keys:
            normalized: list[tuple[ValidationBindingState, Mapping[str, Any]]] = []
            for state in ValidationBindingState:
                state_items = raw_items.get(state.value)
                if state_items is None:
                    continue
                if not isinstance(state_items, list):
                    raise ValidationRegistryError(
                        f"{field_name}.{state.value} must be a list"
                    )
                normalized.extend(
                    (
                        _state_from_item(raw_item, state, field_name),
                        _required_mapping_item(raw_item, field_name),
                    )
                    for raw_item in state_items
                )
            return tuple(normalized)

        return (
            (
                _state_from_item(raw_items, None, field_name),
                _required_mapping_item(raw_items, field_name),
            ),
        )

    raise ValidationRegistryError(
        f"{field_name} must be a mapping, a list, or null; found {type(raw_items).__name__}"
    )


def _state_from_item(
    raw_item: Any,
    collection_state: ValidationBindingState | None,
    field_name: str,
) -> ValidationBindingState:
    if not isinstance(raw_item, Mapping):
        raise ValidationRegistryError(
            f"{field_name} items must be mappings; found {type(raw_item).__name__}"
        )
    raw_state = _raw_item_state(raw_item, field_name)
    if raw_state is None:
        # Domain-pack validator items are active unless scoped metadata says otherwise.
        return collection_state or ValidationBindingState.ACTIVE
    try:
        item_state = ValidationBindingState(str(raw_state))
    except ValueError as exc:
        raise ValidationRegistryError(
            f"{field_name} item state must be active, planned, or blocked"
        ) from exc
    if collection_state is not None and item_state is not collection_state:
        raise ValidationRegistryError(
            f"{field_name}.{collection_state.value} item declares conflicting "
            f"state '{item_state.value}'"
        )
    return item_state


def _required_mapping_item(raw_item: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(raw_item, Mapping):
        raise ValidationRegistryError(
            f"{field_name} items must be mappings; found {type(raw_item).__name__}"
        )
    return raw_item


def _raw_item_state(
    raw_item: Mapping[str, Any],
    field_name: str,
) -> Any:
    raw_status = raw_item.get("status")
    raw_state = raw_item.get("state")
    if (
        raw_status is not None
        and raw_state is not None
        and str(raw_status) != str(raw_state)
    ):
        raise ValidationRegistryError(
            f"{field_name} item declares conflicting status/state values"
        )
    return raw_status if raw_status is not None else raw_state


def _optional_mapping(raw_item: Any, field_name: str) -> Mapping[str, Any]:
    if raw_item is None:
        return {}
    if not isinstance(raw_item, Mapping):
        raise ValidationRegistryError(f"{field_name} must be a mapping")
    return raw_item


def _required_string(
    raw_item: Mapping[str, Any],
    key: str,
    field_name: str,
) -> str:
    value = raw_item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationRegistryError(f"{field_name}.{key} must be a non-empty string")
    if value != value.strip():
        raise ValidationRegistryError(
            f"{field_name}.{key} must not have leading or trailing whitespace"
        )
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_bool(value: Any) -> bool:
    return bool(value) if value is not None else False


def _optional_bool_with_default(value: Any, default: bool) -> bool:
    return bool(value) if value is not None else default


def _definition_state(raw_item: Mapping[str, Any]) -> DefinitionState:
    raw_state = raw_item.get("definition_state")
    if raw_state is None:
        return DefinitionState.STABLE
    try:
        return DefinitionState(str(raw_state))
    except ValueError as exc:
        raise ValidationRegistryError(
            "definition_state must be stable, draft, in_development, or deprecated"
        ) from exc


def _coerce_string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        raise ValidationRegistryError(
            f"Expected a string or list of strings, found {type(value).__name__}"
        )

    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str) or not item.strip():
            raise ValidationRegistryError("Expected non-empty string values")
        clean_item = item.strip()
        if clean_item in seen:
            continue
        normalized.append(clean_item)
        seen.add(clean_item)
    return tuple(normalized)


def _coerce_field_type_tuple(value: Any) -> tuple[DomainPackFieldType, ...]:
    field_types: list[DomainPackFieldType] = []
    seen: set[DomainPackFieldType] = set()
    for raw_field_type in _coerce_string_tuple(value):
        try:
            field_type = DomainPackFieldType(raw_field_type)
        except ValueError as exc:
            raise ValidationRegistryError(
                f"Unknown domain-pack field type '{raw_field_type}'"
            ) from exc
        if field_type in seen:
            continue
        field_types.append(field_type)
        seen.add(field_type)
    return tuple(field_types)


def _infer_targets_from_binding(
    raw_item: Mapping[str, Any],
    object_definitions: Mapping[str, DomainPackObjectDefinition],
    *,
    source_object_type: str | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    candidate_values: list[str] = []
    for key in ("input_fields", "expected_result_fields"):
        raw_mapping = raw_item.get(key)
        if isinstance(raw_mapping, Mapping):
            candidate_values.extend(_iter_candidate_target_strings(raw_mapping.values()))
    raw_terms = raw_item.get("target_terms")
    if isinstance(raw_terms, list):
        candidate_values.extend(_iter_candidate_target_strings(raw_terms))

    object_types: list[str] = []
    field_paths: list[str] = []
    seen_object_types: set[str] = set()
    seen_field_paths: set[str] = set()
    for value in candidate_values:
        for object_type, field_path in _infer_field_targets_for_value(
            value,
            object_definitions,
            source_object_type=source_object_type,
        ):
            if object_type not in seen_object_types:
                object_types.append(object_type)
                seen_object_types.add(object_type)
            if field_path not in seen_field_paths:
                field_paths.append(field_path)
                seen_field_paths.add(field_path)

    return tuple(object_types), tuple(field_paths)


def _iter_candidate_target_strings(values: Iterable[Any]) -> Iterable[str]:
    for value in values:
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                yield normalized
            continue
        if isinstance(value, Mapping):
            yield from _iter_candidate_target_strings(value.values())
            continue
        if isinstance(value, list):
            yield from _iter_candidate_target_strings(value)


def _infer_field_targets_for_value(
    value: str,
    object_definitions: Mapping[str, DomainPackObjectDefinition],
    *,
    source_object_type: str | None,
) -> tuple[tuple[str, str], ...]:
    if "." in value:
        prefix, field_path = value.split(".", 1)
        object_definition = object_definitions.get(prefix)
        if object_definition is not None:
            normalized_field_path = _declared_inferred_field_path(
                object_definition,
                field_path,
            )
            if normalized_field_path is not None:
                return ((prefix, normalized_field_path),)

    candidate_object_definitions: Iterable[DomainPackObjectDefinition]
    if source_object_type is not None:
        source_object_definition = object_definitions.get(source_object_type)
        candidate_object_definitions = (
            (source_object_definition,) if source_object_definition is not None else ()
        )
    else:
        candidate_object_definitions = object_definitions.values()

    matches: list[tuple[str, str]] = []
    for object_definition in candidate_object_definitions:
        normalized_field_path = _declared_inferred_field_path(object_definition, value)
        if normalized_field_path is not None:
            matches.append((object_definition.object_type, normalized_field_path))
    return tuple(matches)


def _declared_inferred_field_path(
    object_definition: DomainPackObjectDefinition,
    field_path: str,
) -> str | None:
    """Return a declared field path match for heuristic target inference.

    Invalid syntax returns ``None`` here because inference probes candidate strings
    from binding metadata.  Explicit ``field_paths`` declarations are validated by
    ``_collect_validator_bindings`` before bindings are constructed.
    """

    try:
        normalized_field_path = validate_field_path_syntax(field_path)
    except ValueError:
        return None
    if any(
        field_definition.field_path == normalized_field_path
        for field_definition in object_definition.fields
    ):
        return normalized_field_path
    return None


def _build_field_policies(
    domain_pack: LoadedDomainPack,
    bindings: tuple[ValidatorBinding, ...],
) -> tuple[FieldValidationPolicy, ...]:
    policies: list[FieldValidationPolicy] = []
    for object_definition in domain_pack.metadata.object_definitions:
        object_role = _metadata_object_role(object_definition.metadata)
        for field_definition in object_definition.fields:
            matching_bindings = [
                binding
                for binding in bindings
                if _binding_targets_policy_field(
                    binding=binding,
                    object_definition=object_definition,
                    field_definition=field_definition,
                )
            ]
            blocking_binding_ids = tuple(
                binding.binding_id
                for binding in matching_bindings
                if binding.blocking and binding.state is ValidationBindingState.ACTIVE
            )
            opt_out_bindings = tuple(
                binding
                for binding in matching_bindings
                if binding.state is ValidationBindingState.ACTIVE
                and binding.allow_opt_out
                and (binding.blocking or binding.required)
            )
            policies.append(
                FieldValidationPolicy(
                    domain_pack_id=domain_pack.pack_id,
                    object_type=object_definition.object_type,
                    field_path=field_definition.field_path,
                    field_type=field_definition.field_type,
                    required=field_definition.required,
                    export_blocking=(
                        _metadata_export_blocking(field_definition.metadata)
                        or bool(blocking_binding_ids)
                    ),
                    definition_state=field_definition.definition_state,
                    object_role=object_role,
                    model_ref=field_definition.model_ref or object_definition.model_ref,
                    provider_refs=_merged_provider_refs(
                        object_definition.metadata,
                        field_definition.metadata,
                    ),
                    validator_binding_ids=tuple(
                        binding.binding_id for binding in matching_bindings
                    ),
                    blocking_validator_binding_ids=blocking_binding_ids,
                    allow_opt_out=bool(opt_out_bindings),
                    opt_out_reason_required=any(
                        binding.opt_out_reason_required for binding in opt_out_bindings
                    ),
                )
            )
    return tuple(sorted(policies, key=lambda item: (item.object_type, item.field_path)))


def _binding_targets_policy_field(
    *,
    binding: ValidatorBinding,
    object_definition: DomainPackObjectDefinition,
    field_definition: DomainPackFieldDefinition,
) -> bool:
    if binding.object_types and object_definition.object_type not in binding.object_types:
        return False
    if binding.field_paths and field_definition.field_path not in binding.field_paths:
        return False
    if binding.field_types and field_definition.field_type not in binding.field_types:
        return False
    if binding.required_only and not field_definition.required:
        return False
    return _binding_has_field_constraints(binding)


def _metadata_attachment_option(
    *,
    domain_pack: LoadedDomainPack,
    entry: ValidatorMetadataEntry,
) -> ValidationAttachmentOption:
    return ValidationAttachmentOption(
        attachment_id=_validation_attachment_id(
            domain_pack.pack_id,
            "metadata",
            entry.validator_id,
            "pack",
        ),
        domain_pack_id=domain_pack.pack_id,
        domain_pack_version=domain_pack.version,
        validator_id=entry.validator_id,
        state=entry.state,
        scope="pack",
        tool_name=entry.tool_name,
        label=_validation_attachment_label(entry.validator_id, None),
        description=entry.description,
        definition_state=entry.definition_state,
        blocked_by=entry.blocked_by,
        reason=entry.reason,
        default_enabled=False,
    )


def _binding_attachment_option(
    *,
    domain_pack: LoadedDomainPack,
    binding: ValidatorBinding,
    scope: str,
    object_type: str | None = None,
    object_role: str | None = None,
    field_path: str | None = None,
    field_type: DomainPackFieldType | None = None,
    export_blocking: bool = False,
) -> ValidationAttachmentOption:
    active = binding.state is ValidationBindingState.ACTIVE
    required = active and binding.required
    blocks_export = active and bool(export_blocking)
    allow_opt_out = active and binding.allow_opt_out
    opt_out_reason_required = active and allow_opt_out and binding.opt_out_reason_required

    validator_id = (
        binding.validator
        or binding.validation_kind
        or binding.tool_name
        or binding.binding_id
    )
    return ValidationAttachmentOption(
        attachment_id=_validation_attachment_id(
            domain_pack.pack_id,
            "binding",
            binding.binding_id,
            scope,
            object_type=object_type,
            field_path=field_path,
        ),
        domain_pack_id=domain_pack.pack_id,
        domain_pack_version=domain_pack.version,
        validator_id=validator_id,
        validator_binding_id=binding.binding_id,
        validation_kind=binding.validation_kind,
        tool_name=binding.tool_name,
        tool_method=binding.tool_method,
        state=binding.state,
        scope=scope,
        object_type=object_type,
        object_role=object_role,
        field_path=field_path,
        field_type=field_type,
        label=_validation_attachment_label(validator_id, field_path or object_type),
        description=binding.reason or "",
        definition_state=binding.definition_state,
        blocked_by=binding.blocked_by,
        reason=binding.reason,
        required=required,
        export_blocking=blocks_export,
        default_enabled=active,
        allow_opt_out=allow_opt_out,
        opt_out_reason_required=opt_out_reason_required,
    )


def _validation_attachment_id(
    domain_pack_id: str,
    source: str,
    source_id: str,
    scope: str,
    *,
    object_type: str | None = None,
    field_path: str | None = None,
) -> str:
    target_parts = [
        domain_pack_id,
        source,
        source_id,
        scope,
        object_type or "*",
        field_path or "*",
    ]
    return ":".join(target_parts)


def _validation_attachment_label(
    validator_id: str,
    target: str | None,
) -> str:
    label = validator_id.replace("_", " ").replace(".", " ")
    if target:
        return f"{label} ({target})"
    return label


def _binding_target_object_definitions(
    binding: ValidatorBinding,
    object_definitions: Mapping[str, DomainPackObjectDefinition],
) -> tuple[DomainPackObjectDefinition, ...]:
    matches: list[DomainPackObjectDefinition] = []
    for object_definition in object_definitions.values():
        if binding.object_types and object_definition.object_type not in binding.object_types:
            continue
        if binding.object_roles:
            object_role = _metadata_object_role(object_definition.metadata)
            if object_role not in binding.object_roles:
                continue
        matches.append(object_definition)
    return tuple(matches)


def _matching_objects(
    *,
    binding: ValidatorBinding,
    envelope: DomainEnvelope,
    object_definitions: Mapping[str, DomainPackObjectDefinition],
) -> tuple[tuple[CuratableObjectEnvelope, DomainPackObjectDefinition | None], ...]:
    matches: list[tuple[CuratableObjectEnvelope, DomainPackObjectDefinition | None]] = []
    for object_envelope in envelope.objects:
        object_definition = object_definitions.get(object_envelope.object_type)
        if binding.object_types and object_envelope.object_type not in binding.object_types:
            continue
        if binding.object_roles:
            candidate_roles = _object_role_candidates(object_envelope, object_definition)
            if not set(binding.object_roles).intersection(candidate_roles):
                continue
        matches.append((object_envelope, object_definition))
    return tuple(matches)


def _matching_fields(
    binding: ValidatorBinding,
    object_definition: DomainPackObjectDefinition | None,
) -> tuple[DomainPackFieldDefinition, ...]:
    if object_definition is None:
        return ()
    if not _binding_has_field_constraints(binding):
        return ()

    matches: list[DomainPackFieldDefinition] = []
    for field_definition in object_definition.fields:
        if binding.field_paths and field_definition.field_path not in binding.field_paths:
            continue
        if binding.field_types and field_definition.field_type not in binding.field_types:
            continue
        if binding.required_only and not field_definition.required:
            continue
        matches.append(field_definition)
    return tuple(matches)


def _binding_has_target_constraints(binding: ValidatorBinding) -> bool:
    return bool(
        binding.applies_to_domain_pack_id
        or binding.object_types
        or binding.object_roles
        or _binding_has_field_constraints(binding)
    )


def _binding_has_field_constraints(binding: ValidatorBinding) -> bool:
    return bool(binding.field_paths or binding.field_types or binding.required_only)


def _object_role_candidates(
    object_envelope: CuratableObjectEnvelope,
    object_definition: DomainPackObjectDefinition | None,
) -> set[str]:
    if object_envelope.object_role:
        return {object_envelope.object_role}

    roles: set[str] = set()
    if object_definition is not None:
        metadata_role = _metadata_object_role(object_definition.metadata)
        if metadata_role:
            roles.add(metadata_role)
    return roles


def _metadata_object_role(metadata: Mapping[str, Any]) -> str | None:
    raw_role = metadata.get("object_role")
    return raw_role if isinstance(raw_role, str) and raw_role.strip() else None


def _metadata_provider_refs(metadata: Mapping[str, Any]) -> dict[str, Any]:
    raw_provider_refs = metadata.get("provider_refs")
    return dict(raw_provider_refs) if isinstance(raw_provider_refs, Mapping) else {}


def _merged_provider_refs(*metadata_items: Mapping[str, Any]) -> dict[str, Any]:
    provider_refs: dict[str, Any] = {}
    for metadata in metadata_items:
        provider_refs.update(_metadata_provider_refs(metadata))
    return provider_refs


def _metadata_export_blocking(metadata: Mapping[str, Any]) -> bool:
    for key in ("export_blocking", "required_for_export"):
        raw_value = metadata.get(key)
        if raw_value is not None:
            return bool(raw_value)
    raw_export_behavior = metadata.get("export_behavior")
    if isinstance(raw_export_behavior, Mapping):
        return raw_export_behavior.get("status") == "blocked"
    return False


__all__ = [
    "DomainPackValidationRegistry",
    "FieldValidationPolicy",
    "ValidationBindingState",
    "ValidationRegistryError",
    "ValidationAttachmentOption",
    "ValidatorBinding",
    "ValidatorBindingMatch",
    "ValidatorMetadataEntry",
]
