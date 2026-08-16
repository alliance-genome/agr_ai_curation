"""Shared contracts for package-owned domain validator agent results."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
)

DomainValidatorStatus = Literal["resolved", "unresolved"]

_PYDANTIC_DECORATOR_GROUPS = (
    "validators",
    "field_validators",
    "root_validators",
    "field_serializers",
    "model_serializers",
    "model_validators",
    "computed_fields",
)

_PYDANTIC_MODEL_BEHAVIOR_METHODS = (
    "__get_pydantic_core_schema__",
    "__get_pydantic_json_schema__",
    "model_dump",
    "model_dump_json",
    "model_json_schema",
    "model_validate",
    "model_validate_json",
    "model_validate_strings",
)


class DomainValidatorBaseModel(BaseModel):
    """Strict base model for validator result contracts."""

    model_config = ConfigDict(extra="forbid")


class ValidatorOutputProjection(DomainValidatorBaseModel):
    """Package-owned row projection contract for typed validator results."""

    row_list_field: StrictStr = Field(
        description="Result field containing the canonical projected rows"
    )
    identity_fields: tuple[StrictStr, ...] = Field(
        min_length=1,
        description="Ordered row fields used to derive stable object identities",
    )
    label_fields: tuple[StrictStr, ...] = Field(
        default=(),
        description="Ordered row or inherited fields used for display labels",
    )
    inherited_parent_fields: tuple[StrictStr, ...] = Field(
        default=(),
        description="Top-level result fields copied into each projected row",
    )

    @field_validator("row_list_field")
    @classmethod
    def _validate_row_list_field(cls, value: str) -> str:
        if not value or value != value.strip() or not value.isidentifier():
            raise ValueError("row_list_field must be a non-empty field name")
        return value

    @field_validator("identity_fields", "label_fields", "inherited_parent_fields")
    @classmethod
    def _validate_projection_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            not field or field != field.strip() or not field.isidentifier()
            for field in value
        ):
            raise ValueError("projection entries must be non-empty field names")
        if len(set(value)) != len(value):
            raise ValueError("projection entries must be unique")
        return value


class ValidatorAgentRef(DomainValidatorBaseModel):
    """Package-scoped validator agent identity."""

    package_id: StrictStr = Field(
        description="Owning package ID for the validator agent"
    )
    agent_id: StrictStr = Field(description="Package-local validator agent ID")


class ValidationTarget(DomainValidatorBaseModel):
    """Domain-envelope target inspected by a validator binding."""

    domain_pack_id: StrictStr = Field(description="Domain pack that owns the target")
    object_type: Optional[StrictStr] = Field(
        default=None, description="Target object type"
    )
    object_id: Optional[StrictStr] = Field(default=None, description="Target object ID")
    object_role: Optional[StrictStr] = Field(
        default=None, description="Target object role"
    )
    field_path: Optional[StrictStr] = Field(
        default=None, description="Target field path"
    )
    expected_fields: list[StrictStr] = Field(
        default_factory=list,
        description="Result fields the binding expected the validator to resolve",
    )
    input_values: dict[str, Any] = Field(
        default_factory=dict,
        description="Binding input values supplied to the validator",
    )


class DomainValidationRequest(DomainValidatorBaseModel):
    """Dispatcher request built from one domain-pack validator binding match."""

    request_id: StrictStr = Field(description="Stable request identity")
    validator_binding_id: StrictStr = Field(
        description="Domain-pack validator binding ID"
    )
    validator_agent: ValidatorAgentRef = Field(description="Agent that should validate")
    target: ValidationTarget = Field(
        description="Domain-envelope target being validated"
    )
    selected_inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="Resolved scalar selector values keyed by binding input name",
    )
    input_selectors: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Selector declarations that produced selected_inputs",
    )
    evidence: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Evidence records deterministically attached to the target",
    )
    expected_result_fields: dict[str, Any] = Field(
        default_factory=dict,
        description="Domain-pack result fields expected from the validator",
    )


class ValidatorCandidate(DomainValidatorBaseModel):
    """One candidate surfaced during validator lookup or disambiguation."""

    value: StrictStr = Field(description="Candidate identifier or canonical value")
    label: Optional[StrictStr] = Field(default=None, description="Curator-facing label")
    object_type: Optional[StrictStr] = Field(
        default=None, description="Candidate object type"
    )
    score: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="Confidence score"
    )
    matched_fields: dict[str, Any] = Field(
        default_factory=dict,
        description="Fields from the candidate that matched the target",
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-owned candidate diagnostics",
    )


class ValidatorLookupAttempt(DomainValidatorBaseModel):
    """One lookup attempted while resolving a validator target."""

    provider: StrictStr = Field(description="Lookup provider or data source")
    method: StrictStr = Field(description="Lookup method or endpoint")
    query: dict[str, Any] = Field(description="Lookup query payload")
    result_count: int = Field(
        default=0, ge=0, description="Number of returned candidates"
    )
    outcome: Literal[
        "success",
        "not_found",
        "ambiguous",
        "conflict",
        "blocked",
        "error",
    ] = Field(description="Outcome for this lookup attempt")
    message: Optional[StrictStr] = Field(
        default=None,
        description="Short curator- or developer-facing lookup note",
    )


class DomainValidatorResultBase(DomainValidatorBaseModel):
    """Dispatcher-required base shape for agent-backed domain validators."""

    status: DomainValidatorStatus = Field(
        description="Validator decision for the target; active validators only return resolved or unresolved",
    )
    request_id: StrictStr = Field(description="Validator request identity")
    validator_binding_id: StrictStr = Field(
        description="Domain-pack validator binding ID"
    )
    validator_agent: ValidatorAgentRef = Field(
        description="Agent that produced this result"
    )
    target: ValidationTarget = Field(
        description="Domain-envelope target being validated"
    )
    resolved_values: dict[str, Any] = Field(
        description="Resolved scalar values keyed by binding expected-result field",
    )
    resolved_objects: list[dict[str, Any]] = Field(
        description="Resolved provider objects or facts returned by the validator",
    )
    missing_expected_fields: list[StrictStr] = Field(
        description="Expected result fields that could not be resolved",
    )
    candidates: list[ValidatorCandidate] = Field(
        description="Ambiguous or alternate candidates considered by the validator",
    )
    lookup_attempts: list[ValidatorLookupAttempt] = Field(
        description="Lookup attempts performed while resolving the target",
    )
    curator_message: Optional[StrictStr] = Field(
        description="Concise curator-facing result message",
    )
    explanation: StrictStr = Field(
        description="Validator reasoning and decision explanation"
    )

    @field_validator("status", mode="before")
    @classmethod
    def _reject_metadata_only_statuses(cls, value: object) -> object:
        if value == "under_development":
            raise ValueError(
                "under_development is metadata-only and is not a validator result status"
            )
        return value


def is_domain_validator_result_schema(schema: object) -> bool:
    """Return whether ``schema`` only adds fields to the shared result contract."""

    if not isinstance(schema, type) or not issubclass(
        schema, DomainValidatorResultBase
    ):
        return False

    preserves_fields = all(
        schema.model_fields[field_name].asdict() == base_field.asdict()
        for field_name, base_field in DomainValidatorResultBase.model_fields.items()
    )
    return preserves_fields and _preserves_domain_validator_model_behavior(schema)


def _preserves_domain_validator_model_behavior(
    schema: type[DomainValidatorResultBase],
) -> bool:
    """Reject subclass hooks that can rewrite canonical values or schemas."""

    if schema.model_config != DomainValidatorResultBase.model_config:
        return False
    if schema.__pydantic_custom_init__:
        return False
    if (
        schema.__pydantic_post_init__
        != DomainValidatorResultBase.__pydantic_post_init__
    ):
        return False

    for method_name in _PYDANTIC_MODEL_BEHAVIOR_METHODS:
        if _callable_identity(getattr(schema, method_name)) is not _callable_identity(
            getattr(DomainValidatorResultBase, method_name)
        ):
            return False

    schema_decorators = schema.__pydantic_decorators__
    base_decorators = DomainValidatorResultBase.__pydantic_decorators__
    for group_name in _PYDANTIC_DECORATOR_GROUPS:
        schema_group = getattr(schema_decorators, group_name)
        base_group = getattr(base_decorators, group_name)
        if schema_group.keys() != base_group.keys():
            return False
        for decorator_name, base_decorator in base_group.items():
            schema_decorator = schema_group[decorator_name]
            if schema_decorator.info != base_decorator.info:
                return False
            if _callable_identity(schema_decorator.func) is not _callable_identity(
                base_decorator.func
            ):
                return False

    return True


def _callable_identity(value: object) -> object:
    """Return the underlying function for a possibly bound method."""

    return getattr(value, "__func__", value)
