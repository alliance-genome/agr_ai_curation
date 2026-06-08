"""Provider-agnostic metadata contracts for domain packs and fixture packs."""

from __future__ import annotations

import re
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, ClassVar, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .domain_envelope import (
    DefinitionState,
    DomainEnvelope,
    SchemaRef,
    parse_field_path,
    validate_field_path_syntax,
)

_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_PACK_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_SYMBOLIC_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


def _validate_semver(value: str, field_name: str) -> str:
    if not _SEMVER_PATTERN.match(value):
        raise ValueError(f"{field_name} must use semantic version format like 1.2.3")
    return value


def _validate_pack_id(value: str, field_name: str) -> str:
    if not _PACK_ID_PATTERN.match(value):
        raise ValueError(
            f"{field_name} must start with a lowercase letter or digit and only use "
            "lowercase letters, digits, dots, underscores, or hyphens"
        )
    return value


def _validate_symbolic_name(value: str, field_name: str) -> str:
    if not _SYMBOLIC_NAME_PATTERN.match(value):
        raise ValueError(
            f"{field_name} must start with a letter or digit and only use letters, "
            "digits, dots, underscores, hyphens, or colons"
        )
    return value


def _validate_relative_pack_path(value: str, field_name: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    if "\\" in value:
        raise ValueError(f"{field_name} must use forward slashes")

    normalized = PurePosixPath(value)
    if normalized.is_absolute():
        raise ValueError(f"{field_name} must be relative to the domain pack root")
    if ".." in normalized.parts:
        raise ValueError(f"{field_name} must not traverse parent directories")
    if normalized.parts and normalized.parts[0] == ".":
        raise ValueError(f"{field_name} must not start with './'")

    return str(normalized)


def _require_unique(values: list[str], field_name: str) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    if duplicates:
        duplicate_list = ", ".join(sorted(duplicates))
        raise ValueError(f"{field_name} contains duplicate entries: {duplicate_list}")


class DomainPackMetadataBaseModel(BaseModel):
    """Strict base model for domain-pack metadata contracts."""

    model_config = ConfigDict(extra="forbid")


class DomainPackStatus(str, Enum):
    """Lifecycle state for a domain pack."""

    ACTIVE = "active"
    IN_DEVELOPMENT = "in_development"
    DEPRECATED = "deprecated"


class DomainPackValidatorAgentRef(DomainPackMetadataBaseModel):
    """Package-scoped validator agent reference declared in validator metadata."""

    package_id: str
    agent_id: str

    @field_validator("package_id")
    @classmethod
    def _validate_package_id(cls, value: str) -> str:
        return _validate_pack_id(value, "validator_agent.package_id")

    @field_validator("agent_id")
    @classmethod
    def _validate_agent_id(cls, value: str) -> str:
        return _validate_symbolic_name(value, "validator_agent.agent_id")


class DomainPackValidatorAppliesTo(DomainPackMetadataBaseModel):
    """Target selector for one validator binding."""

    domain_pack_id: Optional[str] = None
    object_types: list[str] = Field(default_factory=list)
    object_roles: list[str] = Field(default_factory=list)
    field_paths: list[str] = Field(default_factory=list)
    field_types: list[str] = Field(default_factory=list)

    @field_validator("domain_pack_id")
    @classmethod
    def _validate_domain_pack_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _validate_pack_id(value, "applies_to.domain_pack_id")

    @field_validator("object_types", "object_roles")
    @classmethod
    def _validate_symbolic_lists(cls, value: list[str], info) -> list[str]:
        validated = [
            _validate_symbolic_name(item, f"applies_to.{info.field_name}")
            for item in value
        ]
        _require_unique(validated, f"applies_to.{info.field_name}")
        return validated

    @field_validator("field_paths")
    @classmethod
    def _validate_field_paths(cls, value: list[str]) -> list[str]:
        validated = [validate_field_path_syntax(item) for item in value]
        _require_unique(validated, "applies_to.field_paths")
        return validated

    @field_validator("field_types")
    @classmethod
    def _validate_field_types(cls, value: list[str]) -> list[str]:
        validated: list[str] = []
        for item in value:
            try:
                validated.append(DomainPackFieldType(item).value)
            except ValueError as exc:
                raise ValueError(f"Unknown domain-pack field type '{item}'") from exc
        _require_unique(validated, "applies_to.field_types")
        return validated


class DomainPackValidatorCuratorOverride(DomainPackMetadataBaseModel):
    """Curator override policy for one active validator binding."""

    allowed: bool = False


class DomainPackInputSelector(DomainPackMetadataBaseModel):
    """Deterministic selector for one validator input value."""

    _ALLOWED_FIELDS_BY_SOURCE: ClassVar[dict[str, set[str]]] = {
        "payload": {
            "source",
            "path",
            "required",
            "allow_multiple",
            "context_only",
        },
        "envelope_metadata": {
            "source",
            "path",
            "required",
            "allow_multiple",
            "context_only",
        },
        "object_metadata": {
            "source",
            "path",
            "required",
            "allow_multiple",
            "context_only",
        },
        "evidence_record": {
            "source",
            "path",
            "field_path",
            "record_id",
            "output",
            "required",
            "allow_multiple",
            "context_only",
        },
        "object_ref": {
            "source",
            "path",
            "field_path",
            "object_type",
            "required",
            "allow_multiple",
            "context_only",
        },
        "literal": {
            "source",
            "value",
            "required",
            "allow_multiple",
            "context_only",
        },
        # Value-dependent literal: reads a sibling field value at ``path`` and maps it
        # through ``key_map`` to produce a fixed literal input. Generic mechanism for a
        # subset (or any input) that depends on a sibling field's staged value — e.g. the
        # disease relation CV subset selected by the staged subject_type. Stays entirely in
        # binding config; no domain names in code.
        "payload_keyed_literal": {
            "source",
            "path",
            "key_map",
            "required",
            "allow_multiple",
            "context_only",
        },
    }

    source: Literal[
        "payload",
        "envelope_metadata",
        "object_metadata",
        "evidence_record",
        "object_ref",
        "literal",
        "payload_keyed_literal",
    ]
    path: Optional[str] = None
    field_path: Optional[str] = None
    object_type: Optional[str] = None
    record_id: Optional[str] = None
    output: Literal["value", "quote_bundle"] = Field(
        default="value",
        exclude_if=lambda value: value == "value",
    )
    value: Any = None
    key_map: Optional[Dict[str, Any]] = None
    required: bool = True
    allow_multiple: Optional[bool] = None
    context_only: bool = Field(default=False, exclude_if=lambda value: value is False)

    @field_validator("path", "field_path")
    @classmethod
    def _validate_optional_paths(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return validate_field_path_syntax(value)

    @field_validator("object_type")
    @classmethod
    def _validate_object_type(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _validate_symbolic_name(value, "input_fields.object_type")

    @field_validator("record_id")
    @classmethod
    def _validate_record_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("input_fields.record_id must not be empty")
        if value != value.strip():
            raise ValueError(
                "input_fields.record_id must not include surrounding whitespace"
            )
        return value

    @model_validator(mode="after")
    def _validate_selector_shape(self) -> "DomainPackInputSelector":
        allowed_fields = self._ALLOWED_FIELDS_BY_SOURCE[self.source]
        invalid_fields = sorted(self.model_fields_set - allowed_fields)
        if invalid_fields:
            field_list = ", ".join(invalid_fields)
            raise ValueError(
                f"{self.source} selectors do not support field(s): {field_list}"
            )
        if (
            self.source
            in {
                "payload",
                "envelope_metadata",
                "object_metadata",
            }
            and self.path is None
        ):
            raise ValueError(f"{self.source} selectors must provide path")
        if (
            self.source == "evidence_record"
            and self.output == "value"
            and self.path is None
        ):
            raise ValueError("evidence_record value selectors must provide path")
        if self.source == "literal" and "value" not in self.model_fields_set:
            raise ValueError("literal selectors must provide value")
        if self.source == "payload_keyed_literal":
            if self.path is None:
                raise ValueError("payload_keyed_literal selectors must provide path")
            if not self.key_map:
                raise ValueError(
                    "payload_keyed_literal selectors must provide a non-empty key_map"
                )
        if (
            self.source == "object_ref"
            and self.field_path is None
            and self.object_type is None
        ):
            raise ValueError(
                "object_ref selectors must provide field_path or object_type"
            )
        return self


class DomainPackValidatorBatchConfig(DomainPackMetadataBaseModel):
    """Optional active-validator batch dispatch opt-in."""

    enabled: bool = False
    family: Optional[str] = None
    max_size: Optional[int] = Field(default=None, ge=1)

    @field_validator("family")
    @classmethod
    def _validate_family(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _validate_symbolic_name(value, "validator_bindings.batch.family")


class DomainPackActiveValidatorBinding(DomainPackMetadataBaseModel):
    """Executable package-scoped validator binding metadata."""

    binding_id: str
    display_name: Optional[str] = None
    description: str = ""
    validator_agent: DomainPackValidatorAgentRef
    applies_to: DomainPackValidatorAppliesTo
    input_fields: dict[str, DomainPackInputSelector] = Field(default_factory=dict)
    expected_result_fields: dict[str, Any] = Field(default_factory=dict)
    max_tool_calls: Optional[int] = Field(default=None, ge=0)
    required: bool = False
    blocking: bool = False
    allow_opt_out: bool = False
    batch: DomainPackValidatorBatchConfig = Field(
        default_factory=DomainPackValidatorBatchConfig
    )
    curator_override: DomainPackValidatorCuratorOverride = Field(
        default_factory=DomainPackValidatorCuratorOverride
    )
    definition_state: DefinitionState = DefinitionState.STABLE

    @field_validator("binding_id")
    @classmethod
    def _validate_binding_id(cls, value: str) -> str:
        return _validate_symbolic_name(value, "validator_bindings.binding_id")

    @model_validator(mode="after")
    def validate_blocking_policy(self) -> "DomainPackActiveValidatorBinding":
        """Require blocking validator policy to also be required."""

        if self.blocking and not self.required:
            raise ValueError(
                "validator_bindings.active entries cannot set blocking: true "
                "unless required: true"
            )
        return self


class DomainPackUnderDevelopmentValidatorBinding(DomainPackMetadataBaseModel):
    """Informational validator capability that must not affect runtime policy."""

    binding_id: str
    display_name: str = Field(min_length=1)
    description: str = ""
    state_explanation: str = Field(min_length=1)
    validator_agent: Optional[DomainPackValidatorAgentRef] = None
    applies_to: Optional[DomainPackValidatorAppliesTo] = None
    input_fields: dict[str, DomainPackInputSelector] = Field(default_factory=dict)
    expected_result_fields: dict[str, Any] = Field(default_factory=dict)
    max_tool_calls: Optional[int] = Field(default=None, ge=0)
    definition_state: DefinitionState = DefinitionState.IN_DEVELOPMENT

    @field_validator("binding_id")
    @classmethod
    def _validate_binding_id(cls, value: str) -> str:
        return _validate_symbolic_name(value, "validator_bindings.binding_id")

    @field_validator("display_name")
    @classmethod
    def _validate_display_name(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("display_name must not include surrounding whitespace")
        return value

    @field_validator("state_explanation")
    @classmethod
    def _validate_state_explanation(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError(
                "state_explanation must not include surrounding whitespace"
            )
        return value


class DomainPackValidatorBindings(DomainPackMetadataBaseModel):
    """Target validator-binding bucket contract."""

    active: list[DomainPackActiveValidatorBinding] = Field(default_factory=list)
    under_development: list[DomainPackUnderDevelopmentValidatorBinding] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def _validate_unique_binding_ids(self) -> "DomainPackValidatorBindings":
        _require_unique(
            [item.binding_id for item in [*self.active, *self.under_development]],
            "validator_bindings",
        )
        return self


def _validate_metadata_mapping(value: dict[str, Any]) -> dict[str, Any]:
    raw_bindings = value.get("validator_bindings")
    if raw_bindings is None:
        return value
    validated_bindings = DomainPackValidatorBindings.model_validate(raw_bindings)
    return {
        **value,
        "validator_bindings": validated_bindings.model_dump(
            mode="json",
            exclude_none=True,
        ),
    }


class DomainPackFieldType(str, Enum):
    """Provider-neutral value types for declared object fields."""

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    OBJECT = "object"
    ARRAY = "array"
    ENUM = "enum"
    OBJECT_REF = "object_ref"
    FIELD_REF = "field_ref"
    ANY = "any"


class DomainPackEnumValue(DomainPackMetadataBaseModel):
    """One allowed value in a domain-pack enum."""

    value: str = Field(min_length=1)
    label: Optional[str] = None
    description: str = ""

    @field_validator("value", "label")
    @classmethod
    def _validate_non_empty_optional(cls, value: Optional[str], info) -> Optional[str]:
        if value is None:
            return None
        if not value.strip():
            raise ValueError(f"{info.field_name} must not be empty")
        if value != value.strip():
            raise ValueError(
                f"{info.field_name} must not include surrounding whitespace"
            )
        return value


class DomainPackEnumDefinition(DomainPackMetadataBaseModel):
    """Provider-neutral enum definition exposed by a domain pack."""

    enum_id: str
    display_name: str = Field(min_length=1)
    description: str = ""
    values: list[DomainPackEnumValue] = Field(min_length=1)
    definition_state: DefinitionState = DefinitionState.STABLE
    definition_notes: list[str] = Field(default_factory=list)

    @field_validator("enum_id")
    @classmethod
    def _validate_enum_id(cls, value: str) -> str:
        return _validate_symbolic_name(value, "enum_id")

    @model_validator(mode="after")
    def _validate_values(self) -> "DomainPackEnumDefinition":
        _require_unique(
            [item.value for item in self.values], f"enum {self.enum_id} values"
        )
        return self


class DomainPackModelDefinition(DomainPackMetadataBaseModel):
    """Provider-neutral model/contract definition exposed by a domain pack."""

    model_id: str
    display_name: str = Field(min_length=1)
    description: str = ""
    schema_ref: Optional[SchemaRef] = None
    definition_state: DefinitionState = DefinitionState.STABLE
    definition_notes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("model_id")
    @classmethod
    def _validate_model_id(cls, value: str) -> str:
        return _validate_symbolic_name(value, "model_id")

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata_mapping(value)


class DomainPackFieldDefinition(DomainPackMetadataBaseModel):
    """Field declaration for a provider-neutral curatable object type."""

    field_path: str
    field_type: DomainPackFieldType = DomainPackFieldType.ANY
    display_name: Optional[str] = None
    description: str = ""
    required: bool = False
    enum_ref: Optional[str] = Field(
        default=None,
        description="enum_id for enum-valued fields",
    )
    model_ref: Optional[str] = Field(
        default=None,
        description="model_id for nested object payload contracts",
    )
    object_type_ref: Optional[str] = Field(
        default=None,
        description="object_type targeted by object_ref or field_ref fields",
    )
    definition_state: DefinitionState = DefinitionState.STABLE
    definition_notes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("field_path")
    @classmethod
    def _validate_field_path(cls, value: str) -> str:
        return validate_field_path_syntax(value)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata_mapping(value)

    @field_validator("enum_ref", "model_ref", "object_type_ref")
    @classmethod
    def _validate_optional_refs(cls, value: Optional[str], info) -> Optional[str]:
        if value is None:
            return None
        return _validate_symbolic_name(value, info.field_name)

    @property
    def multivalued(self) -> bool:
        """Whether this field declares per-element validation fan-out.

        A ``multivalued: true`` field holds a list whose every present element is
        validated/materialized at ``field[i]`` (rather than only the legacy ``field[0]``
        slot). The flag lives in the freeform field ``metadata`` mapping so packs opt in
        per field; the validation engine reads it to fan a single binding match out into
        one match per list element.
        """

        return bool(self.metadata.get("multivalued"))

    @model_validator(mode="after")
    def _validate_ref_shape(self) -> "DomainPackFieldDefinition":
        raw_multivalued = self.metadata.get("multivalued")
        if raw_multivalued is not None:
            if not isinstance(raw_multivalued, bool):
                raise ValueError("field metadata 'multivalued' must be a boolean")
            if raw_multivalued and any(
                isinstance(part, int) for part in parse_field_path(self.field_path)
            ):
                raise ValueError(
                    "multivalued fields must declare a bare field_path without a list "
                    f"index; got {self.field_path!r}"
                )
        if self.field_type is DomainPackFieldType.ENUM and self.enum_ref is None:
            raise ValueError("enum fields must provide enum_ref")
        if (
            self.field_type is not DomainPackFieldType.ENUM
            and self.enum_ref is not None
        ):
            raise ValueError("enum_ref is only valid for enum fields")
        if (
            self.field_type
            not in {DomainPackFieldType.OBJECT, DomainPackFieldType.ARRAY}
            and self.model_ref is not None
        ):
            raise ValueError("model_ref is only valid for object or array fields")
        if (
            self.field_type
            not in {DomainPackFieldType.OBJECT_REF, DomainPackFieldType.FIELD_REF}
            and self.object_type_ref is not None
        ):
            raise ValueError(
                "object_type_ref is only valid for object_ref or field_ref fields"
            )
        return self


class DomainPackObjectDefinition(DomainPackMetadataBaseModel):
    """Curatable object type declared by a domain pack."""

    object_type: str
    display_name: str = Field(min_length=1)
    description: str = ""
    model_ref: Optional[str] = None
    schema_ref: Optional[SchemaRef] = None
    fields: list[DomainPackFieldDefinition] = Field(default_factory=list)
    definition_state: DefinitionState = DefinitionState.STABLE
    definition_notes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("object_type", "model_ref")
    @classmethod
    def _validate_optional_refs(cls, value: Optional[str], info) -> Optional[str]:
        if value is None:
            return None
        return _validate_symbolic_name(value, info.field_name)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata_mapping(value)

    @model_validator(mode="after")
    def _validate_unique_fields(self) -> "DomainPackObjectDefinition":
        _require_unique(
            [field.field_path for field in self.fields],
            f"object {self.object_type} fields",
        )
        return self


class DomainPackFixturePackRef(DomainPackMetadataBaseModel):
    """Metadata for a provider-neutral fixture pack bundled with a domain pack."""

    fixture_pack_id: str
    display_name: str = Field(min_length=1)
    path: str = Field(
        description="Path to fixture-pack YAML relative to domain pack root"
    )
    description: str = ""
    object_types: list[str] = Field(default_factory=list)
    definition_state: DefinitionState = DefinitionState.STABLE
    definition_notes: list[str] = Field(default_factory=list)

    @field_validator("fixture_pack_id")
    @classmethod
    def _validate_fixture_pack_id(cls, value: str) -> str:
        return _validate_symbolic_name(value, "fixture_pack_id")

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return _validate_relative_pack_path(value, "fixture_packs.path")

    @field_validator("object_types")
    @classmethod
    def _validate_object_types(cls, value: list[str]) -> list[str]:
        validated = [
            _validate_symbolic_name(item, "fixture_packs.object_types")
            for item in value
        ]
        _require_unique(validated, "fixture_packs.object_types")
        return validated


class DomainPackMetadata(DomainPackMetadataBaseModel):
    """Top-level metadata contract for a provider-neutral domain pack."""

    pack_id: str
    display_name: str = Field(min_length=1)
    version: str
    metadata_api_version: str
    description: str = ""
    status: DomainPackStatus = DomainPackStatus.ACTIVE
    schema_refs: list[SchemaRef] = Field(default_factory=list)
    enum_definitions: list[DomainPackEnumDefinition] = Field(default_factory=list)
    model_definitions: list[DomainPackModelDefinition] = Field(default_factory=list)
    object_definitions: list[DomainPackObjectDefinition] = Field(default_factory=list)
    fixture_packs: list[DomainPackFixturePackRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("pack_id")
    @classmethod
    def _validate_pack_id(cls, value: str) -> str:
        return _validate_pack_id(value, "pack_id")

    @field_validator("version", "metadata_api_version")
    @classmethod
    def _validate_versions(cls, value: str, info) -> str:
        return _validate_semver(value, info.field_name)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata_mapping(value)

    @model_validator(mode="after")
    def _validate_references(self) -> "DomainPackMetadata":
        enum_ids = [item.enum_id for item in self.enum_definitions]
        model_ids = [item.model_id for item in self.model_definitions]
        object_types = [item.object_type for item in self.object_definitions]
        fixture_pack_ids = [item.fixture_pack_id for item in self.fixture_packs]

        _require_unique([item.schema_id for item in self.schema_refs], "schema_refs")
        _require_unique(enum_ids, "enum_definitions")
        _require_unique(model_ids, "model_definitions")
        _require_unique(object_types, "object_definitions")
        _require_unique(fixture_pack_ids, "fixture_packs")

        enum_id_set = set(enum_ids)
        model_id_set = set(model_ids)
        object_type_set = set(object_types)
        errors: list[str] = []

        for object_definition in self.object_definitions:
            if (
                object_definition.model_ref is not None
                and object_definition.model_ref not in model_id_set
            ):
                errors.append(
                    f"object_definitions.{object_definition.object_type}.model_ref "
                    f"references unknown model '{object_definition.model_ref}'"
                )

            for field_definition in object_definition.fields:
                field_location = (
                    f"object_definitions.{object_definition.object_type}."
                    f"fields.{field_definition.field_path}"
                )
                if (
                    field_definition.enum_ref is not None
                    and field_definition.enum_ref not in enum_id_set
                ):
                    errors.append(
                        f"{field_location}.enum_ref references unknown enum "
                        f"'{field_definition.enum_ref}'"
                    )
                if (
                    field_definition.model_ref is not None
                    and field_definition.model_ref not in model_id_set
                ):
                    errors.append(
                        f"{field_location}.model_ref references unknown model "
                        f"'{field_definition.model_ref}'"
                    )
                if (
                    field_definition.object_type_ref is not None
                    and field_definition.object_type_ref not in object_type_set
                ):
                    errors.append(
                        f"{field_location}.object_type_ref references unknown object_type "
                        f"'{field_definition.object_type_ref}'"
                    )

        for fixture_pack in self.fixture_packs:
            for object_type in fixture_pack.object_types:
                if object_type not in object_type_set:
                    errors.append(
                        f"fixture_packs.{fixture_pack.fixture_pack_id}.object_types "
                        f"references unknown object_type '{object_type}'"
                    )

        if errors:
            raise ValueError("; ".join(errors))
        return self


class DomainFixture(DomainPackMetadataBaseModel):
    """One named envelope fixture bundled in a fixture pack."""

    name: str
    description: str = ""
    envelope: DomainEnvelope

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _validate_symbolic_name(value, "fixtures.name")


class DomainFixturePack(DomainPackMetadataBaseModel):
    """Provider-neutral fixture pack with concrete domain envelope examples."""

    fixture_pack_id: str
    domain_pack_id: str
    fixtures_api_version: str
    display_name: str = Field(min_length=1)
    description: str = ""
    fixtures: list[DomainFixture] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("fixture_pack_id")
    @classmethod
    def _validate_fixture_pack_id(cls, value: str) -> str:
        return _validate_symbolic_name(value, "fixture_pack_id")

    @field_validator("domain_pack_id")
    @classmethod
    def _validate_domain_pack_id(cls, value: str) -> str:
        return _validate_pack_id(value, "domain_pack_id")

    @field_validator("fixtures_api_version")
    @classmethod
    def _validate_fixture_version(cls, value: str) -> str:
        return _validate_semver(value, "fixtures_api_version")

    @model_validator(mode="after")
    def _validate_fixture_envelopes(self) -> "DomainFixturePack":
        _require_unique([fixture.name for fixture in self.fixtures], "fixtures")

        errors: list[str] = []
        for fixture in self.fixtures:
            if fixture.envelope.domain_pack_id != self.domain_pack_id:
                errors.append(
                    f"fixtures.{fixture.name}.envelope.domain_pack_id "
                    f"'{fixture.envelope.domain_pack_id}' does not match fixture pack "
                    f"domain_pack_id '{self.domain_pack_id}'"
                )
        if errors:
            raise ValueError("; ".join(errors))
        return self


__all__ = [
    "DomainFixture",
    "DomainFixturePack",
    "DomainPackEnumDefinition",
    "DomainPackEnumValue",
    "DomainPackFieldDefinition",
    "DomainPackFieldType",
    "DomainPackFixturePackRef",
    "DomainPackMetadata",
    "DomainPackModelDefinition",
    "DomainPackObjectDefinition",
    "DomainPackStatus",
    "DomainPackActiveValidatorBinding",
    "DomainPackUnderDevelopmentValidatorBinding",
    "DomainPackValidatorAgentRef",
    "DomainPackValidatorAppliesTo",
    "DomainPackValidatorBindings",
    "DomainPackValidatorBatchConfig",
    "DomainPackValidatorCuratorOverride",
    "DomainPackInputSelector",
]
