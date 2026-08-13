"""GO annotations lookup agent schema."""

from typing import Optional

from pydantic import Field, StrictBool, StrictStr

from src.schemas.domain_validator import (  # type: ignore[reportMissingImports]
    DomainValidatorBaseModel,
    DomainValidatorResultBase,
)


class GOAnnotationResult(DomainValidatorBaseModel):
    """One typed gene-to-GO annotation returned by the lookup."""

    go_id: StrictStr
    go_name: Optional[StrictStr] = None
    aspect: Optional[StrictStr] = None
    evidence_code: Optional[StrictStr] = None
    evidence_label: Optional[StrictStr] = None
    assigned_by: Optional[StrictStr] = None
    is_manual: Optional[StrictBool] = None
    qualifier: Optional[list[StrictStr]] = None


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
    manual_count: int = Field(default=0, ge=0, description="Manual annotation count")
    automatic_count: int = Field(
        default=0,
        ge=0,
        description="Automatic or electronic annotation count",
    )
