"""Affected genomic model validation agent schema."""

from typing import Any, Optional

from pydantic import Field, StrictStr

from src.schemas.domain_validator import (
    DomainValidatorBaseModel,
    DomainValidatorResultBase,
)


class AgmCandidateDetail(DomainValidatorBaseModel):
    """Alliance affected genomic model candidate facts."""

    agm_id: StrictStr = Field(
        description="Affected genomic model CURIE or primary external ID"
    )
    label: Optional[StrictStr] = Field(
        default=None, description="AGM label or symbol returned by lookup"
    )
    taxon: Optional[StrictStr] = Field(
        default=None, description="NCBITaxon CURIE returned by lookup"
    )
    data_provider: Optional[StrictStr] = Field(
        default=None, description="Alliance data provider code when returned"
    )
    match_type: Optional[StrictStr] = Field(
        default=None,
        description="Lookup match type, such as curie, exact, synonym, or partial",
    )
    matched_fields: dict[str, Any] = Field(
        default_factory=dict,
        description="Candidate fields that matched the validation request",
    )


class AgmValidationResult(DomainValidatorResultBase):
    """Canonical result schema for Alliance AGM validators."""

    __envelope_class__ = True

    agm_candidates: list[AgmCandidateDetail] = Field(
        default_factory=list,
        description="AGM-specific candidates considered or resolved by lookup",
    )
    unresolved_explanations: list[StrictStr] = Field(
        default_factory=list,
        description="Specific reasons the AGM subject could not be resolved",
    )
