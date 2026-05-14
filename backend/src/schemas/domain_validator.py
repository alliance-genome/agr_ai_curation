"""Shared contracts for package-owned domain validator agent results."""

from __future__ import annotations

from typing import Any, Literal, Optional, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator


DomainValidatorStatus = Literal["resolved", "unresolved"]


class DomainValidatorBaseModel(BaseModel):
    """Strict base model for validator result contracts."""

    model_config = ConfigDict(extra="forbid")


class ValidatorAgentRef(DomainValidatorBaseModel):
    """Package-scoped validator agent identity."""

    package_id: StrictStr = Field(description="Owning package ID for the validator agent")
    agent_id: StrictStr = Field(description="Package-local validator agent ID")


class ValidationTarget(DomainValidatorBaseModel):
    """Domain-envelope target inspected by a validator binding."""

    domain_pack_id: StrictStr = Field(description="Domain pack that owns the target")
    object_type: Optional[StrictStr] = Field(default=None, description="Target object type")
    object_id: Optional[StrictStr] = Field(default=None, description="Target object ID")
    object_role: Optional[StrictStr] = Field(default=None, description="Target object role")
    field_path: Optional[StrictStr] = Field(default=None, description="Target field path")
    expected_fields: list[StrictStr] = Field(
        default_factory=list,
        description="Result fields the binding expected the validator to resolve",
    )
    input_values: dict[str, Any] = Field(
        default_factory=dict,
        description="Binding input values supplied to the validator",
    )


class ValidatorCandidate(DomainValidatorBaseModel):
    """One candidate surfaced during validator lookup or disambiguation."""

    value: StrictStr = Field(description="Candidate identifier or canonical value")
    label: Optional[StrictStr] = Field(default=None, description="Curator-facing label")
    object_type: Optional[StrictStr] = Field(default=None, description="Candidate object type")
    score: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Confidence score")
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
    query: dict[str, Any] = Field(default_factory=dict, description="Lookup query payload")
    result_count: int = Field(default=0, ge=0, description="Number of returned candidates")
    outcome: Literal["success", "not_found", "ambiguous", "conflict", "error"] = Field(
        description="Outcome for this lookup attempt",
    )
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
    validator_binding_id: StrictStr = Field(description="Domain-pack validator binding ID")
    validator_agent: ValidatorAgentRef = Field(description="Agent that produced this result")
    target: ValidationTarget = Field(description="Domain-envelope target being validated")
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
    explanation: StrictStr = Field(description="Validator reasoning and decision explanation")

    @field_validator("status", mode="before")
    @classmethod
    def _reject_metadata_only_statuses(cls, value: object) -> object:
        if value == "under_development":
            raise ValueError("under_development is metadata-only and is not a validator result status")
        return value


def is_domain_validator_result_schema(schema: object) -> bool:
    """Return whether ``schema`` inherits from or embeds ``DomainValidatorResultBase``."""

    if not isinstance(schema, type) or not issubclass(schema, BaseModel):
        return False
    if issubclass(schema, DomainValidatorResultBase):
        return True
    return any(
        _annotation_contains_domain_validator_base(field.annotation)
        for field in schema.model_fields.values()
    )


def _annotation_contains_domain_validator_base(annotation: object) -> bool:
    if isinstance(annotation, type):
        try:
            return issubclass(annotation, DomainValidatorResultBase)
        except TypeError:
            return False

    origin = get_origin(annotation)
    if origin is not None and _annotation_contains_domain_validator_base(origin):
        return True

    return any(_annotation_contains_domain_validator_base(arg) for arg in get_args(annotation))
