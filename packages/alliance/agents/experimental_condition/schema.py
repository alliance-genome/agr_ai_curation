"""Composite experimental condition validation agent schema."""

from typing import Any, Literal, Optional

from pydantic import Field, StrictBool, StrictStr

from src.schemas.domain_validator import (
    DomainValidatorBaseModel,
    DomainValidatorResultBase,
    ValidatorAgentRef,
    ValidatorCandidate,
    ValidatorLookupAttempt,
)


ComponentValidationStatus = Literal[
    "resolved",
    "unresolved",
    "not_present",
    "not_checked",
]


class ExperimentalConditionNormalizedComponent(DomainValidatorBaseModel):
    """One normalized condition component selected from lower-level evidence."""

    component_type: StrictStr = Field(
        description=(
            "Component category, such as condition_class, condition_id, "
            "condition_chemical, condition_taxon, relation, data_provider, "
            "quantity, unit, free_text, or evidence_quote"
        )
    )
    field_path: Optional[StrictStr] = Field(
        default=None, description="Payload field path for this component"
    )
    resolved_values: dict[str, Any] = Field(
        default_factory=dict,
        description="Scalar values resolved for this component",
    )
    resolved_objects: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Provider-backed facts selected for this component",
    )
    source_inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="Inputs from selected_inputs or target.input_values used for the component",
    )
    validator_agent: Optional[ValidatorAgentRef] = Field(
        default=None,
        description="Lower-level validator capability that owns this component's semantics",
    )


class ExperimentalConditionComponentValidation(DomainValidatorBaseModel):
    """Inspectable validation decision for one condition component."""

    component_type: StrictStr = Field(
        description="Component category validated within the experimental condition"
    )
    field_path: Optional[StrictStr] = Field(
        default=None, description="Payload field path for the component"
    )
    required: StrictBool = Field(
        default=False,
        description="Whether this component is required for the condition binding",
    )
    validator_agent: Optional[ValidatorAgentRef] = Field(
        default=None,
        description="Lower-level validator capability used or required for this component",
    )
    status: ComponentValidationStatus = Field(
        description="Component-level validation decision"
    )
    selected_inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="Selector values supplied to this component check",
    )
    resolved_values: dict[str, Any] = Field(
        default_factory=dict,
        description="Component-level resolved scalar values",
    )
    missing_expected_fields: list[StrictStr] = Field(
        default_factory=list,
        description="Component expected fields that remain unresolved",
    )
    candidates: list[ValidatorCandidate] = Field(
        default_factory=list,
        description="Ambiguous or alternate candidates for this component",
    )
    lookup_attempts: list[ValidatorLookupAttempt] = Field(
        default_factory=list,
        description="Lookup attempts used for this component",
    )
    curator_message: Optional[StrictStr] = Field(
        default=None,
        description="Curator-facing component decision summary",
    )
    explanation: StrictStr = Field(
        description="Plain-language component decision tied to lookup evidence"
    )


class ExperimentalConditionValidationResult(DomainValidatorResultBase):
    """Canonical result schema for composite experimental condition validators."""

    __envelope_class__ = True

    condition_status: Literal["resolved", "unresolved"] = Field(
        description="Condition-level decision after composing component validations"
    )
    condition_id: Optional[StrictStr] = Field(
        default=None,
        description="Resolved ExperimentalCondition identifier or stable condition key when available",
    )
    normalized_components: list[ExperimentalConditionNormalizedComponent] = Field(
        default_factory=list,
        description="Resolved condition components suitable for materializer use",
    )
    component_validations: list[ExperimentalConditionComponentValidation] = Field(
        default_factory=list,
        description="Per-component decisions, candidates, lookup attempts, and explanations",
    )
    unresolved_components: list[StrictStr] = Field(
        default_factory=list,
        description="Component types or field paths keeping the condition unresolved",
    )
