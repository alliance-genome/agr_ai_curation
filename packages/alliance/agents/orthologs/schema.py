"""Orthologs lookup agent schema."""

from typing import Any, Optional

from pydantic import Field

from src.schemas.domain_validator import DomainValidatorResultBase


class OrthologsResult(DomainValidatorResultBase):
    """Canonical result schema for Alliance orthology validator agents."""

    __envelope_class__ = True

    query_gene: Optional[dict[str, Any]] = Field(
        default=None,
        description="Gene that was queried for ortholog relationships",
    )
    orthologs: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Ortholog records with species and confidence details",
    )
    high_confidence_count: int = Field(
        default=0,
        ge=0,
        description="Number of high-confidence orthologs",
    )
    species_represented: list[str] = Field(
        default_factory=list,
        description="Species with returned orthologs",
    )
