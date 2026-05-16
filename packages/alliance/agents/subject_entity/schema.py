"""Typed subject entity validation router schema."""

from typing import Any, Optional

from pydantic import Field, StrictStr

from src.schemas.domain_validator import (
    DomainValidatorBaseModel,
    DomainValidatorResultBase,
    ValidatorAgentRef,
)


class SelectedSubjectValidator(DomainValidatorBaseModel):
    """Concrete validator path selected by the subject router."""

    subject_type: StrictStr = Field(description="Normalized subject type")
    validator_agent: ValidatorAgentRef = Field(
        description="Concrete package-scoped validator selected for this subject type"
    )
    tool_methods: list[StrictStr] = Field(
        default_factory=list,
        description="Alliance lookup methods used or expected for this route",
    )
    route_reason: StrictStr = Field(
        description="Plain-language explanation for the selected route"
    )


class SubjectEntityCandidateDetail(DomainValidatorBaseModel):
    """Candidate facts for gene, allele, or AGM subject validation."""

    subject_identifier: StrictStr = Field(
        description="Candidate normalized subject identifier"
    )
    subject_type: StrictStr = Field(description="Candidate normalized subject type")
    subject_label: Optional[StrictStr] = Field(
        default=None, description="Candidate subject label or symbol"
    )
    taxon: Optional[StrictStr] = Field(
        default=None, description="Candidate subject NCBITaxon CURIE"
    )
    data_provider: Optional[StrictStr] = Field(
        default=None, description="Alliance data provider code when returned"
    )
    match_type: Optional[StrictStr] = Field(
        default=None,
        description="Lookup match type, such as curie, exact, synonym, or partial",
    )
    selected_validator: Optional[SelectedSubjectValidator] = Field(
        default=None,
        description="Concrete validator route that produced this candidate",
    )
    matched_fields: dict[str, Any] = Field(
        default_factory=dict,
        description="Candidate fields that matched the validation request",
    )


class SubjectEntityValidationResult(DomainValidatorResultBase):
    """Canonical result schema for typed phenotype/disease subject validation."""

    __envelope_class__ = True

    normalized_subject_identifier: Optional[StrictStr] = Field(
        default=None, description="Resolved subject CURIE or primary external ID"
    )
    normalized_subject_type: Optional[StrictStr] = Field(
        default=None, description="Resolved subject type: gene, allele, or agm"
    )
    normalized_subject_label: Optional[StrictStr] = Field(
        default=None, description="Resolved subject label or symbol"
    )
    taxon: Optional[StrictStr] = Field(
        default=None, description="Resolved NCBITaxon CURIE"
    )
    selected_validator: Optional[SelectedSubjectValidator] = Field(
        default=None, description="Concrete validator path selected by subject type"
    )
    subject_candidates: list[SubjectEntityCandidateDetail] = Field(
        default_factory=list,
        description="Subject-specific candidates considered or resolved by routing",
    )
    unresolved_explanations: list[StrictStr] = Field(
        default_factory=list,
        description="Specific reasons the subject could not be resolved",
    )
