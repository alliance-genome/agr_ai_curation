"""Metadata-driven domain-pack validation registry.

Domain packs own their validator declarations.  This module keeps the core
runtime provider-agnostic by normalizing those declarations into deterministic
bindings, validator metadata entries, match results, and field-level policies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Protocol

from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DefinitionState,
    DomainEnvelope,
    validate_field_path_syntax,
)
from src.schemas.domain_pack_metadata import (
    DomainPackFieldDefinition,
    DomainPackFieldType,
    DomainPackInputSelector,
    DomainPackObjectDefinition,
)

from .registry import LoadedDomainPack


class ValidationBindingState(str, Enum):
    """Lifecycle state for a domain-pack validator binding."""

    ACTIVE = "active"
    UNDER_DEVELOPMENT = "under_development"
    PLANNED = "planned"
    BLOCKED = "blocked"


class ValidationRegistryError(ValueError):
    """Raised when domain-pack validation metadata is malformed."""


class PackageRegistryForValidatorReferences(Protocol):
    """Package registry behavior needed to validate validator agent refs."""

    def get_package(self, package_id: str) -> object | None:
        """Return a loaded package by package ID, if present."""

    def package_declares_dependency(
        self,
        source_package_id: str,
        target_package_id: str,
    ) -> bool:
        """Return whether source package declares target package as a dependency."""


ValidatorAgentResolver = Callable[[str, str], object | None]
ValidatorSchemaResolver = Callable[[str], object | None]


@dataclass(frozen=True)
class ValidatorAgentRef:
    """Package-scoped validator agent reference declared by a domain pack."""

    package_id: str
    agent_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "package_id": self.package_id,
            "agent_id": self.agent_id,
        }


@dataclass(frozen=True)
class ValidatorMetadataEntry:
    """One declarative entry from ``metadata.validators``."""

    validator_id: str
    state: ValidationBindingState
    display_name: str | None = None
    description: str = ""
    definition_state: DefinitionState = DefinitionState.STABLE
    blocked_by: str | None = None
    reason: str | None = None
    validator_agent: ValidatorAgentRef | None = None
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
        if self.display_name:
            details["display_name"] = self.display_name
        if self.blocked_by:
            details["blocked_by"] = self.blocked_by
        if self.reason:
            details["reason"] = self.reason
        if self.validator_agent:
            details["validator_agent"] = self.validator_agent.to_dict()
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
    display_name: str | None = None
    validator_agent: ValidatorAgentRef | None = None
    definition_state: DefinitionState = DefinitionState.STABLE
    reason: str | None = None
    blocking: bool = False
    required: bool = False
    allow_opt_out: bool = False
    max_tool_calls: int | None = None
    curator_override_allowed: bool = False
    applies_to_domain_pack_id: str | None = None
    object_types: tuple[str, ...] = ()
    object_roles: tuple[str, ...] = ()
    field_paths: tuple[str, ...] = ()
    field_types: tuple[DomainPackFieldType, ...] = ()
    input_fields: dict[str, DomainPackInputSelector] = field(default_factory=dict)
    expected_result_fields: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

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
        }
        optional_values = {
            "display_name": self.display_name,
            "validator_agent": (
                self.validator_agent.to_dict()
                if self.validator_agent is not None
                else None
            ),
            "reason": self.reason,
            "state_explanation": (
                self.reason
                if self.state is ValidationBindingState.UNDER_DEVELOPMENT
                else None
            ),
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
            details["field_types"] = [
                field_type.value for field_type in self.field_types
            ]
        if self.input_fields:
            details["input_fields"] = {
                input_name: selector.model_dump(mode="json", exclude_none=True)
                for input_name, selector in self.input_fields.items()
            }
        if self.expected_result_fields:
            details["expected_result_fields"] = dict(self.expected_result_fields)
        if self.max_tool_calls is not None:
            details["max_tool_calls"] = self.max_tool_calls
        if self.curator_override_allowed:
            details["curator_override"] = {"allowed": True}
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
    tool_name: str | None = None
    validator_package_id: str | None = None
    validator_agent_id: str | None = None
    object_type: str | None = None
    object_role: str | None = None
    field_path: str | None = None
    field_type: DomainPackFieldType | None = None
    label: str = ""
    target_label: str | None = None
    description: str = ""
    definition_state: DefinitionState = DefinitionState.STABLE
    blocked_by: str | None = None
    reason: str | None = None
    state_explanation: str | None = None
    affected_fields: tuple[str, ...] = ()
    required: bool = False
    export_blocking: bool = False
    default_enabled: bool = False
    allow_opt_out: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-serializable option metadata for APIs and flow payloads."""

        payload: dict[str, Any] = {
            "attachment_id": self.attachment_id,
            "domain_pack_id": self.domain_pack_id,
            "domain_pack_version": self.domain_pack_version,
            "validator_id": self.validator_id,
            "validator_binding_id": self.validator_binding_id,
            "tool_name": self.tool_name,
            "validator_package_id": self.validator_package_id,
            "validator_agent_id": self.validator_agent_id,
            "state": self.state.value,
            "scope": self.scope,
            "object_type": self.object_type,
            "object_role": self.object_role,
            "field_path": self.field_path,
            "field_type": (
                self.field_type.value if self.field_type is not None else None
            ),
            "label": self.label,
            "target_label": self.target_label,
            "description": self.description,
            "definition_state": self.definition_state.value,
            "blocked_by": self.blocked_by,
            "reason": self.reason,
            "state_explanation": self.state_explanation,
            "affected_fields": list(self.affected_fields),
            "required": self.required,
            "export_blocking": self.export_blocking,
            "default_enabled": self.default_enabled,
            "allow_opt_out": self.allow_opt_out,
        }
        return {
            key: value
            for key, value in payload.items()
            if value is not None and value != []
        }


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
    object_display_name: str | None = None
    field_display_name: str | None = None
    object_role: str | None = None
    model_ref: str | None = None
    provider_refs: dict[str, Any] = field(default_factory=dict)
    validator_binding_ids: tuple[str, ...] = ()
    blocking_validator_binding_ids: tuple[str, ...] = ()
    allow_opt_out: bool = False

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
        }
        if self.object_role:
            details["object_role"] = self.object_role
        if self.object_display_name:
            details["object_display_name"] = self.object_display_name
        if self.field_display_name:
            details["field_display_name"] = self.field_display_name
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
                details["object_schema_ref"] = (
                    self.object_envelope.schema_ref.model_dump(mode="json")
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
    def from_domain_pack(
        cls, domain_pack: LoadedDomainPack
    ) -> "DomainPackValidationRegistry":
        """Build a validation registry from one loaded domain pack."""

        metadata = domain_pack.metadata
        validator_metadata = _collect_validator_metadata(metadata.metadata)
        bindings = _collect_validator_bindings(metadata.metadata)

        for object_definition in metadata.object_definitions:
            bindings.extend(
                _collect_validator_bindings(
                    object_definition.metadata,
                    source_scope="object",
                    source_object_type=object_definition.object_type,
                )
            )
            for field_definition in object_definition.fields:
                bindings.extend(
                    _collect_validator_bindings(
                        field_definition.metadata,
                        source_scope="field",
                        source_object_type=object_definition.object_type,
                        source_field_path=field_definition.field_path,
                        source_field_type=field_definition.field_type,
                    )
                )

        normalized_bindings = tuple(
            sorted(
                bindings, key=lambda binding: (binding.state.value, binding.binding_id)
            )
        )
        _validate_active_binding_selectors(domain_pack, normalized_bindings)
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
                matches.append(
                    ValidatorBindingMatch(binding=binding, envelope=envelope)
                )
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
                            object_display_name=policy.object_display_name,
                            object_role=policy.object_role,
                            field_path=policy.field_path,
                            field_display_name=policy.field_display_name,
                            field_type=policy.field_type,
                            affected_fields=(policy.field_path,),
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
                            object_display_name=object_definition.display_name,
                            object_role=_metadata_object_role(
                                object_definition.metadata
                            ),
                            affected_fields=tuple(
                                field.field_path for field in object_definition.fields
                            ),
                            export_blocking=binding.blocking,
                        )
                    )
                continue

            options.append(
                _binding_attachment_option(
                    domain_pack=self.domain_pack,
                binding=binding,
                scope="pack",
                affected_fields=tuple(
                    field.field_path
                    for object_definition in self.domain_pack.metadata.object_definitions
                    for field in object_definition.fields
                ),
                export_blocking=binding.blocking,
            )
        )

        return _dedupe_validation_attachment_options(
            tuple(sorted(options, key=lambda option: option.attachment_id))
        )


def validate_active_validator_agent_references(
    registries: Iterable[DomainPackValidationRegistry],
    package_registry: PackageRegistryForValidatorReferences,
    agent_resolver: ValidatorAgentResolver | None = None,
    output_schema_resolver: ValidatorSchemaResolver | None = None,
) -> None:
    """Validate active package-scoped validator agent refs across loaded packages."""

    if agent_resolver is None:
        from src.lib.config.agent_loader import get_agent_definition_for_package

        agent_resolver = get_agent_definition_for_package
    if output_schema_resolver is None:
        from src.lib.config.schema_discovery import resolve_output_schema

        output_schema_resolver = resolve_output_schema

    errors: list[str] = []
    for registry in registries:
        owner_package_id = registry.domain_pack.package_id
        for entry in registry.validator_metadata:
            if entry.state is not ValidationBindingState.ACTIVE:
                continue
            if entry.validator_agent is None:
                continue

            _validate_active_validator_agent_reference(
                errors=errors,
                registry=registry,
                owner_package_id=owner_package_id,
                ref=entry.validator_agent,
                reference_kind="validator",
                reference_id=entry.validator_id,
                package_registry=package_registry,
                agent_resolver=agent_resolver,
                output_schema_resolver=output_schema_resolver,
            )

        for binding in registry.bindings:
            if binding.state is not ValidationBindingState.ACTIVE:
                continue
            if binding.validator_agent is None:
                continue

            _validate_active_validator_agent_reference(
                errors=errors,
                registry=registry,
                owner_package_id=owner_package_id,
                ref=binding.validator_agent,
                reference_kind="binding",
                reference_id=binding.binding_id,
                package_registry=package_registry,
                agent_resolver=agent_resolver,
                output_schema_resolver=output_schema_resolver,
            )

    if errors:
        raise ValidationRegistryError("; ".join(errors))


def _validate_active_validator_agent_reference(
    *,
    errors: list[str],
    registry: DomainPackValidationRegistry,
    owner_package_id: str | None,
    ref: ValidatorAgentRef,
    reference_kind: str,
    reference_id: str,
    package_registry: PackageRegistryForValidatorReferences,
    agent_resolver: ValidatorAgentResolver,
    output_schema_resolver: ValidatorSchemaResolver,
) -> None:
    if package_registry.get_package(ref.package_id) is None:
        errors.append(
            f"Domain pack '{registry.domain_pack.pack_id}' {reference_kind} "
            f"'{reference_id}' references missing validator package "
            f"'{ref.package_id}'"
        )
        return

    agent = agent_resolver(ref.package_id, ref.agent_id)
    if agent is None:
        errors.append(
            f"Domain pack '{registry.domain_pack.pack_id}' {reference_kind} "
            f"'{reference_id}' references missing validator agent "
            f"'{ref.package_id}:{ref.agent_id}'"
        )
    else:
        _validate_active_validator_agent_output_schema(
            errors=errors,
            registry=registry,
            reference_kind=reference_kind,
            reference_id=reference_id,
            ref=ref,
            agent=agent,
            output_schema_resolver=output_schema_resolver,
        )

    if (
        owner_package_id is not None
        and owner_package_id != ref.package_id
        and not package_registry.package_declares_dependency(
            owner_package_id,
            ref.package_id,
        )
    ):
        errors.append(
            f"Package '{owner_package_id}' must declare dependency "
            f"'{ref.package_id}' for domain pack "
            f"'{registry.domain_pack.pack_id}' {reference_kind} "
            f"'{reference_id}'"
        )


def _validate_active_validator_agent_output_schema(
    *,
    errors: list[str],
    registry: DomainPackValidationRegistry,
    reference_kind: str,
    reference_id: str,
    ref: ValidatorAgentRef,
    agent: object,
    output_schema_resolver: ValidatorSchemaResolver,
) -> None:
    from src.schemas.domain_validator import is_domain_validator_result_schema

    output_schema_key = str(getattr(agent, "output_schema", "") or "").strip()
    if not output_schema_key:
        errors.append(
            f"Domain pack '{registry.domain_pack.pack_id}' {reference_kind} "
            f"'{reference_id}' references validator agent "
            f"'{ref.package_id}:{ref.agent_id}' without an output_schema"
        )
        return

    schema = output_schema_resolver(output_schema_key)
    if schema is None:
        errors.append(
            f"Domain pack '{registry.domain_pack.pack_id}' {reference_kind} "
            f"'{reference_id}' references validator agent "
            f"'{ref.package_id}:{ref.agent_id}' with unknown output_schema "
            f"'{output_schema_key}'"
        )
        return

    if not is_domain_validator_result_schema(schema):
        errors.append(
            f"Domain pack '{registry.domain_pack.pack_id}' {reference_kind} "
            f"'{reference_id}' references validator agent "
            f"'{ref.package_id}:{ref.agent_id}' whose output_schema "
            f"'{output_schema_key}' must inherit from or embed DomainValidatorResultBase"
        )


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
                display_name=_optional_string(raw_item.get("display_name")),
                description=_optional_string(raw_item.get("description")),
                definition_state=_definition_state(raw_item),
                blocked_by=_optional_string(raw_item.get("blocked_by")),
                reason=_optional_string(raw_item.get("reason")),
                validator_agent=_validator_agent_ref(raw_item),
                tool_name=_optional_string(raw_item.get("tool_name")),
                raw=dict(raw_item),
            )
        )
    return entries


def _collect_validator_bindings(
    owner_metadata: Mapping[str, Any],
    *,
    source_scope: str = "pack",
    source_object_type: str | None = None,
    source_field_path: str | None = None,
    source_field_type: DomainPackFieldType | None = None,
) -> list[ValidatorBinding]:
    raw_bindings = owner_metadata.get("validator_bindings")
    bindings: list[ValidatorBinding] = []
    for state, raw_item in _iter_validator_binding_items(raw_bindings):
        applies_to = _optional_mapping(raw_item.get("applies_to"), "applies_to")
        object_types = _coerce_string_tuple(applies_to.get("object_types"))
        field_paths = _coerce_string_tuple(applies_to.get("field_paths"))
        field_types = _coerce_field_type_tuple(applies_to.get("field_types"))

        if source_object_type is not None and not object_types:
            object_types = (source_object_type,)
        if source_field_path is not None and not field_paths:
            field_paths = (source_field_path,)
        if source_field_type is not None and not field_types:
            field_types = (source_field_type,)

        active = state is ValidationBindingState.ACTIVE
        blocking = active and _optional_bool(raw_item.get("blocking"))
        required = active and _optional_bool(raw_item.get("required"))
        allow_opt_out = active and _optional_bool(raw_item.get("allow_opt_out"))
        curator_override = _optional_mapping(
            raw_item.get("curator_override"),
            "curator_override",
        )

        if state is ValidationBindingState.UNDER_DEVELOPMENT:
            display_name = _required_string(
                raw_item,
                "display_name",
                "validator_bindings.under_development",
            )
            reason = _required_string(
                raw_item,
                "state_explanation",
                "validator_bindings.under_development",
            )
        else:
            display_name = _optional_string(raw_item.get("display_name"))
            reason = _optional_string(raw_item.get("description"))

        bindings.append(
            ValidatorBinding(
                binding_id=_required_string(
                    raw_item, "binding_id", "validator_bindings"
                ),
                state=state,
                source_scope=source_scope,
                source_object_type=source_object_type,
                source_field_path=source_field_path,
                display_name=display_name,
                validator_agent=_validator_agent_ref(raw_item),
                definition_state=_definition_state(raw_item),
                reason=reason,
                blocking=blocking,
                required=required,
                allow_opt_out=allow_opt_out,
                applies_to_domain_pack_id=_optional_string(
                    applies_to.get("domain_pack_id")
                ),
                object_types=object_types,
                object_roles=_coerce_string_tuple(applies_to.get("object_roles")),
                field_paths=tuple(
                    validate_field_path_syntax(path) for path in field_paths
                ),
                field_types=field_types,
                input_fields=_coerce_input_selectors(raw_item.get("input_fields")),
                expected_result_fields=dict(
                    _optional_mapping(
                        raw_item.get("expected_result_fields"),
                        "expected_result_fields",
                    )
                ),
                max_tool_calls=_optional_int(raw_item.get("max_tool_calls")),
                curator_override_allowed=active
                and _optional_bool(curator_override.get("allowed")),
                raw=dict(raw_item),
            )
        )
    return bindings


def _iter_validator_binding_items(
    raw_items: Any,
) -> Iterable[tuple[ValidationBindingState, Mapping[str, Any]]]:
    if raw_items is None:
        return ()
    if not isinstance(raw_items, Mapping):
        raise ValidationRegistryError(
            "validator_bindings must be a mapping with active and under_development buckets"
        )

    normalized: list[tuple[ValidationBindingState, Mapping[str, Any]]] = []
    for state in (
        ValidationBindingState.ACTIVE,
        ValidationBindingState.UNDER_DEVELOPMENT,
    ):
        state_items = raw_items.get(state.value)
        if state_items is None:
            continue
        if not isinstance(state_items, list):
            raise ValidationRegistryError(
                f"validator_bindings.{state.value} must be a list"
            )
        normalized.extend(
            (state, _required_mapping_item(raw_item, "validator_bindings"))
            for raw_item in state_items
        )
    return tuple(normalized)


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


def _validator_agent_ref(raw_item: Mapping[str, Any]) -> ValidatorAgentRef | None:
    raw_ref = raw_item.get("validator_agent")
    if raw_ref is None:
        return None
    if not isinstance(raw_ref, Mapping):
        raise ValidationRegistryError(
            "validator_agent must be a mapping with package_id and agent_id"
        )
    package_id = raw_ref.get("package_id")
    agent_id = raw_ref.get("agent_id")
    if not isinstance(package_id, str) or not package_id.strip():
        raise ValidationRegistryError(
            "validator_agent.package_id must be a non-empty string"
        )
    if package_id != package_id.strip():
        raise ValidationRegistryError(
            "validator_agent.package_id must not have leading or trailing whitespace"
        )
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise ValidationRegistryError(
            "validator_agent.agent_id must be a non-empty string"
        )
    if agent_id != agent_id.strip():
        raise ValidationRegistryError(
            "validator_agent.agent_id must not have leading or trailing whitespace"
        )
    return ValidatorAgentRef(package_id=package_id, agent_id=agent_id)


def _coerce_input_selectors(value: Any) -> dict[str, DomainPackInputSelector]:
    raw_selectors = _optional_mapping(value, "input_fields")
    selectors: dict[str, DomainPackInputSelector] = {}
    for raw_name, raw_selector in raw_selectors.items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ValidationRegistryError("input_fields keys must be non-empty strings")
        if raw_name != raw_name.strip():
            raise ValidationRegistryError(
                "input_fields keys must not have leading or trailing whitespace"
            )
        if not isinstance(raw_selector, Mapping):
            raise ValidationRegistryError(
                f"input_fields.{raw_name} must be an explicit selector mapping"
            )
        try:
            selectors[raw_name] = DomainPackInputSelector.model_validate(raw_selector)
        except ValueError as exc:
            raise ValidationRegistryError(f"input_fields.{raw_name}: {exc}") from exc
    return selectors


def _optional_bool(value: Any) -> bool:
    return bool(value) if value is not None else False


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationRegistryError("Expected an integer value")
    return value


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
                    object_display_name=object_definition.display_name,
                    field_display_name=field_definition.display_name,
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
                )
            )
    return tuple(sorted(policies, key=lambda item: (item.object_type, item.field_path)))


def _binding_targets_policy_field(
    *,
    binding: ValidatorBinding,
    object_definition: DomainPackObjectDefinition,
    field_definition: DomainPackFieldDefinition,
) -> bool:
    if (
        binding.object_types
        and object_definition.object_type not in binding.object_types
    ):
        return False
    if binding.field_paths and field_definition.field_path not in binding.field_paths:
        return False
    if binding.field_types and field_definition.field_type not in binding.field_types:
        return False
    return _binding_has_field_constraints(binding)


def _validate_active_binding_selectors(
    domain_pack: LoadedDomainPack,
    bindings: tuple[ValidatorBinding, ...],
) -> None:
    object_definitions = {
        object_definition.object_type: object_definition
        for object_definition in domain_pack.metadata.object_definitions
    }
    errors: list[str] = []
    for binding in bindings:
        if binding.state is not ValidationBindingState.ACTIVE:
            continue
        target_definitions = _binding_target_object_definitions(
            binding,
            object_definitions,
        )
        for input_name, selector in binding.input_fields.items():
            _validate_active_selector_against_targets(
                errors=errors,
                domain_pack_id=domain_pack.pack_id,
                binding=binding,
                input_name=input_name,
                selector=selector,
                target_definitions=target_definitions,
                object_definitions=object_definitions,
            )
    if errors:
        raise ValidationRegistryError("; ".join(errors))


def _validate_active_selector_against_targets(
    *,
    errors: list[str],
    domain_pack_id: str,
    binding: ValidatorBinding,
    input_name: str,
    selector: DomainPackInputSelector,
    target_definitions: tuple[DomainPackObjectDefinition, ...],
    object_definitions: Mapping[str, DomainPackObjectDefinition],
) -> None:
    location = (
        f"Domain pack '{domain_pack_id}' active validator binding "
        f"'{binding.binding_id}' input_fields.{input_name}"
    )

    if selector.source == "payload":
        if not target_definitions:
            return
        for object_definition in target_definitions:
            if not _object_definition_declares_payload_path(
                object_definition,
                selector.path or "",
            ):
                errors.append(
                    f"{location} payload path '{selector.path}' is not declared for "
                    f"object_type '{object_definition.object_type}'"
                )
        return

    if selector.source == "object_ref":
        referenced_object_types: set[str] = set()
        if selector.field_path is not None:
            for object_definition in target_definitions:
                field_definition = _field_definition_for_path(
                    object_definition,
                    selector.field_path,
                )
                if field_definition is None:
                    errors.append(
                        f"{location} object_ref field_path '{selector.field_path}' "
                        f"is not declared for object_type '{object_definition.object_type}'"
                    )
                    continue
                if field_definition.field_type not in {
                    DomainPackFieldType.OBJECT_REF,
                    DomainPackFieldType.FIELD_REF,
                }:
                    errors.append(
                        f"{location} object_ref field_path '{selector.field_path}' "
                        f"must target an object_ref or field_ref field"
                    )
                    continue
                if field_definition.object_type_ref is None:
                    continue
                referenced_object_types.add(field_definition.object_type_ref)
                if (
                    selector.object_type is not None
                    and selector.object_type != field_definition.object_type_ref
                ):
                    errors.append(
                        f"{location} object_ref object_type '{selector.object_type}' "
                        f"does not match field_path '{selector.field_path}' "
                        f"object_type_ref '{field_definition.object_type_ref}'"
                    )
                _validate_active_object_ref_payload_path(
                    errors=errors,
                    location=location,
                    object_definitions=object_definitions,
                    object_type=field_definition.object_type_ref,
                    path=selector.path,
                )
        if selector.object_type is not None:
            if selector.object_type not in referenced_object_types:
                _validate_active_object_ref_payload_path(
                    errors=errors,
                    location=location,
                    object_definitions=object_definitions,
                    object_type=selector.object_type,
                    path=selector.path,
                )


def _validate_active_object_ref_payload_path(
    *,
    errors: list[str],
    location: str,
    object_definitions: Mapping[str, DomainPackObjectDefinition],
    object_type: str,
    path: str | None,
) -> None:
    ref_definition = object_definitions.get(object_type)
    if ref_definition is None:
        errors.append(
            f"{location} object_ref object_type '{object_type}' "
            "is not declared by the domain pack"
        )
        return
    if path is not None and not _object_definition_declares_payload_path(
        ref_definition,
        path,
    ):
        errors.append(
            f"{location} object_ref payload path '{path}' is not "
            f"declared for referenced object_type '{object_type}'"
        )


def _field_definition_for_path(
    object_definition: DomainPackObjectDefinition,
    field_path: str,
) -> DomainPackFieldDefinition | None:
    for field_definition in object_definition.fields:
        if field_definition.field_path == field_path:
            return field_definition
    return None


def _object_definition_declares_payload_path(
    object_definition: DomainPackObjectDefinition,
    field_path: str,
) -> bool:
    if _field_definition_for_path(object_definition, field_path) is not None:
        return True
    return _provider_refs_ground_payload_path(object_definition, field_path)


def _provider_refs_ground_payload_path(
    object_definition: DomainPackObjectDefinition,
    field_path: str,
) -> bool:
    for field_definition in object_definition.fields:
        provider_refs = _metadata_provider_refs(field_definition.metadata)
        for provider_ref in provider_refs.values():
            if not isinstance(provider_ref, Mapping):
                continue
            if not provider_ref.get("schema_ref"):
                continue
            for key in ("slot", "attribute"):
                provider_path = provider_ref.get(key)
                if provider_path == field_path:
                    return True
    return False


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
        validator_package_id=(
            entry.validator_agent.package_id
            if entry.validator_agent is not None
            else None
        ),
        validator_agent_id=(
            entry.validator_agent.agent_id
            if entry.validator_agent is not None
            else None
        ),
        tool_name=entry.tool_name,
        label=_validation_attachment_label(
            entry.display_name or entry.validator_id, None
        ),
        description=entry.description,
        definition_state=entry.definition_state,
        blocked_by=entry.blocked_by,
        reason=entry.reason,
        default_enabled=False,
    )


def _dedupe_validation_attachment_options(
    options: tuple[ValidationAttachmentOption, ...],
) -> tuple[ValidationAttachmentOption, ...]:
    """Collapse duplicate pack-level options that represent the same curator note."""

    deduped: list[ValidationAttachmentOption] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for option in sorted(
        options,
        key=lambda item: (
            item.label,
            item.state.value,
            0 if item.validator_binding_id else 1,
            item.attachment_id,
        ),
    ):
        key = (
            option.state.value,
            option.label,
            option.scope,
            option.object_type or "",
            option.field_path or "",
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(option)
    return tuple(sorted(deduped, key=lambda option: option.attachment_id))


def _binding_attachment_option(
    *,
    domain_pack: LoadedDomainPack,
    binding: ValidatorBinding,
    scope: str,
    object_type: str | None = None,
    object_display_name: str | None = None,
    object_role: str | None = None,
    field_path: str | None = None,
    field_display_name: str | None = None,
    field_type: DomainPackFieldType | None = None,
    affected_fields: tuple[str, ...] = (),
    export_blocking: bool = False,
) -> ValidationAttachmentOption:
    active = binding.state is ValidationBindingState.ACTIVE
    required = active and binding.required
    blocks_export = active and bool(export_blocking)
    allow_opt_out = active and binding.allow_opt_out

    validator_id = (
        f"{binding.validator_agent.package_id}:{binding.validator_agent.agent_id}"
        if binding.validator_agent is not None
        else binding.binding_id
    )
    target_label = _binding_attachment_target_label(
        object_display_name=object_display_name,
        object_type=object_type,
        field_display_name=field_display_name,
        field_path=field_path,
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
        validator_package_id=(
            binding.validator_agent.package_id
            if binding.validator_agent is not None
            else None
        ),
        validator_agent_id=(
            binding.validator_agent.agent_id
            if binding.validator_agent is not None
            else None
        ),
        state=binding.state,
        scope=scope,
        object_type=object_type,
        object_role=object_role,
        field_path=field_path,
        field_type=field_type,
        label=_binding_attachment_label(
            binding=binding,
            fallback_validator_id=validator_id,
            target_label=target_label,
            field_display_name=field_display_name,
            field_path=field_path,
        ),
        target_label=target_label,
        description=(
            ""
            if binding.state is ValidationBindingState.UNDER_DEVELOPMENT
            else (binding.reason if binding.reason is not None else "")
        ),
        definition_state=binding.definition_state,
        reason=binding.reason,
        state_explanation=(
            binding.reason
            if binding.state is ValidationBindingState.UNDER_DEVELOPMENT
            else None
        ),
        affected_fields=affected_fields,
        required=required,
        export_blocking=blocks_export,
        default_enabled=active,
        allow_opt_out=allow_opt_out,
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


def _binding_attachment_label(
    *,
    binding: ValidatorBinding,
    fallback_validator_id: str,
    target_label: str | None,
    field_display_name: str | None,
    field_path: str | None,
) -> str:
    base_label = _validation_attachment_label(
        binding.display_name or fallback_validator_id,
        None,
    )
    field_label = _clean_display_label(field_display_name) or _humanize_field_path(
        field_path
    )
    if field_label and _label_already_names_target(base_label, field_label):
        return base_label
    if not target_label:
        return base_label
    if _label_already_names_target(base_label, target_label):
        return base_label
    if (
        "envelope validation" in base_label.lower()
        or base_label.lower() == "data check"
    ):
        return f"{target_label} data check"
    return f"{target_label}: {base_label}"


def _binding_attachment_target_label(
    *,
    object_display_name: str | None,
    object_type: str | None,
    field_display_name: str | None,
    field_path: str | None,
) -> str | None:
    object_label = _clean_display_label(object_display_name) or _humanize_identifier(
        object_type
    )
    field_label = _clean_display_label(field_display_name) or _humanize_field_path(
        field_path
    )
    return _friendly_target_label(object_label=object_label, field_label=field_label)


def _friendly_target_label(
    *,
    object_label: str | None,
    field_label: str | None,
) -> str | None:
    if object_label and field_label:
        field_label = _remove_redundant_leading_word(field_label, object_label)
        return f"{object_label} {field_label}"
    return object_label or field_label


def _clean_display_label(value: str | None) -> str | None:
    if value is None:
        return None
    label = " ".join(str(value).split())
    return label or None


def _humanize_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    label = value.replace("_", " ")
    label = label.replace("-", " ")
    words: list[str] = []
    for word in label.split():
        if word.isupper():
            words.append(word)
            continue
        words.append(word[:1].upper() + word[1:])
    return " ".join(words) or None


def _humanize_field_path(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.replace("[0]", " first ")
    normalized = normalized.replace(".", " ")
    normalized = normalized.replace("_", " ")
    normalized = " ".join(normalized.split())
    if not normalized:
        return None
    return normalized[:1].upper() + normalized[1:]


def _remove_redundant_leading_word(field_label: str, object_label: str) -> str:
    field_words = field_label.split()
    object_words = object_label.split()
    object_words_normalized = {word.lower() for word in object_words}
    if len(field_words) > 1 and field_words[0].lower() in object_words_normalized:
        return " ".join(field_words[1:])
    return field_label


def _label_already_names_target(label: str, target_label: str) -> bool:
    normalized_label = " ".join(label.lower().split())
    normalized_target = " ".join(target_label.lower().split())
    if normalized_label.startswith(normalized_target):
        return True
    target_words = set(normalized_target.split())
    if not target_words:
        return False
    label_words = set(normalized_label.split())
    return target_words.issubset(label_words)


def _binding_target_object_definitions(
    binding: ValidatorBinding,
    object_definitions: Mapping[str, DomainPackObjectDefinition],
) -> tuple[DomainPackObjectDefinition, ...]:
    if not binding.object_types and not binding.object_roles:
        return ()

    matches: list[DomainPackObjectDefinition] = []
    for object_definition in object_definitions.values():
        if (
            binding.object_types
            and object_definition.object_type not in binding.object_types
        ):
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
    matches: list[tuple[CuratableObjectEnvelope, DomainPackObjectDefinition | None]] = (
        []
    )
    for object_envelope in envelope.objects:
        object_definition = object_definitions.get(object_envelope.object_type)
        if (
            binding.object_types
            and object_envelope.object_type not in binding.object_types
        ):
            continue
        if binding.object_roles:
            candidate_roles = _object_role_candidates(
                object_envelope, object_definition
            )
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
        if (
            binding.field_paths
            and field_definition.field_path not in binding.field_paths
        ):
            continue
        if (
            binding.field_types
            and field_definition.field_type not in binding.field_types
        ):
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
    return bool(binding.field_paths or binding.field_types)


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
    "ValidatorAgentRef",
    "ValidatorBinding",
    "ValidatorBindingMatch",
    "ValidatorMetadataEntry",
    "validate_active_validator_agent_references",
]
