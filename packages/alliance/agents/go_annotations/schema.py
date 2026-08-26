"""GO annotations lookup agent schema."""

from typing import Literal, Optional

from pydantic import Field, StrictBool, StrictStr

from src.schemas.domain_validator import (  # type: ignore[reportMissingImports]
    DomainValidatorBaseModel,
    DomainValidatorResultBase,
)


class GOAnnotationRelation(DomainValidatorBaseModel):
    """Relation asserted between the gene product and GO term."""

    id: StrictStr
    label: Optional[StrictStr] = None


class GOAnnotationProvenance(DomainValidatorBaseModel):
    """Source record identity retained for curator comparison."""

    source: StrictStr
    source_url: StrictStr
    source_record_id: StrictStr


class GOAnnotationResult(DomainValidatorBaseModel):
    """One typed gene-to-GO annotation returned by the lookup."""

    gene_product_id: StrictStr
    go_id: StrictStr
    go_name: Optional[StrictStr] = None
    aspect: Optional[Literal["MF", "BP", "CC"]] = None
    evidence_code: Optional[StrictStr] = None
    eco_id: Optional[StrictStr] = None
    evidence_label: Optional[StrictStr] = None
    references: list[StrictStr] = Field(default_factory=list)
    relation: Optional[GOAnnotationRelation] = None
    with_from: list[StrictStr] = Field(default_factory=list)
    qualifiers: list[StrictStr] = Field(default_factory=list)
    negated: StrictBool = False
    providers: list[StrictStr] = Field(default_factory=list)
    product_type: Optional[StrictStr] = None
    provenance: GOAnnotationProvenance


class GOAnnotationsResult(DomainValidatorResultBase):
    """Canonical result schema for Alliance GO annotation validator agents."""

    __envelope_class__ = True

    gene_id: Optional[str] = Field(
        default=None,
        description="Gene CURIE that was queried",
    )
    gene_symbol: Optional[str] = Field(default=None, description="Gene symbol")
    annotations: list[GOAnnotationResult] = Field(
        default_factory=list,
        description="GO annotations returned for the queried gene",
    )
    source: Optional[StrictStr] = Field(default=None, description="Lookup source")
    source_url: Optional[StrictStr] = Field(
        default=None,
        description="Exact source endpoint used for the lookup",
    )
