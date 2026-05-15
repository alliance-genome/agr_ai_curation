"""Allele validation agent schema."""

from typing import Optional

from pydantic import Field, StrictStr

from src.schemas.domain_validator import (
    DomainValidatorBaseModel,
    DomainValidatorResultBase,
)


class AlleleFullnameAttribution(DomainValidatorBaseModel):
    """Heuristic attribution facts preserved from allele lookup results."""

    value: StrictStr = Field(description="Attribution value from the allele fullname")
    confidence: StrictStr = Field(description="Attribution confidence from the lookup")
    source: StrictStr = Field(description="Attribution source from the lookup")


class AlleleCandidateDetail(DomainValidatorBaseModel):
    """Alliance allele candidate facts preserved alongside generic candidates."""

    allele_id: StrictStr = Field(
        description="Allele CURIE in Alliance format, such as MGI:3689906"
    )
    symbol: StrictStr = Field(description="Allele symbol returned by the lookup")
    species: Optional[StrictStr] = Field(
        default=None, description="Full species name returned by the lookup"
    )
    data_provider: Optional[StrictStr] = Field(
        default=None, description="Alliance data provider code"
    )
    name: Optional[StrictStr] = Field(
        default=None, description="Full allele name returned by the lookup"
    )
    associated_gene: Optional[StrictStr] = Field(
        default=None, description="Associated gene symbol or ID returned by the lookup"
    )
    is_obsolete: Optional[bool] = Field(
        default=None, description="Whether the lookup marked this allele obsolete"
    )
    is_extinct: Optional[bool] = Field(
        default=None, description="Whether the lookup marked this allele extinct"
    )
    synonyms: list[StrictStr] = Field(
        default_factory=list,
        description="Synonyms or alternate names returned by the lookup",
    )
    fullname_attribution: Optional[AlleleFullnameAttribution] = Field(
        default=None,
        description="Heuristic creator or institution attribution from the lookup",
    )
    match_type: Optional[StrictStr] = Field(
        default=None, description="Lookup match type, such as exact, prefix, or synonym"
    )


class AlleleResultEnvelope(DomainValidatorResultBase):
    """Canonical result schema for Alliance allele validator agents."""

    __envelope_class__ = True

    allele_candidates: list[AlleleCandidateDetail] = Field(
        default_factory=list,
        description="Domain-specific allele candidates considered or resolved by lookup",
    )
