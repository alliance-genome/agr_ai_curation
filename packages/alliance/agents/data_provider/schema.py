"""Data provider validation agent schema."""

from typing import Any, Optional

from pydantic import Field, StrictBool, StrictStr

from src.schemas.domain_validator import (
    DomainValidatorBaseModel,
    DomainValidatorResultBase,
)


class DataProviderCandidateContext(DomainValidatorBaseModel):
    """Optional context explaining how a provider candidate was produced."""

    provider: Optional[StrictStr] = Field(
        default=None, description="Lookup provider or data source"
    )
    method: Optional[StrictStr] = Field(default=None, description="Lookup method")
    query: dict[str, Any] = Field(
        default_factory=dict, description="Lookup query payload for this candidate"
    )
    lookup_status: Optional[StrictStr] = Field(
        default=None, description="Lookup status associated with this candidate"
    )


class DataProviderCandidateDetail(DomainValidatorBaseModel):
    """Data provider candidate facts preserved alongside generic candidates."""

    abbreviation: StrictStr = Field(description="Alliance data provider abbreviation")
    taxon_id: StrictStr = Field(description="Provider taxon CURIE")
    display_name: Optional[StrictStr] = Field(
        default=None, description="Curator-facing provider display name"
    )
    species: Optional[StrictStr] = Field(
        default=None, description="Provider organism name when available"
    )
    match_type: Optional[StrictStr] = Field(
        default=None,
        description="Lookup match type, such as abbreviation, provider_name, or taxon_id",
    )
    matched_value: Optional[StrictStr] = Field(
        default=None, description="Input value or returned value that matched this candidate"
    )
    taxon_matches: Optional[StrictBool] = Field(
        default=None,
        description="Whether the supplied taxon matched this provider candidate",
    )
    mismatch_explanation: Optional[StrictStr] = Field(
        default=None,
        description="Curator-facing explanation for provider/taxon or name mismatch",
    )
    context: DataProviderCandidateContext = Field(
        default_factory=DataProviderCandidateContext,
        description="Structured binding and lookup context used for the candidate",
    )


class DataProviderValidationResult(DomainValidatorResultBase):
    """Canonical result schema for Alliance data provider validators."""

    __envelope_class__ = True

    data_provider_candidates: list[DataProviderCandidateDetail] = Field(
        default_factory=list,
        description="Data-provider-specific candidates considered or resolved by lookup",
    )
    mismatch_explanations: list[StrictStr] = Field(
        default_factory=list,
        description="Provider/taxon mismatch explanations surfaced for unresolved results",
    )
