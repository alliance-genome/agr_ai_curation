"""GO annotations lookup agent schema."""

from typing import Any, Optional

from pydantic import Field

from src.schemas.domain_validator import DomainValidatorResultBase


class GOAnnotationsResult(DomainValidatorResultBase):
    """Canonical result schema for Alliance GO annotation validator agents."""

    __envelope_class__ = True

    gene_id: Optional[str] = Field(
        default=None,
        description="Gene CURIE that was queried",
    )
    gene_symbol: Optional[str] = Field(default=None, description="Gene symbol")
    annotations: list[dict[str, Any]] = Field(
        default_factory=list,
        description="GO annotations returned for the queried gene",
    )
    manual_count: int = Field(default=0, ge=0, description="Manual annotation count")
    automatic_count: int = Field(
        default=0,
        ge=0,
        description="Automatic or electronic annotation count",
    )
