"""Shared contracts for package-owned domain validator agent results."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StrictStr,
    field_validator,
    model_validator,
)

DomainValidatorStatus = Literal["resolved", "unresolved"]


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
    optional_fields: Optional[list[StrictStr]] = Field(
        default=None,
        description="Result fields to fill only when the lookup confirms them; never required",
    )
    input_values: dict[str, Any] = Field(
        default_factory=dict,
        description="Binding input values supplied to the validator",
    )


class DomainValidationRequest(DomainValidatorBaseModel):
    """Dispatcher request built from one domain-pack validator binding match."""

    validation_guidance: Optional[StrictStr] = Field(
        default=None,
        description="Short extractor-authored advisory context for this target, not evidence or a validation decision",
    )
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
    optional_result_fields: Optional[dict[str, Any]] = Field(
        default=None,
        description="Domain-pack result fields written only when the validator returns them",
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
    coverage: Optional[dict[str, Any]] = Field(
        default=None,
        description="Provider-returned discovery/display limits and truncation facts; copy from the lookup, never infer totals",
    )
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


class ValidatorFieldResolution(DomainValidatorBaseModel):
    """A composite validator's decision for one of the values it validated.

    Keyed in ``DomainValidatorResultBase.field_resolutions`` by an
    expected-result field or by the payload path of the resolvable value the
    decision covers (ALL-1299). ``lookup_outcome`` is a value of the shared
    ``LookupOutcome`` vocabulary: ``matched`` exactly when resolved.
    """

    status: DomainValidatorStatus = Field(description="Decision for this value")
    resolved_values: dict[str, Any] = Field(
        default_factory=dict,
        description="Resolved values for this value, keyed by binding expected-result field",
    )
    lookup_outcome: StrictStr = Field(description="Lookup outcome for this value")
    explanation: Optional[StrictStr] = Field(
        default=None, description="Validator explanation for this value",
    )
    curator_message: Optional[StrictStr] = Field(
        default=None, description="Curator-facing message for this value",
    )

    @model_validator(mode="after")
    def _validate_outcome(self) -> "ValidatorFieldResolution":
        from src.lib.domain_packs.resolvable_values import (
            LOOKUP_OUTCOMES,
            OUTCOME_MATCHED,
            STORED_UNRESOLVED_OUTCOMES,
        )

        if self.lookup_outcome not in LOOKUP_OUTCOMES:
            raise ValueError(
                f"lookup_outcome must be one of {LOOKUP_OUTCOMES}, got {self.lookup_outcome!r}"
            )
        if self.status == "resolved" and self.lookup_outcome != OUTCOME_MATCHED:
            raise ValueError("a resolved field resolution has lookup_outcome 'matched'")
        if self.status == "unresolved":
            if self.lookup_outcome not in STORED_UNRESOLVED_OUTCOMES:
                raise ValueError(
                    "an unresolved field resolution has a lookup_outcome in "
                    f"{STORED_UNRESOLVED_OUTCOMES}, got {self.lookup_outcome!r}"
                )
            if self.resolved_values:
                raise ValueError("an unresolved field resolution carries no resolved_values")
        return self


class DomainValidatorResultBase(DomainValidatorBaseModel):
    """Dispatcher-required base shape for agent-backed domain validators."""

    # Set only by the program-owned compact assembler, never accepted from JSON.
    # Composite completeness concerns the values actually decided, not absent
    # components whose possible destination fields also appear in the binding.
    _assembled_field_completeness: bool = PrivateAttr(default=False)

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
    field_resolutions: dict[str, ValidatorFieldResolution] = Field(
        default_factory=dict,
        description=(
            "Per-value decisions of a composite validator, keyed by expected-result "
            "field or resolvable-value payload path; values not listed are not written"
        ),
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
    """Return whether ``schema`` inherits from ``DomainValidatorResultBase``."""

    return (
        isinstance(schema, type)
        and issubclass(schema, BaseModel)
        and issubclass(schema, DomainValidatorResultBase)
    )
