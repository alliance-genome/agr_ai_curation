"""Gene validation agent schema."""

from typing import Any, Optional

from pydantic import Field, StrictStr

from src.schemas.domain_validator import (
    DomainValidatorBaseModel,
    DomainValidatorResultBase,
)


class GeneCandidateDetail(DomainValidatorBaseModel):
    """Alliance gene candidate facts preserved alongside generic candidates."""

    gene_id: StrictStr = Field(
        description="Gene CURIE in Alliance format, such as WB:WBGene00001234"
    )
    symbol: StrictStr = Field(description="Gene symbol returned by the lookup")
    species: Optional[StrictStr] = Field(
        default=None, description="Full species name returned by the lookup"
    )
    data_provider: Optional[StrictStr] = Field(
        default=None, description="Alliance data provider code"
    )
    name: Optional[StrictStr] = Field(
        default=None, description="Full gene name returned by the lookup"
    )
    gene_type: Optional[StrictStr] = Field(
        default=None, description="Gene type returned by the lookup"
    )
    genomic_location: Optional[dict[str, Any]] = Field(
        default=None, description="Genomic location facts returned by the lookup"
    )
    cross_references: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Cross-references returned by the lookup",
    )
    synonyms: list[StrictStr] = Field(
        default_factory=list,
        description="Synonyms or alternate names returned by the lookup",
    )
    match_type: Optional[StrictStr] = Field(
        default=None, description="Lookup match type, such as exact, prefix, or synonym"
    )


class GeneResultEnvelope(DomainValidatorResultBase):
    """Canonical result schema for Alliance gene validator agents."""

    __envelope_class__ = True

    gene_candidates: list[GeneCandidateDetail] = Field(
        default_factory=list,
        description="Domain-specific gene candidates considered or resolved by lookup",
    )
