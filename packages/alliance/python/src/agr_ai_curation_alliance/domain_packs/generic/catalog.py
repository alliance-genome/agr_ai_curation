"""Derived class catalog and generated domain-pack view for generic extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Mapping

from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.capabilities import object_capabilities
from src.lib.domain_packs.resolvable_values import (
    ResolvableSpec,
    declared_resolvable_fields,
)
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
    ValidatorBinding,
)
from src.schemas.domain_pack_metadata import (
    DomainPackActiveValidatorBinding,
    DomainPackEnumDefinition,
    DomainPackModelDefinition,
    DomainPackValidatorGroupScope,
    DomainPackFieldType,
    DomainPackFieldDefinition,
    DomainPackMetadata,
    DomainPackObjectDefinition,
    DomainPackUnderDevelopmentValidatorBinding,
    DomainPackValidatorAgentRef,
    DomainPackValidatorAppliesTo,
    DomainPackValidatorBatchConfig,
    DomainPackValidatorBindings,
    DomainPackValidatorCuratorOverride,
)

from agr_ai_curation_alliance.domain_packs.loader import get_alliance_domain_pack
from agr_ai_curation_alliance.domain_packs.loader import load_alliance_domain_packs

from .constants import GENERIC_DOMAIN_PACK_ID, GENERIC_PROXY_PREFIX
from .values import EVIDENCE_SOURCE_FIELDS, builder_owned_keys


@dataclass(frozen=True)
class GenericValidatorBindingSummary:
    binding_id: str
    display_name: str | None
    validator_agent: dict[str, str] | None
    required: bool
    blocking: bool
    input_fields: tuple[str, ...]
    expected_result_fields: tuple[str, ...]


@dataclass(frozen=True)
class GenericClassCatalogEntry:
    class_key: str
    stageable: bool
    source_domain_pack_id: str
    source_object_type: str
    generic_object_type: str
    display_name: str
    description: str
    definition_state: str
    source_is_generic_native: bool
    payload_fields: tuple[str, ...]
    required_payload_fields: tuple[str, ...]
    field_summaries: tuple[dict[str, Any], ...]
    active_validator_bindings: tuple[GenericValidatorBindingSummary, ...] = ()
    under_development_validator_bindings: tuple[GenericValidatorBindingSummary, ...] = ()
    task_hints: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    capabilities: dict[str, Any] = field(default_factory=dict)
    # Declared resolvable values by payload path ("" for the object root).
    resolvable_fields: Mapping[str, ResolvableSpec] = field(default_factory=dict)
    # Payload paths a validator binding writes back; the extractor never writes them.
    validator_owned_fields: tuple[str, ...] = ()

    @property
    def paper_wording_fields(self) -> tuple[str, ...]:
        """Where the extractor writes each resolvable value's paper wording."""

        return tuple(
            f"{field_path}.{spec.mention_key}" if field_path else spec.mention_key
            for field_path, spec in sorted(self.resolvable_fields.items())
        )

    @property
    def system_written_payload_fields(self) -> tuple[str, ...]:
        """Payload fields validation, the builder or the evidence record fill in; never the extractor."""

        builder_owned = [
            f"{field_path}.{key}" if field_path else key
            for field_path, spec in sorted(self.resolvable_fields.items())
            for key in builder_owned_keys(spec)
        ]
        evidence_owned = [
            field_path for field_path in EVIDENCE_SOURCE_FIELDS if field_path in self.payload_fields
        ]
        return tuple(dict.fromkeys([*self.validator_owned_fields, *builder_owned, *evidence_owned]))

    @property
    def validator_state(self) -> str:
        if self.active_validator_bindings:
            return "active"
        if self.under_development_validator_bindings:
            return "under_development"
        return "none"

    def compact_tool_dict(self) -> dict[str, Any]:
        return {
            "class_key": self.class_key,
            "stageable": self.stageable,
            "source_domain_pack_id": self.source_domain_pack_id,
            "object_type": self.source_object_type,
            "generic_object_type": self.generic_object_type,
            "display_name": self.display_name,
            "description": self.description,
            "definition_state": self.definition_state,
            "capabilities": self.capabilities,
            "validator_state": self.validator_state,
            "validator_bindings": [
                binding.binding_id for binding in self.active_validator_bindings
            ],
            "under_development_validator_bindings": [
                binding.binding_id
                for binding in self.under_development_validator_bindings
            ],
            "validator_agents": [
                binding.validator_agent
                for binding in self.active_validator_bindings
                if binding.validator_agent is not None
            ],
            "payload_fields": list(self.payload_fields),
            "required_payload_fields": list(self.required_payload_fields),
            "paper_wording_fields": list(self.paper_wording_fields),
            "system_written_payload_fields": list(self.system_written_payload_fields),
            "field_summaries": list(self.field_summaries),
            "validator_input_fields": sorted(
                {
                    field_name
                    for binding in self.active_validator_bindings
                    for field_name in binding.input_fields
                }
            ),
            "expected_result_fields": sorted(
                {
                    field_name
                    for binding in self.active_validator_bindings
                    for field_name in binding.expected_result_fields
                }
            ),
            "task_hints": list(self.task_hints),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class GenericClassCatalog:
    entries: tuple[GenericClassCatalogEntry, ...]
    generated_domain_pack: LoadedDomainPack
    entries_by_class_key: Mapping[str, GenericClassCatalogEntry] = field(init=False)
    entries_by_generic_object_type: Mapping[str, GenericClassCatalogEntry] = field(
        init=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "entries_by_class_key",
            {entry.class_key: entry for entry in self.entries},
        )
        object.__setattr__(
            self,
            "entries_by_generic_object_type",
            {entry.generic_object_type: entry for entry in self.entries},
        )

    def require_stageable(self, class_key: str) -> GenericClassCatalogEntry:
        normalized = str(class_key or "").strip()
        entry = self.entries_by_class_key.get(normalized)
        if entry is None:
            raise KeyError(f"Unknown generic extraction class_key: {normalized!r}")
        if not entry.stageable:
            raise ValueError(
                f"Generic extraction class_key {normalized!r} is known but not stageable"
            )
        return entry

    def tool_payload(self, *, include_non_stageable: bool = False) -> dict[str, Any]:
        entries = [
            entry
            for entry in self.entries
            if include_non_stageable or entry.stageable
        ]
        return {
            "domain_pack_id": GENERIC_DOMAIN_PACK_ID,
            "class_count": len(entries),
            "classes": [entry.compact_tool_dict() for entry in entries],
        }


def proxy_object_type(source_domain_pack_id: str, source_object_type: str) -> str:
    return (
        f"{GENERIC_PROXY_PREFIX}__"
        f"{_safe_key(source_domain_pack_id)}__"
        f"{_safe_key(source_object_type)}"
    )


@lru_cache(maxsize=1)
def load_generic_class_catalog() -> GenericClassCatalog:
    packs = load_alliance_domain_packs()
    generic_pack = get_alliance_domain_pack(GENERIC_DOMAIN_PACK_ID)
    entries: list[GenericClassCatalogEntry] = []
    generated_objects: list[DomainPackObjectDefinition] = []
    active_bindings: list[DomainPackActiveValidatorBinding] = []
    under_dev_bindings: list[DomainPackUnderDevelopmentValidatorBinding] = []
    proxied_enums: dict[str, DomainPackEnumDefinition] = {}
    proxied_models: dict[str, DomainPackModelDefinition] = {}

    for object_definition in generic_pack.metadata.object_definitions:
        entry = _entry_from_object_definition(
            source_pack=generic_pack,
            object_definition=object_definition,
            registry=DomainPackValidationRegistry.from_domain_pack(generic_pack),
            source_is_generic_native=True,
        )
        entries.append(entry)
        generated_objects.append(object_definition)

    for source_pack in packs:
        if source_pack.pack_id == GENERIC_DOMAIN_PACK_ID:
            continue
        registry = DomainPackValidationRegistry.from_domain_pack(source_pack)
        for object_definition in source_pack.metadata.object_definitions:
            generic_metadata = _generic_extraction_metadata(
                object_definition.metadata
            )
            if not generic_metadata.get("stageable"):
                continue
            entry = _entry_from_object_definition(
                source_pack=source_pack,
                object_definition=object_definition,
                registry=registry,
                source_is_generic_native=False,
            )
            entries.append(entry)
            generated_objects.append(
                _proxy_object_definition(
                    object_definition,
                    entry=entry,
                    source_pack=source_pack,
                    proxied_enums=proxied_enums,
                    proxied_models=proxied_models,
                )
            )
            for binding in registry.bindings:
                if not _binding_applies_to_object(
                    binding,
                    source_pack_id=source_pack.pack_id,
                    object_definition=object_definition,
                ):
                    continue
                if binding.state is ValidationBindingState.ACTIVE:
                    active_bindings.append(_proxy_active_binding(binding, entry=entry))
                else:
                    under_dev_bindings.append(
                        _proxy_under_development_binding(binding, entry=entry)
                    )

    generated_metadata = generic_pack.metadata.model_copy(
        update={
            "object_definitions": generated_objects,
            "enum_definitions": [
                *generic_pack.metadata.enum_definitions,
                *proxied_enums.values(),
            ],
            "model_definitions": [
                *generic_pack.metadata.model_definitions,
                *proxied_models.values(),
            ],
            "metadata": {
                **generic_pack.metadata.metadata,
                "generic_extraction": {
                    "generated_proxy_view": True,
                    "source_pack_count": len({entry.source_domain_pack_id for entry in entries}),
                },
                "validator_bindings": DomainPackValidatorBindings(
                    active=active_bindings,
                    under_development=under_dev_bindings,
                ).model_dump(mode="json", exclude_none=True),
            },
        },
        deep=True,
    )
    generated_pack = LoadedDomainPack(
        pack_id=generic_pack.pack_id,
        display_name=generic_pack.display_name,
        version=generic_pack.version,
        pack_path=generic_pack.pack_path,
        metadata_path=generic_pack.metadata_path,
        metadata=DomainPackMetadata.model_validate(
            generated_metadata.model_dump(mode="json", exclude_none=True)
        ),
        package_id=generic_pack.package_id,
        package_display_name=generic_pack.package_display_name,
        package_version=generic_pack.package_version,
    )
    return GenericClassCatalog(
        entries=tuple(sorted(entries, key=lambda item: item.class_key)),
        generated_domain_pack=generated_pack,
    )


def get_generated_generic_domain_pack() -> LoadedDomainPack:
    return load_generic_class_catalog().generated_domain_pack


def _entry_from_object_definition(
    *,
    source_pack: LoadedDomainPack,
    object_definition: DomainPackObjectDefinition,
    registry: DomainPackValidationRegistry,
    source_is_generic_native: bool,
) -> GenericClassCatalogEntry:
    class_key = f"{source_pack.pack_id}:{object_definition.object_type}"
    generic_object_type = (
        object_definition.object_type
        if source_is_generic_native
        else proxy_object_type(source_pack.pack_id, object_definition.object_type)
    )
    active = tuple(
        _binding_summary(binding)
        for binding in registry.bindings
        if binding.state is ValidationBindingState.ACTIVE
        and _binding_applies_to_object(
            binding,
            source_pack_id=source_pack.pack_id,
            object_definition=object_definition,
        )
    )
    under_dev = tuple(
        _binding_summary(binding)
        for binding in registry.bindings
        if binding.state is ValidationBindingState.UNDER_DEVELOPMENT
        and _binding_applies_to_object(
            binding,
            source_pack_id=source_pack.pack_id,
            object_definition=object_definition,
        )
    )
    metadata = _generic_extraction_metadata(object_definition.metadata)
    return GenericClassCatalogEntry(
        class_key=class_key,
        stageable=bool(metadata.get("stageable")),
        source_domain_pack_id=source_pack.pack_id,
        source_object_type=object_definition.object_type,
        generic_object_type=generic_object_type,
        display_name=object_definition.display_name,
        description=object_definition.description,
        definition_state=object_definition.definition_state.value,
        source_is_generic_native=source_is_generic_native,
        payload_fields=tuple(field.field_path for field in object_definition.fields),
        required_payload_fields=tuple(
            field.field_path for field in object_definition.fields if field.required
        ),
        field_summaries=tuple(
            {
                "field_path": field.field_path,
                "field_type": field.field_type.value,
                "display_name": field.display_name,
                "description": field.description,
                "required": field.required,
                "enum_ref": field.enum_ref,
                "model_ref": field.model_ref,
                "object_type_ref": field.object_type_ref,
            }
            for field in object_definition.fields
        ),
        active_validator_bindings=active,
        under_development_validator_bindings=under_dev,
        task_hints=tuple(str(item) for item in metadata.get("task_hints") or ()),
        notes=tuple(object_definition.definition_notes),
        capabilities=object_capabilities(
            source_pack.metadata, object_definition,
            active_validators=len(active), development_validators=len(under_dev),
        ),
        resolvable_fields=declared_resolvable_fields(
            source_pack.metadata, object_definition.object_type
        ),
        validator_owned_fields=_validator_owned_fields(
            registry, source_pack_id=source_pack.pack_id, object_definition=object_definition
        ),
    )


def _validator_owned_fields(
    registry: DomainPackValidationRegistry,
    *,
    source_pack_id: str,
    object_definition: DomainPackObjectDefinition,
) -> tuple[str, ...]:
    """Payload fields a binding writes back; one it also reads stays the extractor's input."""

    owned: list[str] = []
    for binding in registry.bindings:
        if not _binding_applies_to_object(
            binding, source_pack_id=source_pack_id, object_definition=object_definition
        ):
            continue
        reads = {
            selector.path
            for selector in binding.input_fields.values()
            if selector.source == "payload"
        }
        owned.extend(
            str(field_path)
            for field_path in binding.expected_result_fields.values()
            if field_path not in reads
        )
    return tuple(dict.fromkeys(owned))


def _binding_summary(binding: ValidatorBinding) -> GenericValidatorBindingSummary:
    return GenericValidatorBindingSummary(
        binding_id=binding.binding_id,
        display_name=binding.display_name,
        validator_agent=(
            binding.validator_agent.to_dict()
            if binding.validator_agent is not None
            else None
        ),
        required=binding.required,
        blocking=binding.blocking,
        input_fields=tuple(sorted(binding.input_fields)),
        expected_result_fields=tuple(sorted(binding.expected_result_fields)),
    )


def _generic_extraction_metadata(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    value = metadata.get("generic_extraction")
    return value if isinstance(value, Mapping) else {}


def _binding_applies_to_object(
    binding: ValidatorBinding,
    *,
    source_pack_id: str,
    object_definition: DomainPackObjectDefinition,
) -> bool:
    if (
        binding.applies_to_domain_pack_id is not None
        and binding.applies_to_domain_pack_id != source_pack_id
    ):
        return False
    if binding.object_types and object_definition.object_type not in binding.object_types:
        return False
    if (
        binding.source_object_type
        and binding.source_object_type != object_definition.object_type
    ):
        return False
    if binding.object_roles:
        object_role = object_definition.metadata.get("object_role")
        if not isinstance(object_role, str) or object_role not in binding.object_roles:
            return False
    if binding.field_paths or binding.field_types:
        field_matched = False
        for field_definition in object_definition.fields:
            if binding.field_paths and field_definition.field_path not in binding.field_paths:
                continue
            if binding.field_types and field_definition.field_type not in binding.field_types:
                continue
            field_matched = True
            break
        if not field_matched:
            return False
    return True


def _proxy_object_definition(
    object_definition: DomainPackObjectDefinition,
    *,
    entry: GenericClassCatalogEntry,
    source_pack: LoadedDomainPack,
    proxied_enums: dict[str, DomainPackEnumDefinition],
    proxied_models: dict[str, DomainPackModelDefinition],
) -> DomainPackObjectDefinition:
    metadata = {
        **object_definition.metadata,
        "generic_extraction_proxy": {
            "class_key": entry.class_key,
            "source_domain_pack_id": entry.source_domain_pack_id,
            "source_object_type": entry.source_object_type,
            "source_display_name": entry.display_name,
        },
    }
    metadata.pop("validator_bindings", None)
    return object_definition.model_copy(
        update={
            "object_type": entry.generic_object_type,
            # A resolvable object root stays resolvable in the generic view (ALL-1283).
            "model_ref": _proxy_resolvable_model_ref(
                object_definition.model_ref, source_pack, proxied_models
            ),
            "fields": [
                _proxy_field_definition(
                    field_definition,
                    source_pack=source_pack,
                    proxied_enums=proxied_enums,
                    proxied_models=proxied_models,
                )
                for field_definition in object_definition.fields
            ],
            "metadata": metadata,
        },
        deep=True,
    )


def _proxy_field_definition(
    field_definition: DomainPackFieldDefinition,
    *,
    source_pack: LoadedDomainPack,
    proxied_enums: dict[str, DomainPackEnumDefinition],
    proxied_models: dict[str, DomainPackModelDefinition],
) -> DomainPackFieldDefinition:
    source_refs: dict[str, str] = {}
    updates: dict[str, Any] = {}
    metadata = dict(field_definition.metadata)
    metadata.pop("validator_bindings", None)
    for ref_field in ("enum_ref", "model_ref", "object_type_ref"):
        value = getattr(field_definition, ref_field)
        if value is None:
            continue
        source_refs[ref_field] = value
        updates[ref_field] = None
    vocabulary_enum = _resolvable_vocabulary_enum(field_definition, source_pack)
    if vocabulary_enum is not None:
        # A resolvable value's resolution_state / lookup_outcome stays a closed
        # vocabulary (ALL-1283), under a proxied enum id.
        proxied_id = _proxy_definition_id(source_pack.pack_id, vocabulary_enum.enum_id)
        proxied_enums.setdefault(
            proxied_id, vocabulary_enum.model_copy(update={"enum_id": proxied_id}, deep=True)
        )
        updates["enum_ref"] = proxied_id
    elif field_definition.field_type is DomainPackFieldType.ENUM:
        updates["field_type"] = DomainPackFieldType.STRING
    model_ref = _proxy_resolvable_model_ref(field_definition.model_ref, source_pack, proxied_models)
    if model_ref is not None:
        updates["model_ref"] = model_ref
    if source_refs:
        metadata["generic_extraction_proxy_source_refs"] = source_refs
    updates["metadata"] = metadata
    return field_definition.model_copy(update=updates, deep=True)


def _resolvable_vocabulary_enum(
    field_definition: DomainPackFieldDefinition,
    source_pack: LoadedDomainPack,
) -> DomainPackEnumDefinition | None:
    """The source enum of a resolvable value's resolution_state or lookup_outcome leaf."""

    from src.lib.domain_packs.resolvable_values import (
        LOOKUP_OUTCOME_KEY,
        LOOKUP_OUTCOMES,
        RESOLUTION_STATE_KEY,
        RESOLUTION_STATES,
    )

    if field_definition.field_type is not DomainPackFieldType.ENUM or field_definition.enum_ref is None:
        return None
    vocabulary = {RESOLUTION_STATE_KEY: RESOLUTION_STATES, LOOKUP_OUTCOME_KEY: LOOKUP_OUTCOMES}.get(
        field_definition.field_path.rpartition(".")[2]
    )
    if vocabulary is None:
        return None
    enum = next(
        (item for item in source_pack.metadata.enum_definitions if item.enum_id == field_definition.enum_ref),
        None,
    )
    if enum is None or [value.value for value in enum.values] != list(vocabulary):
        return None
    return enum


def _proxy_resolvable_model_ref(
    model_ref: str | None,
    source_pack: LoadedDomainPack,
    proxied_models: dict[str, DomainPackModelDefinition],
) -> str | None:
    """A proxied id for a source model that declares a resolvable value, else None.

    Only the model's display declaration travels (under a proxied model id),
    so the generic view reads the same resolvable value (headers, legacy rule,
    vocabulary checks); other models stay source-pack concerns.
    """

    from src.lib.domain_packs.resolvable_values import resolvable_spec_from_display

    model = next(
        (item for item in source_pack.metadata.model_definitions if item.model_id == model_ref), None,
    ) if model_ref else None
    if model is None or resolvable_spec_from_display(model.metadata.get("display")) is None:
        return None
    proxied_id = _proxy_definition_id(source_pack.pack_id, model.model_id)
    proxied_models.setdefault(
        proxied_id,
        DomainPackModelDefinition(
            model_id=proxied_id,
            display_name=model.display_name,
            description=model.description,
            definition_state=model.definition_state,
            metadata={"display": dict(model.metadata["display"])},
        ),
    )
    return proxied_id


def _proxy_definition_id(source_domain_pack_id: str, definition_id: str) -> str:
    return f"{GENERIC_PROXY_PREFIX}__{_safe_key(source_domain_pack_id)}__{_safe_key(definition_id)}"


def _proxy_active_binding(
    binding: ValidatorBinding,
    *,
    entry: GenericClassCatalogEntry,
) -> DomainPackActiveValidatorBinding:
    return DomainPackActiveValidatorBinding(
        binding_id=_proxy_binding_id(binding, entry=entry),
        display_name=binding.display_name,
        description=(
            f"Generated generic proxy for {entry.class_key} binding {binding.binding_id}."
        ),
        validator_agent=_validator_agent_ref(binding),
        applies_to=DomainPackValidatorAppliesTo(
            domain_pack_id=GENERIC_DOMAIN_PACK_ID,
            object_types=[entry.generic_object_type],
            object_roles=list(binding.object_roles),
            field_paths=list(binding.field_paths),
            field_types=[field_type.value for field_type in binding.field_types],
        ),
        input_fields=binding.input_fields,
        expected_result_fields=dict(binding.expected_result_fields),
        max_tool_calls=binding.max_tool_calls,
        required=binding.required,
        blocking=binding.blocking,
        allow_opt_out=binding.allow_opt_out,
        group_scope=(
            DomainPackValidatorGroupScope(
                required_any_active_group=list(binding.required_any_active_group),
                provider_value_field_paths=list(binding.provider_value_field_paths),
                allowed_provider_values=list(binding.allowed_provider_values),
                allow_cross_provider=binding.allow_cross_provider,
            )
            if binding.required_any_active_group
            else None
        ),
        batch=DomainPackValidatorBatchConfig(
            enabled=binding.batch_enabled,
            family=binding.batch_family,
            max_size=binding.batch_max_size,
        ),
        curator_override=DomainPackValidatorCuratorOverride(
            allowed=binding.curator_override_allowed,
        ),
        definition_state=binding.definition_state,
    )


def _proxy_under_development_binding(
    binding: ValidatorBinding,
    *,
    entry: GenericClassCatalogEntry,
) -> DomainPackUnderDevelopmentValidatorBinding:
    return DomainPackUnderDevelopmentValidatorBinding(
        binding_id=_proxy_binding_id(binding, entry=entry),
        display_name=binding.display_name or binding.binding_id,
        description=(
            f"Generated generic proxy for {entry.class_key} binding {binding.binding_id}."
        ),
        state_explanation=binding.reason
        or "Source validator binding is under development in the source domain pack.",
        validator_agent=_validator_agent_ref(binding) if binding.validator_agent else None,
        applies_to=DomainPackValidatorAppliesTo(
            domain_pack_id=GENERIC_DOMAIN_PACK_ID,
            object_types=[entry.generic_object_type],
            object_roles=list(binding.object_roles),
            field_paths=list(binding.field_paths),
            field_types=[field_type.value for field_type in binding.field_types],
        ),
        input_fields=binding.input_fields,
        expected_result_fields=dict(binding.expected_result_fields),
        max_tool_calls=binding.max_tool_calls,
        group_scope=(
            DomainPackValidatorGroupScope(
                required_any_active_group=list(binding.required_any_active_group),
                provider_value_field_paths=list(binding.provider_value_field_paths),
                allowed_provider_values=list(binding.allowed_provider_values),
                allow_cross_provider=binding.allow_cross_provider,
            )
            if binding.required_any_active_group
            else None
        ),
        definition_state=binding.definition_state,
    )


def _validator_agent_ref(binding: ValidatorBinding) -> DomainPackValidatorAgentRef:
    if binding.validator_agent is None:
        raise ValueError(f"Validator binding {binding.binding_id} has no validator_agent")
    return DomainPackValidatorAgentRef(
        package_id=binding.validator_agent.package_id,
        agent_id=binding.validator_agent.agent_id,
    )


def _proxy_binding_id(
    binding: ValidatorBinding,
    *,
    entry: GenericClassCatalogEntry,
) -> str:
    return (
        f"proxy__{_safe_key(entry.source_domain_pack_id)}__"
        f"{_safe_key(entry.source_object_type)}__"
        f"{_safe_key(binding.binding_id)}"
    )


def _safe_key(value: str) -> str:
    return str(value).strip().replace(".", "_").replace("-", "_").replace(":", "_")


__all__ = [
    "GenericClassCatalog",
    "GenericClassCatalogEntry",
    "GenericValidatorBindingSummary",
    "get_generated_generic_domain_pack",
    "load_generic_class_catalog",
    "proxy_object_type",
]
