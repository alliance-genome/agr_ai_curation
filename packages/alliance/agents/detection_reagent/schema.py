"""Detection reagent validation agent schema."""

from typing import Any, Optional

from pydantic import Field, StrictStr

from src.schemas.domain_validator import (
    DomainValidatorBaseModel,
    DomainValidatorResultBase,
)


class DetectionReagentCandidateDetail(DomainValidatorBaseModel):
    """Curation-DB-backed construct or targeting-reagent candidate."""

    reagent_id: StrictStr = Field(
        description="Construct or sequence-targeting-reagent CURIE"
    )
    name: StrictStr = Field(description="Name or symbol returned by the lookup")
    entity_type: StrictStr = Field(
        description="Lookup entity type: construct or targeting reagent"
    )
    data_provider: Optional[StrictStr] = Field(
        default=None, description="Alliance provider abbreviation"
    )
    match_type: Optional[StrictStr] = Field(
        default=None, description="Lookup match type"
    )
    matched_fields: dict[str, Any] = Field(
        default_factory=dict,
        description="Candidate fields that matched the request",
    )


class DetectionReagentValidationResult(DomainValidatorResultBase):
    """Canonical result for one detection-reagent resolution request."""

    __envelope_class__ = True

    reagent_candidates: list[DetectionReagentCandidateDetail] = Field(
        default_factory=list,
        description="Construct and targeting-reagent candidates considered by lookup",
    )
    unresolved_explanations: list[StrictStr] = Field(
        default_factory=list,
        description="Specific reasons the reagent could not be resolved",
    )
